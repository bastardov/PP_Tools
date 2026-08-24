@echo off
setlocal
cd /d "%~dp0"

rem Razovaya uborka posle sozdaniya repozitoriya iz sredy bez prava udalyat faily.
rem Ubiraet sluzhebnyy musor v .git i vybrasyvaet starye versii ikonok.

where git >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] Git не найден. Установите Git for Windows: https://git-scm.com/download/win
    pause
    exit /b 3
)

echo.
echo === Размер базы объектов до уборки ===
git count-objects -vH

if exist ".git\_stale" (
    echo.
    echo Удаляю служебную папку .git\_stale ...
    rmdir /s /q ".git\_stale"
)
del /q ".git\*.lock" 2>nul
del /q ".git\refs\heads\*.lock" 2>nul
del /q ".git\objects\maintenance.lock" 2>nul
for /r ".git\objects" %%f in (tmp_obj_*) do del /q "%%f" 2>nul

echo.
echo Выбрасываю недостижимые объекты, это займёт полминуты...
git reflog expire --expire=now --all
git gc --prune=now --aggressive --quiet

echo.
echo === Размер базы объектов после уборки ===
git count-objects -vH

echo.
echo === Проверка целостности ===
git fsck --no-progress

echo.
echo === Состояние репозитория ===
git log --oneline -3
git status --short

echo.
echo Готово. Этот файл больше не нужен, можно удалить.
pause
