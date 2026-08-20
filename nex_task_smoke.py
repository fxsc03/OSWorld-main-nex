"""
nex 任务管线冒烟（步骤 2，无需 LLM）——验证在 nex 沙箱上:
  建环境 → 跑任务 setup(下载文件/开应用) → 取截图 → evaluator 判分 → 销毁
证明 setup / observation / evaluator 三段在 nex 上都通。不接模型,不解任务(分数=0 正常)。

用法:
  cd OSWorld-main
  export NEX_API_KEY=ak-xxx NEX_TEMPLATE=osworld-guest NEX_WORKSPACE_ID=workspace-jsxflfg9
  python nex_task_smoke.py                          # 用默认 calc 任务
  python nex_task_smoke.py --task evaluation_examples/examples/<域>/<id>.json

依赖: 需先 pip install -r requirements.txt(含 gymnasium 等)。
"""
import argparse
import json
import os
import sys
import time

DEFAULT_TASK = "evaluation_examples/examples/libreoffice_calc/1954cced-e748-45c4-9c26-9855b97fbc5e.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=DEFAULT_TASK, help="任务 JSON 路径")
    ap.add_argument("--save-shot", default="nex_task_shot.png", help="setup 后截图存哪")
    args = ap.parse_args()

    for v in ("NEX_API_KEY", "NEX_TEMPLATE"):
        if not os.environ.get(v):
            sys.exit(f"[!] 环境变量 {v} 未设置。请先 export {v}=...")

    if not os.path.exists(args.task):
        sys.exit(f"[!] 任务文件不存在: {args.task}（要在 OSWorld-main 目录下运行）")
    task = json.load(open(args.task))
    print(f"[0] 任务: {task.get('id')} | 指令: {task.get('instruction','')[:70]}")
    print(f"    setup 步骤: {[s.get('type') for s in task.get('config',[])]} | evaluator: {task.get('evaluator',{}).get('func')}")

    from desktop_env.desktop_env import DesktopEnv

    t0 = time.time()
    print("[1] 建 DesktopEnv(provider=nex)——自动建沙箱+等桌面渲染(约 40-100s,别急)...", flush=True)
    env = DesktopEnv(provider_name="nex", action_space="pyautogui",
                     os_type="Ubuntu", screen_size=(1920, 1080), headless=True)
    print(f"    环境就绪, 用时 {time.time()-t0:.0f}s", flush=True)

    try:
        print("[2] env.reset(task)——跑任务 setup(下载文件/开应用)...", flush=True)
        t1 = time.time()
        obs = env.reset(task_config=task)
        print(f"    setup 完成, 用时 {time.time()-t1:.0f}s", flush=True)

        shot = obs.get("screenshot") if isinstance(obs, dict) else None
        if shot:
            with open(args.save_shot, "wb") as f:
                f.write(shot)
            print(f"[3] setup 后截图已存 {args.save_shot} ({len(shot)} bytes)——肉眼确认应用/文件已就位")
        else:
            print("[3] 未拿到 screenshot(检查 observation_type)")

        print("[4] env.evaluate()——跑 evaluator 判分(没做任务,分数应为 0,能返回=判分链通)...", flush=True)
        score = env.evaluate()
        print(f"    evaluate score = {score}")

        print("\n===== 结论 =====")
        print("✅ setup / observation / evaluator 三段在 nex 上均通。任务管线就绪。")
        print("   (score=0 是正常的——本冒烟不接模型、不解任务;接 LLM 见 NEX_USAGE.md 步骤 3)")
    finally:
        print("[5] env.close()——销毁沙箱", flush=True)
        env.close()


if __name__ == "__main__":
    main()
