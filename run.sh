#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python_cmd="${PYTHON:-python3}"
if [[ ! -x .venv/bin/python ]]; then
  "$python_cmd" -m venv .venv
fi
if ! .venv/bin/python -c 'from PySide6 import QtWidgets, QtCore; import psutil; import PIL; assert int(PIL.__version__.split(".")[0]) >= 13; assert tuple(map(int,QtCore.qVersion().split(".")[:2])) >= (6,12)' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m activity_bub "$@"
