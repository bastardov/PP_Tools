@echo off
chcp 65001 >nul
REM ============================================================
REM  Выгрузка логов использования PP Tools на сервер (xpenology)
REM  Копирует файлы pp_usage_YYYY-MM.jsonl из папки telemetry плагина.
REM  Запускать вручную или по расписанию (Планировщик задач Windows).
REM ============================================================

REM --- Папка с логами: telemetry внутри расширения ---
set "SRC=%APPDATA%\pyRevit-Master\extensions\PP_Tools.extension\telemetry"

REM --- Куда выгружать. ЗАМЕНИТЕ на свой путь: сетевая папка сервера или буква диска. ---
REM  Примеры:
REM    set "DST=\\server\backup\pp_usage"
REM    set "DST=Z:\pp_usage"
set "DST=\\ИЗМЕНИТЕ_МЕНЯ\pp_usage"

echo Источник: %SRC%
echo Назначение: %DST%

if not exist "%SRC%" (
    echo [Ошибка] Папка с логами не найдена: %SRC%
    exit /b 1
)

REM Текущий месяц дописывается постоянно, поэтому копируем все файлы заново:
REM robocopy сам заменит изменившиеся. /R:2 /W:5 — 2 попытки по 5 сек; /NP — без прогресса.
robocopy "%SRC%" "%DST%" pp_usage_*.jsonl /R:2 /W:5 /NP /LOG+:"%SRC%\_export.log"

REM robocopy: код 0-7 = успех, 8+ = ошибка
if %ERRORLEVEL% GEQ 8 (
    echo [Ошибка] robocopy завершился с кодом %ERRORLEVEL%. Проверьте доступ к серверу.
    exit /b %ERRORLEVEL%
)

echo Готово.
exit /b 0
