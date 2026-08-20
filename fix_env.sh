#!/usr/bin/env bash
# 修复 NumPy 1.x 兼容性:把 scipy/librosa/tifffile 降到不要求 numpy>=2 的版本
set -e
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

echo ">> 使用解释器: $PY"
"$PY" -m pip install --disable-pip-version-check \
    "scipy<1.18" "librosa<1.0" "tifffile<2026.4"

echo
echo ">> 重新自检..."
"$PY" nex_env_check.py
