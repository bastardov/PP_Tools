@echo off
chcp 65001 >nul
setlocal

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 "%~dp0validate_project.py" %*
    exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python "%~dp0validate_project.py" %*
    exit /b %ERRORLEVEL%
)

echo [Ошибка] Python 3 не найден. Установите Python 3 или запустите проверку через Codex.
exit /b 3
