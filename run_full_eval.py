#!/usr/bin/env python3
"""带性能埋点的 OSWorld 全量评测启动器。

    python run_full_eval.py                          # 跑全量 test_all.json
    python run_full_eval.py --task_set evaluation_examples/smoke_test_5tasks.json
    python run_full_eval.py --report_only            # 只对已有数据出报告

做的事:
  1. 预检(环境变量 / SGLang 端点 / 残留沙箱 / GPU 保活)
  2. 给 DesktopEnv、nex client、SetupController、agent 打上计时埋点
  3. 把参数拼好后交给 sample_local_qwen3vl.py 执行
  4. 结束后聚合所有子进程的事件,打印分阶段耗时表 + 故障分类账,落盘 perf_stats.json

埋点全部是 monkey-patch,不改仓库任何原有文件;删掉本文件就干净了。
每个进程把事件写到 <result_dir>/perf/events_<pid>.jsonl,进程崩了也不丢数据。
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from collections import defaultdict

# 所有产出一律锚定到本文件所在目录(=仓库根),不受 cwd 影响。
# 仓库放在云盘上时,换机器后 runs/ 里的历史数据自动跟着走。
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────────────
# 参数
# ─────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="OSWorld 全量评测 + 性能埋点")
    p.add_argument("--task_set", default="evaluation_examples/test_all.json")
    p.add_argument("--result_dir", default=None,
                   help="显式指定输出目录;不给则用 <仓库>/runs/<UTC时间戳>_<机器名>/")
    p.add_argument("--runs_root", default=None,
                   help="run 目录的父目录,默认 <仓库>/runs")
    p.add_argument("--run_name", default=None,
                   help="本次 run 的目录名,默认 <UTC时间戳>_<机器名>")
    p.add_argument("--model", default="qwen3-vl")
    p.add_argument("--num_envs", type=int, default=2)

    # 与 run_multienv_qwen3vl.py 默认值对齐
    p.add_argument("--max_steps", type=int, default=15)
    p.add_argument("--temperature", type=float, default=0)
    p.add_argument("--top_p", type=float, default=0.9)
    p.add_argument("--coordinate_type", default="relative",
                   choices=["absolute", "relative", "qwen25"])
    p.add_argument("--observation_type", default="screenshot")
    p.add_argument("--max_image_history_length", type=int, default=3)
    p.add_argument("--screen_width", type=int, default=1920)
    p.add_argument("--screen_height", type=int, default=1080)

    # 必须偏离其默认值的两个
    p.add_argument("--max_tokens", type=int, default=4096,
                   help="它默认 32768;输入+输出超过 context-length 会被 SGLang 返回 400")
    p.add_argument("--sleep_after_execution", type=float, default=5.0,
                   help="它默认 0.0;动作后 UI 未重绘就截图会系统性失分,复现实验勿调小")

    p.add_argument("--no_dump_llm_io", action="store_true",
                   help="关掉模型输入输出留档(默认开,写到每个任务目录的 llm_io/)")
    p.add_argument("--skip_preflight", action="store_true")
    p.add_argument("--report_only", action="store_true",
                   help="只聚合已有事件出报告;不指定 --result_dir 时自动取最近一次 run")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────
# 事件记录
# ─────────────────────────────────────────────────────────────────────
_PERF_DIR = None
_FH = None
_CUR_TASK = {"id": "-"}


def _init_writer(result_dir):
    global _PERF_DIR
    _PERF_DIR = os.path.join(result_dir, "perf")
    os.makedirs(_PERF_DIR, exist_ok=True)


def _emit(phase, dur, ok=True, extra=None):
    """写一条事件。每个进程首次调用时按自己的 pid 开文件。"""
    global _FH
    if _PERF_DIR is None:
        return
    try:
        if _FH is None or getattr(_FH, "_pid", None) != os.getpid():
            _FH = open(os.path.join(_PERF_DIR, f"events_{os.getpid()}.jsonl"),
                       "a", encoding="utf-8")
            _FH._pid = os.getpid()
        rec = {"ts": round(time.time(), 3), "pid": os.getpid(), "phase": phase,
               "dur": round(dur, 4), "ok": ok, "task": _CUR_TASK["id"]}
        if extra:
            rec.update(extra)
        _FH.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _FH.flush()
    except Exception:
        pass


def timed(phase, extra_fn=None):
    """把一个函数包成计时器。失败也记一条 ok=False。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            t0 = time.time()
            try:
                r = fn(*a, **kw)
                _emit(phase, time.time() - t0, True,
                      extra_fn(r, a, kw) if extra_fn else None)
                return r
            except BaseException as e:
                _emit(phase, time.time() - t0, False,
                      {"err": f"{type(e).__name__}: {str(e)[:160]}"})
                raise
        return wrapper
    return deco


# ─────────────────────────────────────────────────────────────────────
# 埋点
# ─────────────────────────────────────────────────────────────────────
def install_patches():
    import lib_run_single
    from desktop_env.desktop_env import DesktopEnv
    from desktop_env.controllers.setup import SetupController
    from desktop_env.controllers.python import PythonController
    from desktop_env.providers.nex import client as nexc

    # --- nex 沙箱生命周期 ---
    nexc.create_sandbox     = timed("sandbox.create")(nexc.create_sandbox)
    nexc.wait_until_running = timed("sandbox.wait_running")(nexc.wait_until_running)
    nexc.wait_guest_ready   = timed("sandbox.guest_ready")(nexc.wait_guest_ready)
    nexc.delete_sandbox     = timed("sandbox.delete")(nexc.delete_sandbox)

    # --- DesktopEnv ---
    DesktopEnv.__init__ = timed("env.provision")(DesktopEnv.__init__)
    DesktopEnv.reset    = timed("env.reset_total")(DesktopEnv.reset)
    DesktopEnv.close    = timed("env.close")(DesktopEnv.close)
    DesktopEnv.evaluate = timed("env.evaluate",
        lambda r, a, kw: {"score": r})(DesktopEnv.evaluate)
    DesktopEnv._get_obs = timed("step.get_obs")(DesktopEnv._get_obs)
    DesktopEnv.step     = timed("step.execute")(DesktopEnv.step)
    DesktopEnv.maximize_window = timed("step.maximize_window")(DesktopEnv.maximize_window)
    DesktopEnv.get_app_state   = timed("step.get_app_state")(DesktopEnv.get_app_state)
    for name, phase in [("_ensure_mcp_server", "reset.mcp_probe"),
                        ("_dismiss_session_failed", "reset.dismiss_session_failed"),
                        ("get_mcp_tool_list", "step.mcp_tool_list")]:
        if hasattr(DesktopEnv, name):
            setattr(DesktopEnv, name, timed(phase)(getattr(DesktopEnv, name)))

    # --- setup 各步骤 ---
    SetupController.setup           = timed("reset.setup_total")(SetupController.setup)
    SetupController._download_setup = timed("reset.setup_download")(SetupController._download_setup)
    SetupController._open_setup     = timed("reset.setup_open")(SetupController._open_setup)
    SetupController._launch_setup   = timed("reset.setup_launch")(SetupController._launch_setup)

    # --- 底层 IO ---
    PythonController.get_screenshot = timed("io.screenshot")(PythonController.get_screenshot)
    PythonController.execute_action = timed("io.execute_action")(PythonController.execute_action)

    # --- agent 推理 ---
    try:
        from mm_agents.qwen3vl_agent_local import Qwen3VLAgentLocal
        inner = "_call_llm_inner" if hasattr(Qwen3VLAgentLocal, "_call_llm_inner") else "call_llm"
        setattr(Qwen3VLAgentLocal, inner,
                timed("llm.inference")(getattr(Qwen3VLAgentLocal, inner)))
        Qwen3VLAgentLocal.predict = timed("llm.predict_total")(Qwen3VLAgentLocal.predict)
    except Exception as e:
        print(f"[perf] agent 埋点跳过: {e}")

    # --- lib_run_single 里写死的 time.sleep,单独归类 ---
    class _TimeProxy:
        def __getattr__(self, k):
            return getattr(time, k)
        def sleep(self, s):
            t0 = time.time()
            time.sleep(s)
            _emit("wait.hardcoded_sleep", time.time() - t0, True, {"requested": s})
    lib_run_single.time = _TimeProxy()

    # --- 任务级:记录 task id、总耗时、是否用满步数 ---
    _orig_run = lib_run_single.run_single_example

    @functools.wraps(_orig_run)
    def run_single_example(agent, env, example, max_steps, instruction, args_,
                           example_result_dir, scores, *a, **kw):
        _CUR_TASK["id"] = f"{example.get('id','?')[:8]}"
        t0 = time.time()
        ok = True
        try:
            return _orig_run(agent, env, example, max_steps, instruction, args_,
                             example_result_dir, scores, *a, **kw)
        except BaseException as e:
            ok = False
            _emit("task.error", 0, False, {"err": f"{type(e).__name__}: {str(e)[:160]}"})
            raise
        finally:
            steps = 0
            try:
                tj = os.path.join(example_result_dir, "traj.jsonl")
                steps = sum(1 for _ in open(tj, encoding="utf-8")) if os.path.exists(tj) else 0
            except Exception:
                pass
            score = None
            try:
                rf = os.path.join(example_result_dir, "result.txt")
                if os.path.exists(rf):
                    score = float(open(rf).read().strip())
            except Exception:
                pass
            _emit("task.total", time.time() - t0, ok,
                  {"steps": steps, "hit_max_steps": steps >= max_steps, "score": score})
            _CUR_TASK["id"] = "-"

    lib_run_single.run_single_example = run_single_example
    print("[perf] 埋点已安装")


# ─────────────────────────────────────────────────────────────────────
# 预检
# ─────────────────────────────────────────────────────────────────────
def preflight(args):
    def die(m):
        print(f"\n[!] {m}\n", file=sys.stderr)
        sys.exit(1)

    print(">>> 预检")
    for v in ("NEX_API_KEY", "NEX_TEMPLATE", "NEX_WORKSPACE_ID", "QWEN3VL_LOCAL_ENDPOINTS"):
        if not os.environ.get(v):
            die(f"{v} 未设置。先 source /data/osworld_env.sh")
    key = os.environ["NEX_API_KEY"]
    if not key.isascii():
        die("NEX_API_KEY 含非 ASCII 字符(多半把占位符整行复制了)")
    if not key.startswith("ak-"):
        die(f"NEX_API_KEY 格式可疑: {key[:8]}...")

    subprocess.run(["pkill", "-f", "gpu_keepalive"], capture_output=True)
    print("    GPU 保活已关闭")

    import urllib.request
    for ep in os.environ["QWEN3VL_LOCAL_ENDPOINTS"].split(","):
        ep = ep.strip()
        try:
            urllib.request.urlopen(ep + "/v1/models", timeout=10).read()
            print(f"    {ep}  OK")
        except Exception as e:
            die(f"{ep} 不可用 ({e})。先起 SGLang,见 QS_SGLANG_RUNBOOK.md 第 3 步")

    try:
        from desktop_env.providers.nex import client as nexc
        d = nexc._request("GET", nexc._instances_base())
        items = d if isinstance(d, list) else (d.get("items") or d.get("list")
                                               or d.get("instances") or [])
        for it in items:
            sid = it.get("id") or it.get("sandbox_id") or it.get("instance_id")
            if sid:
                print(f"    删除残留沙箱 {sid}")
                nexc.delete_sandbox(sid)
        if not items:
            print("    无残留沙箱")
    except Exception as e:
        print(f"    残留沙箱检查失败(可忽略): {e}")


# ─────────────────────────────────────────────────────────────────────
# 报告
# ─────────────────────────────────────────────────────────────────────
TOP_LEVEL = ["env.provision", "env.reset_total", "step.get_obs", "llm.predict_total",
             "step.execute", "wait.hardcoded_sleep", "env.evaluate", "env.close"]


def report(result_dir):
    perf_dir = os.path.join(result_dir, "perf")
    if not os.path.isdir(perf_dir):
        print(f"[!] 找不到 {perf_dir},没有埋点数据")
        return
    events = []
    for fn in os.listdir(perf_dir):
        if fn.startswith("events_") and fn.endswith(".jsonl"):
            for line in open(os.path.join(perf_dir, fn), encoding="utf-8"):
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    if not events:
        print("[!] 埋点文件为空")
        return

    by_phase = defaultdict(list)
    fails = defaultdict(int)
    for e in events:
        by_phase[e["phase"]].append(e)
        if not e.get("ok", True):
            fails[e["phase"]] += 1

    tasks = by_phase.get("task.total", [])
    n_task = len(tasks)
    task_wall = sum(e["dur"] for e in tasks)
    span = max(e["ts"] for e in events) - min(e["ts"] for e in events)

    def row(phase):
        ev = by_phase.get(phase, [])
        if not ev:
            return None
        d = sorted(x["dur"] for x in ev)
        tot = sum(d)
        return (phase, len(d), tot, 100 * tot / task_wall if task_wall else 0,
                statistics.median(d), d[int(len(d) * 0.9)] if len(d) > 1 else d[0], d[-1])

    def table(title, phases, note=""):
        print(f"\n{title}")
        if note:
            print(f"  {note}")
        print(f"  {'阶段':<30}{'次数':>7}{'总耗时':>12}{'占比':>8}{'p50':>10}{'p90':>10}{'max':>10}")
        print("  " + "-" * 87)
        for p in phases:
            r = row(p)
            if r:
                print(f"  {r[0]:<30}{r[1]:>7}{r[2]:>11.1f}s{r[3]:>7.1f}%"
                      f"{r[4]:>9.2f}s{r[5]:>9.2f}s{r[6]:>9.2f}s")

    print("\n" + "=" * 91)
    print(f"  OSWorld 性能报告   {n_task} 个任务   墙钟 {span/3600:.2f}h"
          f"   任务耗时合计 {task_wall/3600:.2f}h")
    print("=" * 91)

    table("【顶层阶段】占比之和 ≈ 100%,相互不重叠", TOP_LEVEL)
    table("【沙箱生命周期】", ["sandbox.create", "sandbox.wait_running",
                              "sandbox.guest_ready", "sandbox.delete"],
          "注:这些嵌套在 env.provision / env.close 内部")
    table("【reset 细分】", ["reset.setup_total", "reset.setup_download",
                            "reset.setup_open", "reset.setup_launch",
                            "reset.mcp_probe", "reset.dismiss_session_failed"],
          "注:嵌套在 env.reset_total 内部")
    table("【单步细分】", ["step.mcp_tool_list", "step.maximize_window",
                          "step.get_app_state", "io.screenshot",
                          "io.execute_action", "llm.inference"],
          "注:嵌套在 step.* / llm.predict_total 内部")

    # 故障分类账
    print("\n【故障分类账】")
    steps_ev = [e for e in tasks]
    hit_max = sum(1 for e in steps_ev if e.get("hit_max_steps"))
    scored = [e["score"] for e in steps_ev if e.get("score") is not None]
    ledger = [
        ("任务完成", n_task, ""),
        ("任务出错", len(by_phase.get("task.error", [])), "run_single_example 抛异常"),
        ("用满 max_steps", hit_max,
         f"{100*hit_max/n_task:.0f}% —— agent 没主动 DONE/FAIL" if n_task else ""),
        ("沙箱创建", len(by_phase.get("sandbox.create", [])), ""),
        ("沙箱创建失败", fails.get("sandbox.create", 0), "配额/网络"),
        ("桌面就绪超时", fails.get("sandbox.guest_ready", 0), ""),
        ("MCP 探测", len(by_phase.get("reset.mcp_probe", [])), ""),
        ("MCP 探测失败", fails.get("reset.mcp_probe", 0), "本场景不用 MCP,失败无害但耗时"),
        ("MCP tool_list 调用", len(by_phase.get("step.mcp_tool_list", [])), "每步都在浪费"),
        ("LLM 调用", len(by_phase.get("llm.inference", [])), ""),
        ("LLM 调用失败", fails.get("llm.inference", 0), "含 400 context overflow"),
        ("evaluate 失败", fails.get("env.evaluate", 0), ""),
    ]
    w = max(len(x[0]) for x in ledger)
    for name, cnt, note in ledger:
        print(f"  {name:<{w}}  {cnt:>7}   {note}")

    if scored:
        print(f"\n  成功率  {100*sum(scored)/len(scored):.2f}%   ({int(sum(scored))}/{len(scored)})")
    if n_task:
        print(f"  吞吐    {n_task/(span/3600):.1f} 任务/小时"
              f"   单任务均值 {task_wall/n_task:.0f}s")

    # 优化提示
    waste = sum(sum(x["dur"] for x in by_phase.get(p, []))
                for p in ("reset.mcp_probe", "step.mcp_tool_list", "wait.hardcoded_sleep"))
    if waste and task_wall:
        print(f"\n【可优化】MCP 探测 + MCP tool_list + 写死的 sleep 合计 {waste/3600:.2f}h"
              f",占任务总耗时 {100*waste/task_wall:.1f}%")
        print("  这三项都不改变 agent 看到的内容,砍掉不影响评测协议。")

    out = os.path.join(result_dir, "perf_stats.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "tasks": n_task, "wall_clock_sec": span, "task_time_sum_sec": task_wall,
            "success_rate": (sum(scored)/len(scored)) if scored else None,
            "hit_max_steps": hit_max,
            "phases": {p: {"count": len(v), "total_sec": sum(x["dur"] for x in v),
                           "p50": statistics.median([x["dur"] for x in v]),
                           "max": max(x["dur"] for x in v),
                           "failed": fails.get(p, 0)}
                       for p, v in sorted(by_phase.items())},
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  明细已写入 {out}\n")


# ─────────────────────────────────────────────────────────────────────
def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def _latest_run(runs_root):
    """report_only 不给目录时,挑最近一次有埋点的 run。"""
    if not os.path.isdir(runs_root):
        return None
    cands = [os.path.join(runs_root, d) for d in os.listdir(runs_root)]
    cands = [c for c in cands if os.path.isdir(os.path.join(c, "perf"))]
    return max(cands, key=os.path.getmtime) if cands else None


def write_run_meta(run_dir, args, task_set_path):
    """记下这一轮是在哪台机器、哪个 commit、什么参数下跑的。

    刻意不记 NEX_API_KEY —— run_meta.json 是要进 git 的。
    """
    n_tasks = None
    try:
        m = json.load(open(task_set_path, encoding="utf-8"))
        n_tasks = sum(len(v) for v in m.values()) if isinstance(m, dict) else len(m)
    except Exception:
        pass
    info = {
        "run_name": os.path.basename(run_dir.rstrip("/")),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "repo_root": REPO_ROOT,
        "git_commit": _sh("git -C '%s' rev-parse HEAD" % REPO_ROOT),
        "git_dirty": bool(_sh("git -C '%s' status --porcelain" % REPO_ROOT)),
        "python": sys.version.split()[0],
        "gpus": [l for l in _sh("nvidia-smi -L").splitlines() if l.strip()],
        "endpoints": [e.strip() for e in
                      os.environ.get("QWEN3VL_LOCAL_ENDPOINTS", "").split(",") if e.strip()],
        "nex_workspace": os.environ.get("NEX_WORKSPACE_ID"),
        "nex_template": os.environ.get("NEX_TEMPLATE"),
        "task_set": task_set_path,
        "task_count": n_tasks,
        "dump_llm_io": os.environ.get("OSWORLD_DUMP_LLM_IO") == "1",
        "args": dict(vars(args)),
    }
    with open(os.path.join(run_dir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


_INDEX_HEADER = (
    "# 历次 run 索引\n\n"
    "换机器后这张表跟着云盘一起走,用来横向对比不同机器 / 不同参数下的耗时和成功率。\n"
    "每行对应 `runs/<目录>/`,里面有 `perf_stats.json`(统计)、`perf/`(原始埋点)、\n"
    "以及各任务目录下的 `llm_io/`(模型输入输出)。\n\n"
    "| run | 机器 | 开始(UTC) | 任务数 | 并发 | max_steps | 成功率 | 墙钟(h) | 单任务均值(s) | git |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)


def append_index(runs_root, run_dir, meta):
    st = {}
    sp = os.path.join(run_dir, "perf_stats.json")
    if os.path.exists(sp):
        try:
            st = json.load(open(sp, encoding="utf-8"))
        except Exception:
            pass
    idx = os.path.join(runs_root, "INDEX.md")
    fresh = not os.path.exists(idx)
    n = st.get("tasks") or 0
    sr = st.get("success_rate")
    wall = st.get("wall_clock_sec") or 0
    avg = (st.get("task_time_sum_sec") or 0) / n if n else 0
    a = meta.get("args", {})
    with open(idx, "a", encoding="utf-8") as f:
        if fresh:
            f.write(_INDEX_HEADER)
        f.write("| `{run}` | {host} | {ts} | {n} | {envs} | {ms} | {sr} | "
                "{wall:.2f} | {avg:.0f} | `{git}` |\n".format(
                    run=meta.get("run_name", "-"), host=meta.get("hostname", "-"),
                    ts=meta.get("started_utc", "-"), n=n,
                    envs=a.get("num_envs", "-"), ms=a.get("max_steps", "-"),
                    sr=("%.1f%%" % (100 * sr)) if sr is not None else "-",
                    wall=wall / 3600, avg=avg,
                    git=(meta.get("git_commit") or "")[:8]
                        + ("+dirty" if meta.get("git_dirty") else "")))
    return idx


def main():
    args = parse_args()
    runs_root = os.path.abspath(args.runs_root or os.path.join(REPO_ROOT, "runs"))

    if args.report_only:
        run_dir = os.path.abspath(args.result_dir) if args.result_dir \
            else _latest_run(runs_root)
        if not run_dir:
            print(f"[!] {runs_root} 下没有任何带 perf/ 的 run,先跑一次评测")
            sys.exit(1)
        print(f">>> 报告目录: {run_dir}")
        report(run_dir)
        return

    if args.result_dir:
        run_dir = os.path.abspath(args.result_dir)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        run_dir = os.path.join(runs_root,
                               args.run_name or f"{stamp}_{socket.gethostname().split('.')[0]}")

    os.makedirs(runs_root, exist_ok=True)
    os.makedirs(run_dir, exist_ok=True)
    _init_writer(run_dir)

    # 模型输入输出默认留档:agent 会把每次请求/响应写到
    # <run_dir>/.../<任务id>/llm_io/call_NNN.json,图片按 sha1 去重存同目录。
    if not args.no_dump_llm_io:
        os.environ["OSWORLD_DUMP_LLM_IO"] = "1"

    if not args.skip_preflight:
        preflight(args)

    for d in ("logs", "cache", os.path.join(REPO_ROOT, "logs"),
              os.path.join(REPO_ROOT, "cache")):
        os.makedirs(d, exist_ok=True)

    meta = write_run_meta(run_dir, args, args.task_set)
    print(f"\n>>> 本次 run 目录: {run_dir}")
    print(f"    机器 {meta['hostname']} | git {(meta.get('git_commit') or '')[:8]}"
          f"{' (dirty)' if meta.get('git_dirty') else ''}"
          f" | 模型输入输出留档 {'开' if meta['dump_llm_io'] else '关'}")

    install_patches()

    sys.argv = [
        "sample_local_qwen3vl.py",
        "--provider_name", "nex",
        "--test_all_meta_path", args.task_set,
        "--model", args.model,
        "--headless",
        "--observation_type", args.observation_type,
        "--max_steps", str(args.max_steps),
        "--max_tokens", str(args.max_tokens),
        "--temperature", str(args.temperature),
        "--top_p", str(args.top_p),
        "--coordinate_type", args.coordinate_type,
        "--max_image_history_length", str(args.max_image_history_length),
        "--sleep_after_execution", str(args.sleep_after_execution),
        "--screen_width", str(args.screen_width),
        "--screen_height", str(args.screen_height),
        "--num_envs", str(args.num_envs),
        "--result_dir", run_dir,
    ]
    print(">>> 启动评测:", " ".join(sys.argv[1:]), "\n")

    import runpy
    try:
        runpy.run_path("sample_local_qwen3vl.py", run_name="__main__")
    except SystemExit:
        pass
    except KeyboardInterrupt:
        print("\n[perf] 收到 Ctrl+C,先出报告")
    finally:
        report(run_dir)
        try:
            idx = append_index(runs_root, run_dir, meta)
            print(f"  索引已更新 {idx}")
        except Exception as e:
            print(f"[perf] 索引更新失败(不影响数据): {e}")
        print(f"  本次 run 全部产出在 {run_dir}\n")


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
