@echo off
setlocal
cd /d "%~dp0"

rem PP_Tools - sohranit tekushchee sostoyanie plagina v git.
rem Zamenyaet ruchnoe kopirovanie papok v BACKUPS.

where git >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] Git не найден. Установите Git for Windows: https://git-scm.com/download/win
    pause
    exit /b 3
)

git rev-parse --git-dir >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] В этой папке нет git-репозитория.
    pause
    exit /b 3
)

echo.
echo === Что изменилось с прошлого сохранения ===
git status --short
echo.

set "MSG=%*"
if "%MSG%"=="" set /p MSG=Опишите правку одной строкой: 
if "%MSG%"=="" set "MSG=Правки без описания"

git add -A
git commit -m "%MSG%"
if errorlevel 1 (
    echo.
    echo Новых правок нет. Проверяю, всё ли отправлено на GitHub.
)

echo.
git log --oneline -1

git remote get-url origin >nul 2>nul
if not errorlevel 1 (
    echo.
    echo Отправляю на GitHub (коммиты и метки версий)...
    git push --follow-tags
    if errorlevel 1 echo [Внимание] Отправка не прошла. Локально всё сохранено, отправите позже.
)

echo.
echo Готово.
pause
