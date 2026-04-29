@echo off
setlocal
cd /d "%~dp0"
set "CLUE_ADMIN_TOKEN=local-admin"
set "CLUE_DB_PATH=%~dp0data\clue-dev.db"

if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    call "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" run.py
) else (
    call python.exe run.py
)
