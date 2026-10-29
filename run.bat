@echo off
rem Makimono データ分析 GUI を起動する（Windows）
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
streamlit run app.py %*
