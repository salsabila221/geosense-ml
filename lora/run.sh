#!/usr/bin/env bash
# Convenience launcher for the text/CSV diagnostic reader.
# Usage: ./run.sh [port] [baud] [--log file]
set -euo pipefail
cd "$(dirname "$0")" || exit 1

if [[ -x /opt/geosense/venv/bin/python ]]; then
  PYTHON=/opt/geosense/venv/bin/python
else
  PYTHON=python3
fi

exec "$PYTHON" lora_read.py "$@"
