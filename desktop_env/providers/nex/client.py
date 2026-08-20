"""
nex sandbox REST 客户端（同步,requests）——全仓唯一碰 nex API 的文件。

API 形态 2026-08-18 实测 + SDK(0.1.6) 源码核对：
  base   = http://nex.devops.xiaohongshu.com/api/v1
  鉴权   = header `x-api-key: ak-…`（Bearer 不认）
  create = POST /workspaces/{ws}/sandbox/instances        {"template_id":…,"timeout":…}
  get    = GET  /workspaces/{ws}/sandbox/instances/{id}   → data.status.state / expires_at
  ep     = GET  …/instances/{id}/endpoint?port=N          → data.endpoint（按端口分域名网关,80 口,免 token）
  renew  = POST …/instances/{id}/renew                    {"timeout": 秒}（从 now 起算）
  delete = DELETE …/instances/{id}
  ws 解析= GET /api-keys/current → data.workspace_id

必需环境变量：
  NEX_API_KEY       ak-xxx
  NEX_TEMPLATE      自定义模版名（如 osworld-guest）；或直接给 NEX_TEMPLATE_ID
可选：
  NEX_API_BASE      默认 http://nex.devops.xiaohongshu.com/api/v1
  NEX_WORKSPACE_ID  缺省从 API key 自动解析
  NEX_SANDBOX_TIMEOUT 实例 TTL 秒,默认 3600（episode 内由 renew 线程续期）
"""

import logging
import os
import time

import requests

logger = logging.getLogger("desktopenv.providers.nex.client")
logger.setLevel(logging.INFO)

API_BASE = os.environ.get("NEX_API_BASE", "http://nex.devops.xiaohongshu.com/api/v1")
SANDBOX_TIMEOUT = int(os.environ.get("NEX_SANDBOX_TIMEOUT", "3600"))
_TIMEOUT = 30

_workspace_id = os.environ.get("NEX_WORKSPACE_ID") or None
_template_id = os.environ.get("NEX_TEMPLATE_ID") or None


def _api_key() -> str:
    key = os.environ.get("NEX_API_KEY", "")
    assert key, "NEX_API_KEY 未设置"
    return key


def _request(method: str, path: str, **kwargs) -> dict:
    resp = requests.request(
        method, f"{API_BASE}{path}",
        headers={"x-api-key": _api_key()}, timeout=_TIMEOUT, **kwargs,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"nex API {method} {path} -> {resp.status_code}: {resp.text[:300]}")
    body = resp.json() if resp.content else {}
    code = body.get("code") if isinstance(body, dict) else None
    if code is not None and not (200 <= code < 300):
        raise RuntimeError(f"nex API {method} {path} -> code={code}: {body.get('message')}")
    return body.get("data", body) if isinstance(body, dict) else body


def workspace_id() -> str:
    global _workspace_id
    if not _workspace_id:
        data = _request("GET", "/api-keys/current")
        _workspace_id = (data.get("workspace") or {}).get("id") or data.get("workspace_id")
        assert _workspace_id, f"无法从 api-keys/current 解析 workspace_id: {data}"
        logger.info(f"resolved workspace_id={_workspace_id}")
    return _workspace_id


def _instances_base() -> str:
    return f"/workspaces/{workspace_id()}/sandbox/instances"


def template_id() -> str:
    global _template_id
    if not _template_id:
        name = os.environ.get("NEX_TEMPLATE", "")
        assert name, "NEX_TEMPLATE 或 NEX_TEMPLATE_ID 必须设置其一"
        data = _request("GET", f"/workspaces/{workspace_id()}/sandbox/templates")
        items = data if isinstance(data, list) else \
            data.get("templates") or data.get("items") or data.get("sandbox_templates") or []
        for t in items:
            if t.get("name") == name or t.get("template_name") == name:
                _template_id = t.get("template_id") or t.get("id")
                break
        assert _template_id, f"模版 '{name}' 不存在;现有: {[t.get('name') for t in items]}"
        logger.info(f"resolved template {name} -> {_template_id}")
    return _template_id


NETWORK_EGRESS = os.environ.get("NEX_EGRESS_ACTION", "allow")  # allow | deny


def create_sandbox(timeout: int = None) -> str:
    payload = {
        "template_id": template_id(),
        "timeout": timeout or SANDBOX_TIMEOUT,
        "network": {"policy": {"default_egress_action": NETWORK_EGRESS}},
    }
    data = _request("POST", _instances_base(), json=payload)
    sid = data.get("sandbox_id") or data.get("id")
    assert sid, f"create 响应无 id: {data}"
    logger.info(f"created sandbox {sid}")
    return sid


def get_info(sandbox_id: str) -> dict:
    return _request("GET", f"{_instances_base()}/{sandbox_id}")


def get_state(sandbox_id: str) -> str:
    return (get_info(sandbox_id).get("status") or {}).get("state", "")


def get_endpoint(sandbox_id: str, port: int) -> str:
    """返回该端口的网关域名(host,80 口),如 {id}-{port}-{ws}.sbx.devops.xiaohongshu.com"""
    data = _request("GET", f"{_instances_base()}/{sandbox_id}/endpoint", params={"port": port})
    ep = data.get("endpoint", "") if isinstance(data, dict) else str(data)
    assert ep, f"endpoint 响应为空: {data}"
    return ep.replace("http://", "").rstrip("/")


def renew(sandbox_id: str, duration: int = None):
    _request("POST", f"{_instances_base()}/{sandbox_id}/renew",
             json={"timeout": duration or SANDBOX_TIMEOUT})


def delete_sandbox(sandbox_id: str):
    try:
        _request("DELETE", f"{_instances_base()}/{sandbox_id}")
        logger.info(f"deleted sandbox {sandbox_id}")
    except RuntimeError as e:
        if "404" in str(e):
            logger.info(f"sandbox {sandbox_id} already gone")
        else:
            raise


def wait_until_running(sandbox_id: str, timeout: int = 600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = get_state(sandbox_id)
        if state.lower() == "running":
            return
        if state.lower() in ("terminated", "failed"):
            raise RuntimeError(f"sandbox {sandbox_id} entered state {state}")
        time.sleep(3)
    raise TimeoutError(f"sandbox {sandbox_id} not running within {timeout}s")


def wait_guest_ready(server_endpoint_host: str, timeout: int = 600, min_bytes: int = 200000):
    """真就绪信号:不只 server 响应 200,还要截图真有内容(桌面渲染完)。

    nex 冷启动 osworld server ~5s 就响应,但 GNOME 渲染要 ~35s——期间截图是 6.5KB
    空屏(纯色 PNG 压缩极小),渲染完跳到 ~1.1MB。min_bytes 阈值(默认 200KB)区分
    空屏 vs 真桌面,避免任务首帧观测撞到空屏。
    """
    deadline = time.time() + timeout
    url = f"http://{server_endpoint_host}/screenshot"
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) >= min_bytes:
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    raise TimeoutError(f"guest desktop not rendered within {timeout}s ({url}, min_bytes={min_bytes})")
