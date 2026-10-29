#!/usr/bin/env bash
# Makimono データ分析 GUI を起動する（macOS / Linux）
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt
streamlit run app.py "$@"
