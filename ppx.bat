@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=src"
".venv\Scripts\python.exe" -P -c "from memect.cli import main;main()" %*
endlocal
