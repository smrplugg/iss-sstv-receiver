@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сборка SSTV.exe у себя (нужен Python и setup.bat). Готовая сборка — в dist\SSTV-MKS
python -m pip install pyinstaller==6.22.3 || (pause & exit /b 1)
set PLAYWRIGHT_BROWSERS_PATH=0
python -m playwright install chromium || (pause & exit /b 1)
python -m PyInstaller --noconfirm sstv.spec || (pause & exit /b 1)
echo.
echo Готово: dist\SSTV-MKS\SSTV.exe
pause
