@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python не найден. Установи Python 3.10+ с python.org и отметь "Add python.exe to PATH". & pause & exit /b 1)
echo Устанавливаю библиотеки ...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo Ошибка установки библиотек. & pause & exit /b 1)
echo Устанавливаю браузер Chromium для Playwright ...
python -m playwright install chromium || (echo Ошибка установки Chromium. & pause & exit /b 1)
echo Создаю ярлык ...
python make_shortcut.py
echo.
echo Готово. Запускай программу ярлыком «SSTV МКС» на рабочем столе или в этой папке.
pause
