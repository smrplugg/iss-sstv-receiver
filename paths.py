"""Где лежат файлы программы.

RES — то, что идёт вместе с программой (карта, шрифты, иконка, исходный список
приёмников). DATA — то, что программа пишет сама (настройки, записи, кэш):
рядом с SSTV.exe в собранной версии и рядом с исходниками при запуске из Python.
"""
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    DATA = Path(sys.executable).resolve().parent
    RES = Path(getattr(sys, "_MEIPASS", DATA))
else:
    DATA = RES = Path(__file__).resolve().parent
UI = RES / "ui"


def ensure_defaults():
    """Первый запуск собранной версии: положить рядом с exe исходный список приёмников."""
    dst = DATA / "receivers.json"
    src = RES / "receivers.json"
    if not dst.exists() and src.exists() and src != dst:
        try:
            shutil.copyfile(src, dst)
        except OSError:
            pass
