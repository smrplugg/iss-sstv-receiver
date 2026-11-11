"""Запуск программы без окна консоли: двойной щелчок по этому файлу или по ярлыку «SSTV МКС»."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import qt_app
except Exception as e:  # noqa: BLE001  — нет библиотек: показать понятное сообщение, а не молча закрыться
    msg = (f"Не удалось запустить программу:\n{e!r}\n\n"
           "Скорее всего, не установлены библиотеки. Запусти setup.bat в папке программы.")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "SSTV МКС", 0x10)
    except Exception:  # noqa: BLE001
        print(msg)
    sys.exit(1)
qt_app.main()
