@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0set_clue_env.bat" (
    call "%~dp0set_clue_env.bat"
)

if not defined CLUE_ADMIN_TOKEN set "CLUE_ADMIN_TOKEN=local-admin"
if not defined CLUE_DB_PATH set "CLUE_DB_PATH=%~dp0data\clue-dev.db"
if not defined OPENAI_CLUE_PROJECT_ID set "OPENAI_CLUE_PROJECT_ID=proj_Lw53USO5NinnThSmUspUs1Kt"

if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    call "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" run.py
) else (
    call python.exe run.py
)
