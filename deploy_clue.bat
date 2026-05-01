@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "DEPLOY_FILES="
for %%F in (
  app.yaml
  app.aix.yaml
) do (
  if exist "%%~F" set "DEPLOY_FILES=!DEPLOY_FILES! %%~F"
)

if not defined DEPLOY_FILES (
  echo No deployable App Engine YAML files found in:
  echo   %CD%
  echo Expected names include: app.yaml, dispatch.yaml, app.smoke.yaml, queue.yaml, index.yaml
  exit /b 1
)

echo Deploying from:
echo   %CD%
echo Files:
for %%F in (!DEPLOY_FILES!) do echo   %%F
echo.
call gcloud app deploy --quiet !DEPLOY_FILES!
exit /b %ERRORLEVEL%
