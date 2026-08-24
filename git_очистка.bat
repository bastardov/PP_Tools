@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Разовая уборка после создания репозитория из среды без права удалять файлы.
rem Убирает служебный мусор из .git и выбрасывает старые версии иконок,
rem которые остались в базе объектов от первых коммитов (~33 МБ).

where git >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] Git не найден. Установите Git for Windows: https://git-scm.com/download/win
    pause
    exit /b 3
)

echo Размер .git до уборки:
for /f "usebackq" %%s in (`powershell -nop -c "'{0:N1} МБ' -f ((Get-ChildItem -Recurse -Force .git ^| Measure-Object Length -Sum).Sum/1MB)"`) do echo    %%s

if exist ".git\_stale" rmdir /s /q ".git\_stale"
del /q ".git\*.lock" 2>nul
del /q ".git\refs\heads\*.lock" 2>nul
del /q ".git\objects\maintenance.lock" 2>nul
for /r ".git\objects" %%f in (tmp_obj_*) do del /q "%%f" 2>nul

echo Выбрасываю недостижимые объекты...
git reflog expire --expire=now --all
git gc --prune=now --aggressive --quiet

echo.
echo Размер .git после уборки:
for /f "usebackq" %%s in (`powershell -nop -c "'{0:N1} МБ' -f ((Get-ChildItem -Recurse -Force .git ^| Measure-Object Length -Sum).Sum/1MB)"`) do echo    %%s

echo.
echo Проверяю целостность...
git fsck --no-progress

echo.
echo === Состояние репозитория ===
git log --oneline -3
git status --short
echo.
echo Готово. Этот файл больше не нужен, можно удалить.
pause
