"""Offline test for SetupController._activate_window_setup.

A fake guest server simulates four situations; no sandbox needed.
Run:  PYTHONPATH=$PWD python3 tests/test_activate_window_fallback.py
"""
import json, logging, threading, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO)
CAP = []
class Grab(logging.Handler):
    def emit(self, r): CAP.append(r.getMessage())
logging.getLogger().addHandler(Grab())

WIN = "WeeklySales.xlsx - LibreOffice Calc"
S = {"case": "", "focus": "", "escaped": False}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="text/plain"):
        b = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        c = S["case"]
        if self.path == "/setup/activate_window":
            if c == "endpoint_ok": S["focus"] = WIN
            return self._send(200 if c in ("endpoint_ok", "no_verify") else 500,
                              "ok" if c in ("endpoint_ok", "no_verify") else "FileNotFoundError: wmctrl")
        # /setup/execute
        cmd = d.get("command") or []
        prog, arg = cmd[0], (cmd[2] if len(cmd) > 2 else "")
        if prog == "python3" and "get_input_focus" in arg:           # focus probe
            if c == "no_verify":
                return self._send(200, json.dumps({"returncode": 1, "output": "", "error": "no Xlib"}), "application/json")
            return self._send(200, json.dumps({"returncode": 0, "output": S["focus"] + "\n", "error": ""}), "application/json")
        if prog == "python" and "escape" in arg:                      # leave overview
            S["escaped"] = True
            return self._send(200, json.dumps({"returncode": 0, "output": "", "error": ""}), "application/json")
        if prog == "wmctrl":
            if c == "escape_rescue" and S["escaped"]: S["focus"] = WIN
            return self._send(200, json.dumps({"returncode": 0 if c == "escape_rescue" else 127,
                                               "output": "", "error": "" if c == "escape_rescue" else "not found"}), "application/json")
        return self._send(200, json.dumps({"returncode": 0, "output": "", "error": ""}), "application/json")

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from desktop_env.controllers.setup import SetupController
sc = SetupController(vm_ip="127.0.0.1", server_port=srv.server_address[1], cache_dir="/tmp/cachetest")
sc.http_server = "http://127.0.0.1:%d" % srv.server_address[1]

CASES = [   # (case, expected, focus-holder at start, description)
    ("endpoint_ok",   True,  "",            "endpoint works, focus verified"),
    ("escape_rescue", True,  "gnome-shell", "overview holds focus -> Escape + wmctrl rescues"),
    ("all_fail",      False, "gnome-shell", "nothing works -> False + loud log"),
    ("no_verify",     True,  "",            "no Xlib in guest -> legacy trust-the-endpoint"),
]
fails = 0
for case, expect, focus, desc in CASES:
    S.update(case=case, focus=focus, escaped=False); del CAP[:]
    got = sc._activate_window_setup(WIN, strict=True)
    loud = any("ACTIVATE_WINDOW_FAILED" in m for m in CAP)
    ok = got == expect and loud == (case == "all_fail")
    print("%-14s got=%-5s expect=%-5s escaped=%-5s %s  (%s)" % (case, got, expect, S["escaped"], "PASS" if ok else "FAIL", desc))
    fails += 0 if ok else 1
srv.shutdown()
print("RESULT", "ALL_PASS" if fails == 0 else "%d_FAILED" % fails)
sys.exit(1 if fails else 0)
