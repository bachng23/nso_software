@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv" (
  py -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
set NSO_INTERNAL_CONSOLE=1
python -m streamlit run design_console.py
