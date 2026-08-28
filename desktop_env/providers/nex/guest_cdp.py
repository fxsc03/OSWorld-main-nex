"""在 guest 内部经 loopback 跑原生 CDP。
为什么不能从宿主机连:nex 网关转发时 Host 是 <sid>-9222-<ws>.sbx...,
Chrome DevTools 的 DNS-rebinding 防护只接受 IP 或 localhost,直接 500。
解法不是绕过校验,而是把调用挪进 guest —— 那里 Host 天生是 localhost:1337。
为什么不用 playwright:guest 里那份是坏的(sync_api 导不出 sync_playwright),
改用 CDP 的 REST(/json/list,/json/new,/json/close) + websockets 发 Runtime.evaluate,
只依赖 guest 自带的 urllib 与 websockets(2026-08-22 实测确认均存在)。"""
import json, logging, os, requests
logger = logging.getLogger("desktopenv.guest_cdp")
GUEST_CDP_URL = os.environ.get("OSWORLD_GUEST_CDP_URL", "http://localhost:1337")
MARK = "__CDP_RESULT__"
_TPL = r'''
import json, sys, time, subprocess, urllib.request
BASE = __URL__
CONFIG = json.loads(__CFG__)
RESULT = None
def _http(path, method="GET", timeout=20):
    req = urllib.request.Request(BASE + path, method=method)
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace").strip()
    if not raw or raw[0] not in "[{":
        return raw
    return json.loads(raw)
def ensure_chrome():
    last = None
    for _i in range(15):
        try:
            return _http("/json/version", timeout=5)
        except Exception as e:
            last = e
            subprocess.Popen(["google-chrome", "--remote-debugging-port=1337", "--no-first-run", "--no-default-browser-check"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(4)
    raise RuntimeError("guest chrome CDP unreachable: %s" % last)
def tabs():
    return [t for t in (_http("/json/list") or []) if t.get("type") == "page"]
def open_tab(url):
    return _http("/json/new?" + url, method="PUT", timeout=60)
def close_tab(tid):
    try:
        return _http("/json/close/" + tid)
    except Exception:
        return None
def evaluate(tab, expr, timeout=25):
    import asyncio, websockets
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    async def go():
        async with websockets.connect(ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if m.get("id") == 1:
                    return ((m.get("result") or {}).get("result") or {}).get("value")
    return asyncio.run(asyncio.wait_for(go(), timeout + 10))
ensure_chrome()
__BODY__
sys.stdout.write(__MARK__ + json.dumps(RESULT, default=str))
'''
def build_script(body, config=None):
    return (_TPL.replace("__CFG__", repr(json.dumps(config or {})))
                .replace("__URL__", repr(GUEST_CDP_URL))
                .replace("__MARK__", repr(MARK))
                .replace("__BODY__", body))
def _run_cdp_once(vm_ip, server_port, body, config=None, timeout=240):
    """body 里可用 tabs()/open_tab()/close_tab()/evaluate()/CONFIG,结果赋给 RESULT。"""
    code = build_script(body, config)
    payload = json.dumps({"command": ["python3", "-c", code], "shell": False})
    r = requests.post("http://%s:%s/execute" % (vm_ip, server_port),
                      headers={"Content-Type": "application/json"}, data=payload, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    out = d.get("output") or ""
    if MARK not in out:
        raise RuntimeError("guest CDP 失败 rc=%s err=%s out=%s" % (d.get("returncode"), (d.get("error") or "")[-600:], out[-300:]))
    return json.loads(out.split(MARK, 1)[1])
import time as _time
_RETRY_ON = ("502", "503", "504", "bad gateway", "connection", "timed out", "timeout")
def run_cdp(vm_ip, server_port, body, config=None, timeout=240, attempts=4):
    """nex 网关偶发 502/超时,重试几次;真实错误直接抛,不吞掉。"""
    delay, last = 3.0, None
    for i in range(1, attempts + 1):
        try:
            return _run_cdp_once(vm_ip, server_port, body, config, timeout)
        except Exception as e:
            last = e
            s = str(e).lower()
            if i >= attempts or not any(k in s for k in _RETRY_ON):
                raise
            logger.warning("[guest-cdp] 第 %s/%s 次网关抖动,%.0fs 后重试: %s", i, attempts, delay, str(e)[:160])
            _time.sleep(delay)
            delay = min(delay * 2, 20.0)
    raise last
