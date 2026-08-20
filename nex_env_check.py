"""环境自检:只验证 nex_task_smoke.py 需要的导入链是否健康,不联网、不建沙箱。
用法:  python nex_env_check.py
全绿 => 可以去跑 python nex_task_smoke.py
"""
import importlib, sys, warnings

warnings.filterwarnings("ignore")

# (包名, 期望约束, 说明)
PINS = [
    ("numpy",       lambda v: v.startswith("1."),                 "必须 <2 (cv2/gymnasium 是 NumPy1 ABI)"),
    ("scipy",       lambda v: tuple(map(int, v.split(".")[:2])) < (1, 18), "必须 <1.18 (1.18+ 要求 numpy>=2)"),
    ("librosa",     lambda v: v.startswith("0."),                 "必须 <1.0 (1.0 要求 numpy>=2)"),
    ("tifffile",    lambda v: v < "2026.4",                       "必须 <2026.4"),
    ("cv2",         lambda v: v.startswith("4.8"),                "requirements 锁 ~=4.8.1.78"),
    ("gymnasium",   lambda v: v.startswith("0.28"),               "requirements 锁 ~=0.28.1"),
]

fail = 0
print("== 版本检查 ==")
for name, ok, note in PINS:
    try:
        m = importlib.import_module(name)
        v = getattr(m, "__version__", "?")
        good = ok(v)
    except Exception as e:
        print(f"  [!!] {name:<12} 导入失败: {type(e).__name__}: {e}")
        fail += 1
        continue
    print(f"  [{'ok' if good else '!!'}] {name:<12} {v:<12} {'' if good else '<- ' + note}")
    fail += 0 if good else 1

print("\n== 导入链检查 (nex_task_smoke.py 实际走的路径) ==")
for mod in ("scipy.spatial.distance", "skimage.metrics", "easyocr",
            "desktop_env.evaluators.metrics", "desktop_env.desktop_env"):
    try:
        importlib.import_module(mod)
        print(f"  [ok] {mod}")
    except Exception as e:
        print(f"  [!!] {mod}: {type(e).__name__}: {e}")
        fail += 1

print()
if fail:
    print(f"X {fail} 项不通过。修复: uv pip install 'scipy<1.18' 'librosa<1.0' 'tifffile<2026.4'")
    sys.exit(1)
print("V 环境就绪。下一步:")
print("  export NEX_API_KEY=ak-你的真实key NEX_TEMPLATE=osworld-guest NEX_WORKSPACE_ID=workspace-jsxflfg9")
print("  python nex_task_smoke.py")
