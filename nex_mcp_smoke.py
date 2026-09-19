"""
nex MCP 冒烟（步骤 2.5，无需 LLM）——验证 MCP 链路在 nex 沙箱上通:
  建环境 → 任务 setup → MCP 文件注入 + server(:9292) 起 → tool_list 非空 → 实调一个只读工具

与 nex_task_smoke.py 的区别: 那个验证 setup/observation/evaluator，本脚本专验 MCP 四段:
  ① 注入(host→guest, 走 5000 execute 通道)  ② FastMCP server 在 guest 内起监听 9292
  ③ list_tools 经 RAG 过滤返回非空          ④ call_tool 实调(默认 get_workbook_info, 走 UNO :2002)

用法:
  cd OSWorld-main-nex
  export NEX_API_KEY=ak-xxx NEX_TEMPLATE=osworld-guest NEX_WORKSPACE_ID="workspace-<your-workspace>"
  python nex_mcp_smoke.py                       # 默认 calc 任务
  python nex_mcp_smoke.py --task evaluation_examples/examples/<域>/<id>.json

  MCP_SRC_ROOT 缺省指向本仓库根目录（其下包含 mcp/）(desktop_env 在 import 时读该
环境变量，所以必须在 import DesktopEnv 之前设好——本脚本已处理)。
"""
import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# 必须在 import desktop_env 之前设置(类体读取)
os.environ.setdefault(
    "MCP_SRC_ROOT",
    _HERE,
)

DEFAULT_TASK = "evaluation_examples/examples/libreoffice_calc/1954cced-e748-45c4-9c26-9855b97fbc5e.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=DEFAULT_TASK, help="任务 JSON 路径")
    ap.add_argument("--tool", default=None,
                    help="要实调的工具名(缺省自动挑: get_workbook_info 优先, 其次任意 get/info 只读工具)")
    ap.add_argument("--save-shot", default="nex_mcp_shot.png")
    args = ap.parse_args()

    for v in ("NEX_API_KEY", "NEX_TEMPLATE"):
        if not os.environ.get(v):
            sys.exit(f"[!] 环境变量 {v} 未设置。请先 export {v}=...")

    mcp_src = os.environ["MCP_SRC_ROOT"]
    if not os.path.isdir(os.path.join(mcp_src, "mcp")):
        sys.exit(f"[!] MCP_SRC_ROOT 无效: {mcp_src}(下面必须有 mcp/ 目录)")
    print(f"[0] MCP_SRC_ROOT = {mcp_src}")

    if not os.path.exists(args.task):
        sys.exit(f"[!] 任务文件不存在: {args.task}(要在 OSWorld-main-nex 目录下运行)")
    task = json.load(open(args.task))
    print(f"    任务: {task.get('id')} | 指令: {task.get('instruction','')[:70]}")

    from desktop_env.desktop_env import DesktopEnv

    t0 = time.time()
    print("[1] 建 DesktopEnv(provider=nex)——建沙箱+等桌面渲染(约 40-100s)...", flush=True)
    env = DesktopEnv(provider_name="nex", action_space="pyautogui",
                     os_type="Ubuntu", screen_size=(1920, 1080), headless=True)
    print(f"    环境就绪, 用时 {time.time()-t0:.0f}s", flush=True)

    ok_list, ok_call = False, False
    try:
        print("[2] env.reset(task)——setup + MCP 注入 + server 起 + 首帧 obs...", flush=True)
        t1 = time.time()
        obs = env.reset(task_config=task)
        print(f"    reset 完成, 用时 {time.time()-t1:.0f}s", flush=True)

        shot = obs.get("screenshot")
        if shot:
            with open(args.save_shot, "wb") as f:
                f.write(shot)
            print(f"    截图已存 {args.save_shot} ({len(shot)} bytes)")

        # ── ③ tool_list ────────────────────────────────────────────────
        tool_name = obs.get("tool_name")
        tool_list = obs.get("tool_list") or []
        print(f"[3] tool_name={tool_name!r}, tool_list n={len(tool_list)}")
        if not tool_list:
            print("    首帧为空, 重试 _ensure_mcp_server + get_mcp_tool_list...", flush=True)
            env._ensure_mcp_server()
            time.sleep(3)
            tool_list = env.get_mcp_tool_list(
                tool_name or "libreoffice_calc",
                instruction=task.get("instruction"))
        names = [t.get("name") for t in tool_list]
        ok_list = len(names) > 0
        print(f"    tools({len(names)}): {names[:12]}{' ...' if len(names) > 12 else ''}")

        # ── ④ call_tool ────────────────────────────────────────────────
        target = args.tool
        if not target:
            target = next((n for n in names if n and "get_workbook_info" in n), None)
        if not target:
            target = next((n for n in names if n and re.search(r"get|info|list|search", n)), None)
        if target:
            print(f"[4] call_mcp_tool({target!r}, {{}}) ...", flush=True)
            t2 = time.time()
            resp = env.call_mcp_tool(target, {})
            print(f"    用时 {time.time()-t2:.0f}s, 原始返回({len(resp)} chars):")
            print("    " + resp[:800].replace("\n", "\n    "))
            bad = re.search(r'Traceback|NameError|ModuleNotFound|Connection refused|isError=True|is_error=True|"success"\s*:\s*false', resp)
            ok_call = bool(resp) and not bad
        else:
            print("[4] tool_list 里没有可自动挑的只读工具, 跳过实调(用 --tool 指定)")

        print("\n===== 结论 =====")
        print(f"  注入+server+list_tools : {'✅ 通' if ok_list else '❌ 不通(tool_list 为空)'}")
        print(f"  call_tool 实调         : {'✅ 通' if ok_call else '❌ 不通/未跑'}")
        if ok_list and ok_call:
            print("  MCP 全链路在 nex 上验证通过 → B2/hybrid 可以切 --provider_name nex")
    finally:
        print("[5] env.close()——销毁沙箱", flush=True)
        env.close()

    if not (ok_list and ok_call):
        sys.exit(1)


if __name__ == "__main__":
    main()
