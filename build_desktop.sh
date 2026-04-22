#!/usr/bin/env bash
set -euo pipefail

python -m pip install -U pyinstaller
pyinstaller \
  --name ContextedAI \
  --onefile \
  --windowed \
  --add-data "frontend:frontend" \
  desktop_app.py

echo "Build complete: dist/ContextedAI"
