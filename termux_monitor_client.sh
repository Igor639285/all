#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  echo "Python не найден. Установите в Termux: pkg install python"
  exit 1
fi

PY_BIN="python3"
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
  PY_BIN="python"
fi

"$PY_BIN" "$(dirname "$0")/termux_monitor_client.py" "$@"
