@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Разовая уборка после создания репозитория из среды с ограничением на удаление:
rem убирает служебный мусор из .git и ужимает репозиторий.

where git >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] Git не найден.
    pause
    exit /b 3
)

if exist ".git\_stale" (
    echo Удаляю .git\_stale ...
    rmdir /s /q ".git\_stale"
)
del /q ".git\*.lock" 2>nul
del /q ".git\objects\maintenance.lock" 2>nul
for /r ".git\objects" %%f in (tmp_obj_*) do del /q "%%f" 2>nul

echo Проверяю целостность репозитория...
git fsck --no-progress
echo Ужимаю репозиторий...
git gc --quiet --prune=now

echo.
echo === Состояние ===
git log --oneline -3
git status --short
echo.
echo Готово. Этот файл больше не нужен, можно удалить.
pause
