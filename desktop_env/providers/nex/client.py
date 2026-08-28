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


def _create_sandbox_once(timeout: int = None) -> str:
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


def _delete_sandbox_once(sandbox_id: str):
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
# ======================= quota guard (2026-08-22) =======================
# 并发下的配额死锁修复：
#   1) DELETE 是异步的，接口 33ms 就返回，pod 还在 Terminating，仍占 4C/8Gi。
#      旧实现发完就走，下一个 create 撞上 12 > 10 核 -> 422。现在等到真的释放才返回。
#   2) create 撞到 422/quota 时指数退避重试，而不是直接抛。
_DELETE_WAIT        = float(os.environ.get("NEX_DELETE_WAIT", "180"))
_DELETE_POLL        = float(os.environ.get("NEX_DELETE_POLL", "1.0"))
_DELETE_SETTLE      = float(os.environ.get("NEX_DELETE_SETTLE", "2.0"))
_CREATE_ATTEMPTS    = int(os.environ.get("NEX_CREATE_ATTEMPTS", "12"))
_CREATE_BACKOFF     = float(os.environ.get("NEX_CREATE_BACKOFF", "3.0"))
_CREATE_BACKOFF_MAX = float(os.environ.get("NEX_CREATE_BACKOFF_MAX", "30.0"))
_GONE_STATES = {"terminated", "deleted", "stopped", "failed", "error", "killed", ""}
def _is_gone(sandbox_id: str) -> bool:
    try:
        st = (get_state(sandbox_id) or "").strip().lower()
    except Exception as e:
        s = str(e).lower()
        if "404" in s or "not found" in s or "not exist" in s:
            return True
        raise
    return st in _GONE_STATES
def _wait_gone(sandbox_id: str, timeout: float = None) -> bool:
    timeout = _DELETE_WAIT if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _is_gone(sandbox_id):
                return True
        except Exception as e:
            logger.warning(f"[quota-guard] 查 {sandbox_id} 状态失败,当已释放: {str(e)[:120]}")
            return True
        time.sleep(_DELETE_POLL)
    logger.warning(f"[quota-guard] 等 {sandbox_id} 终止超时 ({timeout}s),继续")
    return False
def delete_sandbox(sandbox_id: str):
    """删除，并等到 pod 真的释放配额再返回。"""
    _delete_sandbox_once(sandbox_id)
    t0 = time.time()
    ok = _wait_gone(sandbox_id)
    time.sleep(_DELETE_SETTLE)  # 接口立刻 404,看不到 Terminating,只能静置
    logger.info(f"[quota-guard] {sandbox_id} 释放确认={ok} 耗时={time.time()-t0:.1f}s")
def _is_quota_error(e: Exception) -> bool:
    s = str(e).lower()
    return ("422" in s) or ("quota" in s)
def create_sandbox(timeout: int = None) -> str:
    """撞到配额 422 时退避重试，而不是把任务烧成 setup 失败。"""
    delay, last = _CREATE_BACKOFF, None
    for i in range(1, _CREATE_ATTEMPTS + 1):
        try:
            return _create_sandbox_once(timeout)
        except Exception as e:
            last = e
            if not _is_quota_error(e):
                raise
            logger.warning(f"[quota-guard] create 第 {i}/{_CREATE_ATTEMPTS} 次撞配额,{delay:.0f}s 后重试: {str(e)[:140]}")
            time.sleep(delay)
            delay = min(delay * 1.6, _CREATE_BACKOFF_MAX)
    raise RuntimeError(f"[quota-guard] create 连续 {_CREATE_ATTEMPTS} 次配额失败: {last}")
def list_sandboxes() -> list:
    d = _request("GET", _instances_base(), params={"page_size": 100})
    items = d.get("items") if isinstance(d, dict) else d
    return items or []
def sweep_orphans(keep=()) -> int:
    """开跑前清掉上一轮残留的沙箱。只在主进程启动时调,不要在 worker 里调。"""
    keep, n = set(keep or ()), 0
    try: items = list_sandboxes()
    except Exception as e:
        logger.warning(f"[quota-guard] 列沙箱失败,跳过清理: {e}"); return 0
    for it in items:
        sid = (it.get("sandbox_id") or it.get("id") or "") if isinstance(it, dict) else str(it)
        if not sid or sid in keep: continue
        try:
            _delete_sandbox_once(sid); n += 1; logger.warning(f"[quota-guard] 清理残留沙箱 {sid}")
        except Exception as e: logger.warning(f"[quota-guard] 清理 {sid} 失败: {e}")
    if n: time.sleep(_DELETE_SETTLE)
    return n
import fcntl as _fcntl
_LOCK_PATH = os.environ.get("NEX_CREATE_LOCK", "/tmp/nex_create.lock")
_create_sandbox_unlocked = create_sandbox
def create_sandbox(timeout: int = None) -> str:
    """跨进程串行化创建。
    两个 worker 同时 delete→create 时,两个正在终止的 pod 加两个新建
    瞬时需要 4 个槽位,而配额上限是 3 —— 退避重试也抢不过彼此。
    加锁让创建排队,同一时刻只有一个在抢槽位。"""
    t0 = time.time()
    with open(_LOCK_PATH, "a+") as fh:
        _fcntl.flock(fh, _fcntl.LOCK_EX)
        w = time.time() - t0
        if w > 1:
            logger.info("[quota-guard] 等创建锁 %.1fs", w)
        try:
            return _create_sandbox_unlocked(timeout)
        finally:
            _fcntl.flock(fh, _fcntl.LOCK_UN)
