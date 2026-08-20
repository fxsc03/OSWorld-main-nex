#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-VL-8B-Thinking}"
BASE_PORT=8000
DTYPE="bfloat16"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"

NUM_GPU_PER_MODEL=${NUM_GPU_PER_MODEL:-2}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-0}
ENFORCE_EAGER=${ENFORCE_EAGER:-0}
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${MODEL}}"

export HF_HOME="${HF_HOME:-${TRANSFORMERS_CACHE:-}}"
unset CUDA_VISIBLE_DEVICES

python - <<'PY'
import torch, sys
print(f"[check] cuda_available={torch.cuda.is_available()}, torch_cuda={getattr(torch.version,'cuda',None)}")
sys.exit(0 if torch.cuda.is_available() else 1)
PY

mkdir -p /tmp/opencua_pids

# ── 清理上一次残留的服务进程 ──────────────────────────────────────
echo "[cleanup] killing any leftover vllm processes on ports ${BASE_PORT}-$(( BASE_PORT + 7 ))..."
for pidfile in /tmp/opencua_pids/*.pid; do
  [[ -f "$pidfile" ]] || continue
  old_pid=$(cat "$pidfile" 2>/dev/null) || continue
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "  killing old pid=${old_pid} (from ${pidfile})"
    kill -9 "$old_pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done
for port in $(seq "$BASE_PORT" $(( BASE_PORT + 7 ))); do
  pids=$(ss -tlnp 2>/dev/null | awk -v p=":${port} " '$0 ~ p {while(match($0,/pid=([0-9]+)/,a)){print a[1]; $0=substr($0,RSTART+RLENGTH)}}' | sort -u)
  for pid in $pids; do
    echo "  port ${port} still held by pid=${pid}, killing..."
    kill -9 "$pid" 2>/dev/null || true
  done
done
# Remove Docker containers whose host-port mappings conflict with vLLM ports
conflicting=$(docker ps --format '{{.ID}} {{.Ports}}' 2>/dev/null | grep -E '0\.0\.0\.0:(800[0-7])->' | awk '{print $1}' || true)
if [[ -n "$conflicting" ]]; then
  echo "  removing Docker containers on ports 8000-8007: $conflicting"
  echo "$conflicting" | xargs docker rm -f 2>/dev/null || true
  sleep 1
fi
# Final pass: kill any process still holding these ports (retry up to 3 times)
for attempt in 1 2 3; do
  still_held=0
  for port in $(seq "$BASE_PORT" $(( BASE_PORT + 7 ))); do
    pids=$(ss -tlnp 2>/dev/null | awk -v p=":${port} " '$0 ~ p {while(match($0,/pid=([0-9]+)/,a)){print a[1]; $0=substr($0,RSTART+RLENGTH)}}' | sort -u)
    for pid in $pids; do
      echo "  [attempt ${attempt}] port ${port} still held by pid=${pid}, killing..."
      kill -9 "$pid" 2>/dev/null || true
      still_held=1
    done
  done
  [[ "$still_held" == "0" ]] && break
  sleep 2
done
sleep 2
echo "[cleanup] done."

GPU_COUNT=$(nvidia-smi --list-gpus | wc -l | tr -d '[:space:]')
if [[ -z "${GPU_COUNT}" || "${GPU_COUNT}" == "0" ]]; then
  echo "[fatal] no GPU visible"; exit 1
fi

if (( NUM_GPU_PER_MODEL < 1 )); then NUM_GPU_PER_MODEL=1; fi
if (( NUM_GPU_PER_MODEL > GPU_COUNT )); then NUM_GPU_PER_MODEL=${GPU_COUNT}; fi

echo "[groups] GPU_COUNT=${GPU_COUNT}, NUM_GPU_PER_MODEL=${NUM_GPU_PER_MODEL}"

GROUP_STRS=()
start=0
while (( start < GPU_COUNT )); do
  end=$(( start + NUM_GPU_PER_MODEL - 1 ))
  grp=""
  i=${start}
  while (( i < GPU_COUNT && i <= end )); do
    if [[ -z "$grp" ]]; then grp="${i}"; else grp="${grp},${i}"; fi
    i=$(( i + 1 ))
  done
  GROUP_STRS+=( "$grp" )
  start=$(( start + NUM_GPU_PER_MODEL ))
done

echo -n "[groups] GROUPS:"
for g in "${GROUP_STRS[@]}"; do echo -n " {$g}"; done
echo

PORTS=()
idx=0
for grp in "${GROUP_STRS[@]}"; do
  PORT=$(( BASE_PORT + idx ))
  PORTS+=( "$PORT" )
  LOG="/tmp/qwen3vl_${PORT}.log"
  echo "Starting GPUs {${grp}} on port ${PORT} ..."

  CUDA_VISIBLE_DEVICES="${grp}" \
  nohup vllm serve "${MODEL}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --port "${PORT}" \
    --dtype "${DTYPE}" \
    --tensor-parallel-size "${NUM_GPU_PER_MODEL}" \
    --gpu-memory-utilization "${GPU_MEM_UTIL}" \
    --trust-remote-code \
    --enforce-eager \
    $([[ "${MAX_MODEL_LEN}" != "0" ]] && echo --max-model-len "${MAX_MODEL_LEN}") \
    > "${LOG}" 2>&1 &

  echo $! > "/tmp/opencua_pids/${PORT}.pid"
  echo "  -> PID $(cat /tmp/opencua_pids/${PORT}.pid), log ${LOG}"
  idx=$(( idx + 1 ))
done

EP=""
for p in "${PORTS[@]}"; do
  [[ -z "${EP}" ]] && EP="http://127.0.0.1:${p}" || EP="${EP},http://127.0.0.1:${p}"
done

cat > .env <<EOF
OPENCUA_LOCAL_ENDPOINTS=${EP}
OPENCUA_API_KEY=dummy
EOF

echo "[start] wrote .env with endpoints:"
cat .env

echo "[start] waiting all servers to be ready..."
for p in "${PORTS[@]}"; do
  echo -n "  - port ${p} "
  ready=0
  for t in $(seq 1 600); do
    if curl -fsS --noproxy "127.0.0.1" "http://127.0.0.1:${p}/health" >/dev/null 2>/dev/null; then
      echo "READY"
      ready=1
      break
    fi
    sleep 1
  done
  if [[ $ready -eq 0 ]]; then
    echo "TIMEOUT (see /tmp/qwen3vl_${p}.log)"
    exit 1
  fi
done
echo "[start] all servers ready."
