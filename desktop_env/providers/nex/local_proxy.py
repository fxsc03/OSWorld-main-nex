"""
本地 Host 改写代理：把 localhost:<port> 的流量转发到 nex 端口网关域名(80 口)。

为什么需要：nex 入方向是"按端口分域名"的 HTTP 网关（{id}-{port}-{ws}.sbx...:80，
按 Host 头路由），而 DesktopEnv 假设"一个 host + 多个端口"。本代理让 provider
返回 docker provider 同款的 `localhost:五元组`，DesktopEnv 核心零改动。

实现：每连接一线程；解析首个请求头，Host 改写为网关域名后原样转发，之后双向
字节对拷。普通 HTTP 强制 Connection: close（保证 keep-alive 下第二个请求不会带
着错误 Host 直达网关）；WebSocket upgrade（CDP 需要）保留长连接直接对拷。
"""

import logging
import socket
import threading

logger = logging.getLogger("desktopenv.providers.nex.local_proxy")
logger.setLevel(logging.INFO)

_BUF = 65536


def _rewrite_headers(raw: bytes, target_host: str):
    """返回 (改写后的请求头, 是否 websocket upgrade)。raw 含至 \r\n\r\n 的完整头。"""
    head, sep, rest = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    is_ws = False
    out = [lines[0]]
    has_conn = False
    for line in lines[1:]:
        low = line.lower()
        if low.startswith(b"host:"):
            out.append(b"Host: " + target_host.encode())
            continue
        if low.startswith(b"connection:"):
            has_conn = True
            if b"upgrade" in low:
                is_ws = True
                out.append(line)
            else:
                out.append(b"Connection: close")
            continue
        if low.startswith(b"upgrade:") and b"websocket" in low:
            is_ws = True
        out.append(line)
    if not has_conn and not is_ws:
        out.append(b"Connection: close")
    return b"\r\n".join(out) + sep + rest, is_ws


def _pipe(src: socket.socket, dst: socket.socket):
    try:
        while True:
            data = src.recv(_BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle(client: socket.socket, target_host: str):
    try:
        client.settimeout(60)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = client.recv(_BUF)
            if not chunk:
                return
            raw += chunk
        rewritten, is_ws = _rewrite_headers(raw, target_host)
        upstream = socket.create_connection((target_host, 80), timeout=30)
        upstream.sendall(rewritten)
        client.settimeout(None if is_ws else 120)
        upstream.settimeout(None if is_ws else 120)
        t = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
        t.start()
        _pipe(upstream, client)
        t.join(timeout=5)
    except OSError as e:
        logger.debug(f"proxy conn error -> {target_host}: {e}")
    finally:
        try:
            client.close()
        except OSError:
            pass


class LocalProxy:
    """监听 127.0.0.1 随机空闲端口,转发到 target_host:80(Host 改写)。"""

    def __init__(self, target_host: str):
        self.target_host = target_host
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self.port = self._srv.getsockname()[1]
        self._alive = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        logger.info(f"proxy 127.0.0.1:{self.port} -> {target_host}:80")

    def _accept_loop(self):
        while self._alive:
            try:
                client, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=_handle, args=(client, self.target_host), daemon=True).start()

    def close(self):
        self._alive = False
        try:
            self._srv.close()
        except OSError:
            pass
