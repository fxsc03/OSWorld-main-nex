#!/usr/bin/env bash
# 跑 OSWorld 全量评测(nex provider + 本地 SGLang)
#
#   bash run_full_eval.sh            # 后台跑,立刻返回并打印日志路径
#   bash run_full_eval.sh --fg       # 前台跑
#
# 中断后直接重跑同一条命令即可续跑:脚本用同一个 RESULT_DIR,
# sample_local_qwen3vl.py 会跳过已经有 result.txt 的任务。
set -euo pipefail
cd "$(dirname "$0")"

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────
TASK_SET="${TASK_SET:-evaluation_examples/test_all.json}"   # 369 个任务
RESULT_DIR="${RESULT_DIR:-./results_full}"
MODEL="${MODEL:-qwen3-vl}"
NUM_ENVS="${NUM_ENVS:-1}"
ENV_FILE="${ENV_FILE:-/data/osworld_env.sh}"

# 与 run_multienv_qwen3vl.py 对齐的参数
MAX_STEPS=15              # 同其默认值
TEMPERATURE=0             # 同
TOP_P=0.9                 # 同
COORD=relative            # 同 --coord relative(Qwen3-VL 输出归一化 0-999 坐标)
OBS_TYPE=screenshot       # 同
IMG_HISTORY=3             # 同 --max_trajectory_length 3
SCREEN_W=1920
SCREEN_H=1080

# 必须偏离其默认值的三个参数:
MAX_TOKENS=4096           # 它默认 32768。输入+输出 > context-length(32768) 会被
                          # SGLang 返回 400,agent 拿到空响应后整个任务崩掉。
SLEEP_AFTER=5.0           # 它默认 0.0。动作执行完 UI 还没重绘就截图,agent 看到
                          # 过期画面 -> 系统性失分。做复现实验时不要调小。
                          # NUM_ENVS 默认 1:nex 工作空间配额 4C/8Gi,单个沙箱正好
                          # 吃满,>1 会直接 422 quota exceeded。扩了配额再往上加。

# ─────────────────────────────────────────────────────────────
# 预检
# ─────────────────────────────────────────────────────────────
say() { printf '\n\033[1;33m>>> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[!] %s\033[0m\n' "$*" >&2; exit 1; }

say "1/5 加载环境变量"
[[ -f "$ENV_FILE" ]] || die "找不到 $ENV_FILE,先按 QS_SGLANG_RUNBOOK.md 第 1 步创建"
# shellcheck disable=SC1090
source "$ENV_FILE"
for v in NEX_API_KEY NEX_TEMPLATE NEX_WORKSPACE_ID QWEN3VL_LOCAL_ENDPOINTS; do
  [[ -n "${!v:-}" ]] || die "$v 未设置"
done
[[ "$NEX_API_KEY" == ak-* ]] || die "NEX_API_KEY 看起来不对: ${NEX_API_KEY:0:8}..."
python3 -c "import os,sys; sys.exit(0 if os.environ['NEX_API_KEY'].isascii() else 1)" \
  || die "NEX_API_KEY 含非 ASCII 字符(多半是把占位符整行复制了)"
echo "    workspace=$NEX_WORKSPACE_ID  template=$NEX_TEMPLATE"

say "2/5 关掉 GPU 保活(会和推理抢算力)"
pkill -f gpu_keepalive 2>/dev/null || true

say "3/5 检查 SGLang 端点"
IFS=',' read -ra EPS <<< "$QWEN3VL_LOCAL_ENDPOINTS"
for ep in "${EPS[@]}"; do
  curl -fsS --max-time 10 "${ep}/v1/models" >/dev/null 2>&1 \
    && echo "    $ep  OK" \
    || die "$ep 不可用。先起服务(见 QS_SGLANG_RUNBOOK.md 第 3 步)"
done

say "4/5 清理残留沙箱(会占满 nex 配额)"
python3 - <<'PY'
from desktop_env.providers.nex import client as nex
try:
    d = nex._request("GET", nex._instances_base())
    items = d if isinstance(d, list) else (d.get("items") or d.get("list") or d.get("instances") or [])
    if not items:
        print("    无残留")
    for it in items:
        sid = it.get("id") or it.get("sandbox_id") or it.get("instance_id")
        if sid:
            print("    删除", sid); nex.delete_sandbox(sid)
except Exception as e:
    print("    列举失败(可忽略):", e)
PY

mkdir -p logs "$RESULT_DIR" cache
TOTAL=$(python3 -c "
import json; d=json.load(open('$TASK_SET'))
print(sum(len(v) for v in d.values()) if isinstance(d, dict) else len(d))")

say "5/5 启动评测"
cat <<EOF
    任务集      $TASK_SET  ($TOTAL 个)
    结果目录    $RESULT_DIR
    模型        $MODEL @ $QWEN3VL_LOCAL_ENDPOINTS
    max_steps   $MAX_STEPS      max_tokens  $MAX_TOKENS
    并发        $NUM_ENVS        每步等待    ${SLEEP_AFTER}s
EOF

# 留档模型输入输出会占几个 GB,全量默认关闭。要开:export OSWORLD_DUMP_LLM_IO=1
[[ "${OSWORLD_DUMP_LLM_IO:-0}" == "1" ]] && echo "    ⚠ LLM 留档已开启,注意磁盘"

CMD=(python3 sample_local_qwen3vl.py
     --provider_name nex
     --test_all_meta_path "$TASK_SET"
     --model "$MODEL"
     --headless
     --observation_type "$OBS_TYPE"
     --max_steps "$MAX_STEPS"
     --max_tokens "$MAX_TOKENS"
     --temperature "$TEMPERATURE"
     --top_p "$TOP_P"
     --coordinate_type "$COORD"
     --max_image_history_length "$IMG_HISTORY"
     --sleep_after_execution "$SLEEP_AFTER"
     --screen_width "$SCREEN_W" --screen_height "$SCREEN_H"
     --num_envs "$NUM_ENVS"
     --result_dir "$RESULT_DIR")

if [[ "${1:-}" == "--fg" ]]; then
  exec "${CMD[@]}"
else
  LOG="/data/eval_full_$(date +%m%d_%H%M).log"
  nohup "${CMD[@]}" > "$LOG" 2>&1 &
  echo "    PID $!   日志 $LOG"
  cat <<EOF

看进度:
    tail -f $LOG
    echo "\$(find $RESULT_DIR -name result.txt | wc -l) / $TOTAL 已完成"
    python3 show_result.py --result_dir $RESULT_DIR

环境是否健康(修好时最大重复截图应 <= 2):
    for d in \$(find $RESULT_DIR -name "step_1.png" -exec dirname {} \;); do
      echo "\$(basename \$(dirname \$d)) : \$(md5sum \$d/step_*.png | awk '{print \$1}' | sort | uniq -c | sort -rn | head -1 | awk '{print \$1}') / \$(ls \$d/step_*.png | wc -l)"
    done

停止:
    pkill -f sample_local_qwen3vl
EOF
fi
