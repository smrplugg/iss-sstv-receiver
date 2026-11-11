# -*- mode: python ; coding: utf-8 -*-
# Сборка Windows-версии: pyinstaller --noconfirm sstv.spec
# Перед сборкой Chromium нужно поставить ВНУТРЬ пакета playwright, чтобы он попал в сборку:
#   set PLAYWRIGHT_BROWSERS_PATH=0
#   python -m playwright install chromium
# Результат: dist\SSTV-MKS\SSTV.exe (+ папка _internal с библиотеками и браузером).
from PyInstaller.utils.hooks import collect_data_files

datas = [
    ("ui/world.json", "ui"), ("ui/countries.json", "ui"), ("ui/check.svg", "ui"),
    ("ui/icon.ico", "ui"), ("ui/icon.png", "ui"), ("ui/icon_1024.png", "ui"),
    ("ui/fonts", "ui/fonts"),
    ("receivers.json", "."),
]
datas += collect_data_files("skyfield")

a = Analysis(
    ["sstv.pyw"],
    pathex=["."],
    datas=datas,
    hiddenimports=["qt_app", "engine", "iss_sstv", "robot36", "combine", "redecode", "stack", "scan", "tle", "about", "paths",
                   "PyQt5.QtMultimedia", "PyQt5.QtSvg", "scipy.signal", "scipy.ndimage"],
    excludes=["tkinter", "pkg_resources", "setuptools", "matplotlib", "IPython", "pytest", "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SSTV",
    icon="ui/icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="SSTV-MKS")
