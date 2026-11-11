"""Создаёт ярлык «SSTV МКС» (без консоли, с иконкой) в папке программы и на рабочем столе.
Вызывается из setup.bat; можно запустить и отдельно: python make_shortcut.py"""
import base64
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "SSTV МКС"


def main():
    if sys.platform != "win32":
        print("Ярлыки создаются только в Windows. Запуск: python3 qt_app.py")
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    targets = [HERE / f"{NAME}.lnk"]
    desktop = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Desktop"
    try:
        # настоящая папка рабочего стола (может быть в OneDrive)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        if out:
            desktop = Path(out)
    except Exception:  # noqa: BLE001
        pass
    if desktop.exists():
        targets.append(desktop / f"{NAME}.lnk")
    q = lambda x: str(x).replace("'", "''")  # noqa: E731  — кавычка в пути для PowerShell
    for lnk in targets:
        ps = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}')
$s.TargetPath = '{q(pyw)}'
$s.Arguments = '"{q(HERE / "sstv.pyw")}"'
$s.WorkingDirectory = '{q(HERE)}'
$s.IconLocation = '{q(HERE / "ui" / "icon.ico")},0'
$s.Description = 'Приём SSTV с МКС через онлайн-приёмники'
$s.Save()
"""
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc], capture_output=True, text=True)
        print(("Ярлык создан: " if r.returncode == 0 else "Не удалось создать ярлык: ") + str(lnk))


if __name__ == "__main__":
    main()
