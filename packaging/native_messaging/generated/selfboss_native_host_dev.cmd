@echo off
setlocal
set "SELF_BOSS_REPO_ROOT=D:\AppAntiProcrastinate"
set "SELF_BOSS_PYTHON=%SELF_BOSS_REPO_ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%SELF_BOSS_REPO_ROOT%\src;%PYTHONPATH%"
if exist "%SELF_BOSS_PYTHON%" (
  "%SELF_BOSS_PYTHON%" -m selfboss_native_host %*
) else (
  python -m selfboss_native_host %*
)
