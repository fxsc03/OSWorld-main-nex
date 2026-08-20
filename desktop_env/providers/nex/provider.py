"""
Nex provider：nex sandbox（KVM microVM + 容器镜像）本身即 OSWorld 环境，无嵌套虚拟化。

- path_to_vm = sandbox_id（对齐 aws provider 的 instance_id 语义）
- 实例是"一次性 Task"：无 stop/start，reset = 销毁 + 从模版重建（模版即 init_state
  快照），返回新 sandbox_id——DesktopEnv._revert_to_snapshot 已处理 id 变更
- 入方向走按端口分域名的 HTTP 网关；本地起 Host 改写代理（local_proxy.py），
  get_ip_address 返回 docker provider 同款 `localhost:server:chrome:vnc:vlc` 五元组
- renew 守护线程每 20min 续期 TTL,兼任"实例不被平台提前回收"的保险；实例超时
  自动销毁则兼任 watchdog(僵死回收)
"""

import logging
import threading
import time

from desktop_env.providers.base import Provider
from desktop_env.providers.nex import client as nex
from desktop_env.providers.nex.local_proxy import LocalProxy

logger = logging.getLogger("desktopenv.providers.nex.NexProvider")
logger.setLevel(logging.INFO)

# guest 内端口 → 五元组槽位（对齐 docker provider:8006 是 noVNC web 观察口）
GUEST_PORTS = {"server": 5000, "chromium": 9222, "vnc": 8006, "vlc": 8080}
RENEW_INTERVAL = 1200


class NexProvider(Provider):

    def __init__(self, region: str = None):
        super().__init__(region)
        self._proxies = {}        # sandbox_id -> {name: LocalProxy}
        self._renew_stop = {}     # sandbox_id -> threading.Event

    def start_emulator(self, path_to_vm: str, headless: bool, os_type: str = None, *args, **kwargs):
        sid = path_to_vm
        nex.wait_until_running(sid)
        server_ep = nex.get_endpoint(sid, GUEST_PORTS["server"])
        nex.wait_guest_ready(server_ep)

        if sid not in self._proxies:
            self._proxies[sid] = {
                name: LocalProxy(nex.get_endpoint(sid, port))
                for name, port in GUEST_PORTS.items()
            }
        if sid not in self._renew_stop:
            stop = threading.Event()
            self._renew_stop[sid] = stop
            threading.Thread(target=self._renew_loop, args=(sid, stop), daemon=True).start()
        logger.info(f"sandbox {sid} ready; local ports: "
                    f"{ {n: p.port for n, p in self._proxies[sid].items()} }")

    def _renew_loop(self, sid: str, stop: threading.Event):
        while not stop.wait(RENEW_INTERVAL):
            try:
                nex.renew(sid)
            except Exception as e:
                logger.warning(f"renew {sid} failed: {e}")
                return

    def get_ip_address(self, path_to_vm: str) -> str:
        p = self._proxies[path_to_vm]
        return (f"localhost:{p['server'].port}:{p['chromium'].port}"
                f":{p['vnc'].port}:{p['vlc'].port}")

    def save_state(self, path_to_vm: str, snapshot_name: str):
        # 模版即唯一快照;无 per-instance 快照能力
        logger.info("nex: save_state is a no-op (template is the snapshot)")

    def revert_to_snapshot(self, path_to_vm: str, snapshot_name: str) -> str:
        logger.info(f"recreating sandbox (old={path_to_vm}) from template")
        self._teardown(path_to_vm)
        new_id = nex.create_sandbox()
        self.start_emulator(new_id, headless=True)
        return new_id

    def stop_emulator(self, path_to_vm: str, region=None):
        self._teardown(path_to_vm)

    def _teardown(self, sid: str):
        stop = self._renew_stop.pop(sid, None)
        if stop:
            stop.set()
        for proxy in self._proxies.pop(sid, {}).values():
            proxy.close()
        try:
            nex.delete_sandbox(sid)
        except Exception as e:
            logger.warning(f"delete {sid} failed: {e}")
