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

git add -A

rem ============================================================
rem  Проверка перед отправкой. Репозиторий уходит на GitHub,
rem  поэтому в него не должны попадать рабочие данные (телеметрия,
rem  логи, выгрузки моделей) и внутренние адреса. Проверяется то,
rem  что реально подготовлено к коммиту, а не вся папка.
rem  Шаблоны только латиницей - чтобы не зависеть от кодировки.
rem ============================================================
set "PP_NAMES=%TEMP%\pp_check_names.txt"
set "PP_DIFF=%TEMP%\pp_check_diff.txt"
set "PP_HITS=%TEMP%\pp_check_hits.txt"
set "PP_ADDED=%TEMP%\pp_check_added.txt"
set "PP_ALARM="

git diff --cached --name-only > "%PP_NAMES%"
findstr /I /R /C:"\.jsonl" /C:"\.log" /C:"\.csv" /C:"\.bak" /C:"\.rvt" /C:"\.nwd" /C:"^telemetry/" /C:"^_logs/" /C:"model_export" /C:"commands\.json" /C:"pp_settings\.json" "%PP_NAMES%" > "%PP_HITS%"
if not errorlevel 1 (
    echo.
    echo [ВНИМАНИЕ] В сохранение попали файлы с рабочими данными:
    type "%PP_HITS%"
    set "PP_ALARM=1"
)

rem  Смотрим только ДОБАВЛЕННЫЕ строки (+): удаление личного пути - это
rem  как раз то, что нужно сохранить, ругаться на него нечего.
rem  Строки с меткой PP_SELFCHECK - это сам список образцов ниже,
rem  иначе проверка находит сама себя при правке этого файла.
git diff --cached -U0 > "%PP_DIFF%"
findstr /R /C:"^+[^+]" "%PP_DIFF%" | findstr /V /C:"PP_SELFCHECK" > "%PP_ADDED%"
findstr /I /R /C:"RSN://" /C:"C:\\Users" /C:"\\\\[0-9]" /C:"192\.168\." /C:"@gmail" /C:"PP_SELFCHECK" "%PP_ADDED%" > "%PP_HITS%"
if not errorlevel 1 (
    echo.
    echo [ВНИМАНИЕ] В тексте правок найдены внутренние адреса или личные пути:
    type "%PP_HITS%"
    set "PP_ALARM=1"
)

del "%PP_NAMES%" "%PP_DIFF%" "%PP_ADDED%" "%PP_HITS%" >nul 2>nul

if not defined PP_ALARM goto :opisanie

echo.
echo Такое обычно не сохраняют: это уйдёт на GitHub и останется в истории.
echo Правильный путь - добавить эти файлы в .gitignore и запустить заново.
set "PP_OK="
set /p PP_OK=Всё равно сохранить? Напишите ДА (иначе Enter - отмена): 
if /I "%PP_OK%"=="ДА" goto :opisanie
if /I "%PP_OK%"=="DA" goto :opisanie
git reset >nul
echo.
echo Отменено. Ничего не сохранено и не отправлено, правки на месте.
pause
exit /b 2

:opisanie
set "MSG=%*"
if "%MSG%"=="" set /p MSG=Опишите правку одной строкой: 
if "%MSG%"=="" set "MSG=Правки без описания"

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
