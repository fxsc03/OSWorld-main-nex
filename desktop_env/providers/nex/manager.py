"""
NexVMManager：为每个 DesktopEnv 进程分配一个 nex sandbox。

最简实现（对齐 docker manager 的无注册表风格）：get_vm_path 直接创建新
sandbox 返回其 id；生命周期由 provider 的 revert/stop 管理，进程崩溃遗留的
sandbox 依赖 nex 侧 TTL 或人工清理（infra 若有 TTL/标签 API，在 TODO 处接上,
96 并发 RL 下建议必接——对齐现物理机方案 watchdog 的自愈职责）。
"""

import logging
import os

from desktop_env.providers.base import VMManager
from desktop_env.providers.nex import client as nex

logger = logging.getLogger("desktopenv.providers.nex.NexVMManager")
logger.setLevel(logging.INFO)


class NexVMManager(VMManager):
    def __init__(self, registry_path=""):
        pass

    def initialize_registry(self, **kwargs):
        pass

    def add_vm(self, vm_path, region=None, **kwargs):
        pass

    def delete_vm(self, vm_path, region=None, **kwargs):
        pass

    def occupy_vm(self, vm_path, pid, region=None, **kwargs):
        pass

    def list_free_vms(self, **kwargs):
        return []

    def check_and_clean(self, **kwargs):
        # TODO(nex): 若 nex 支持按标签列 sandbox，扫描并回收本机 pid 已死的实例
        pass

    def get_vm_path(self, os_type="Ubuntu", region=None, screen_size=(1920, 1080), **kwargs):
        assert os_type == "Ubuntu", "nex 镜像目前只做了 Ubuntu"
        sandbox_id = nex.create_sandbox()
        logger.info(f"allocated sandbox {sandbox_id} (pid={os.getpid()})")
        return sandbox_id


if __name__ == "__main__":
    # 冒烟: python -m desktop_env.providers.nex.manager
    # 需要 NEX_API_KEY + NEX_TEMPLATE;起一个实例->等就绪->本地代理->截图->销毁
    import logging as _l
    import requests as _rq
    from desktop_env.providers.nex.provider import NexProvider

    _l.basicConfig(level=_l.INFO)
    sid = NexVMManager().get_vm_path()
    prov = NexProvider()
    try:
        prov.start_emulator(sid, headless=True)
        ip_ports = prov.get_ip_address(sid).split(":")
        url = f"http://{ip_ports[0]}:{ip_ports[1]}/screenshot"
        r = _rq.get(url, timeout=30)
        print(f"screenshot via {url}: http={r.status_code} bytes={len(r.content)}")
    finally:
        prov.stop_emulator(sid)
