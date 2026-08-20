"""起一个 nex 沙箱并保持存活,打印 VNC 网页地址,方便肉眼查看桌面。

用法:
  python nex_debug_live.py                 # 空桌面
  python nex_debug_live.py --task evaluation_examples/examples/<域>/<id>.json
  python nex_debug_live.py --minutes 60    # 保活时长(默认 30 分钟)

Ctrl+C 退出时自动销毁沙箱。
"""
import argparse, json, os, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--task", default=None, help="跑这个任务的 setup 后再挂起")
ap.add_argument("--minutes", type=int, default=30)
args = ap.parse_args()

from desktop_env.providers.nex import client as nex

sid = None
try:
    if args.task:
        # 走完整 DesktopEnv,复现 agent 真正看到的状态
        from desktop_env.desktop_env import DesktopEnv
        task = json.load(open(args.task))
        print(f"[*] 任务: {task.get('id')}\n    {task.get('instruction','')[:90]}", flush=True)
        env = DesktopEnv(provider_name="nex", action_space="pyautogui",
                         os_type="Ubuntu", screen_size=(1920, 1080), headless=True)
        sid = env.provider.sandbox_id if hasattr(env, "provider") else None
        print("[*] 环境就绪,开始跑 setup ...", flush=True)
        env.reset(task_config=task)
        print("[*] setup 完成", flush=True)
    else:
        sid = nex.create_sandbox()
        nex.wait_until_running(sid)
        print(f"[*] 沙箱 {sid} 已启动", flush=True)

    if not sid:
        for attr in ("sandbox_id", "_sandbox_id", "vm_path"):
            sid = getattr(getattr(env, "provider", None), attr, None) or sid
    if not sid:
        sys.exit("[!] 拿不到 sandbox_id,请改用不带 --task 的模式")

    print("\n" + "=" * 66)
    for name, port in [("VNC 桌面(浏览器打开这个)", 8006),
                       ("osworld server", 5000),
                       ("chromium debug", 9222)]:
        try:
            host = nex.get_endpoint(sid, port)
            print(f"  {name:<26} http://{host}")
        except Exception as e:
            print(f"  {name:<26} 取不到: {e}")
    print("=" * 66 + "\n")

    end = time.time() + args.minutes * 60
    print(f"[*] 保活 {args.minutes} 分钟,每 10 分钟续期一次。Ctrl+C 提前结束并销毁。", flush=True)
    while time.time() < end:
        time.sleep(600)
        try:
            nex.renew(sid)
            print(f"[*] 已续期,剩余 {int((end-time.time())/60)} 分钟", flush=True)
        except Exception as e:
            print(f"[!] 续期失败: {e}", flush=True)
except KeyboardInterrupt:
    print("\n[*] 收到 Ctrl+C")
finally:
    if sid:
        try:
            nex.delete_sandbox(sid)
            print(f"[*] 已销毁沙箱 {sid}")
        except Exception as e:
            print(f"[!] 销毁失败,请手动清理 {sid}: {e}")
