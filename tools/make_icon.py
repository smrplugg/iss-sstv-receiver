"""Иконка программы (ui/icon.ico, icon.png, icon_1024.png): python tools/make_icon.py"""
import math, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtCore import Qt, QPointF, QRectF
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

ORANGE = QtGui.QColor("#ff6a13")

def draw(S, small=False):
    img = QtGui.QImage(S, S, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    k = 1.7 if small else 1.0                     # толще линии на маленьких размерах
    # фон
    m = S * (0.03 if not small else 0.0)
    bg = QRectF(m, m, S - 2 * m, S - 2 * m)
    p.setPen(QtGui.QPen(QtGui.QColor("#303030"), max(1.0, S * 0.012)))
    p.setBrush(QtGui.QColor("#000000"))
    p.drawRoundedRect(bg, S * 0.2, S * 0.2)
    if small:
        p.setPen(Qt.NoPen)
    cx, cy, R = S * 0.5, S * 0.5, S * (0.2 if not small else 0.22)
    rx, ry, rot = S * (0.37 if not small else 0.4), S * (0.135 if not small else 0.15), -22.0

    def orbit_path(a0, a1, n=120):
        path = QtGui.QPainterPath()
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            x, y = rx * math.cos(a), ry * math.sin(a)
            c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            pt = QPointF(cx + x * c - y * s, cy + x * s + y * c)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        return path

    def orbit_pt(a):
        a = math.radians(a)
        x, y = rx * math.cos(a), ry * math.sin(a)
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        return QPointF(cx + x * c - y * s, cy + x * s + y * c)
    # задняя половина орбиты (за планетой)
    pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 110 if not small else 70), S * 0.016 * k)
    if not small:
        pen.setDashPattern([2.2, 1.6])
    pen.setCapStyle(Qt.FlatCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(orbit_path(180, 360))
    # планета со строками развёртки
    planet = QtGui.QPainterPath()
    planet.addEllipse(QPointF(cx, cy), R, R)
    p.setPen(Qt.NoPen)
    p.setBrush(QtGui.QColor("#121212" if not small else "#2a2a2a"))
    p.drawPath(planet)
    if not small:
        p.save()
        p.setClipPath(planet)
        step = S * 0.034
        y = cy - R + step * 0.6
        i = 0
        while y < cy + R:
            col = QtGui.QColor(255, 255, 255, 34 if i % 2 else 18)
            p.fillRect(QRectF(cx - R, y, 2 * R, step * 0.45), col)
            y += step
            i += 1
        p.restore()
    p.setPen(QtGui.QPen(QtGui.QColor("#8c8c8c"), S * 0.014 * k))
    p.setBrush(Qt.NoBrush)
    p.drawPath(planet)
    # передняя половина орбиты — оранжевая
    pen = QtGui.QPen(ORANGE, S * 0.032 * k)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawPath(orbit_path(0, 180))
    # станция
    st = orbit_pt(28 if not small else 32)
    p.save()
    p.translate(st)
    p.rotate(rot + 8)
    b = S * (0.066 if not small else 0.12)
    pw, ph, gap = S * (0.085 if not small else 0.075), S * (0.036 if not small else 0.06), S * 0.012
    outline = QtGui.QPen(QtGui.QColor("#000"), S * 0.012)
    p.setPen(outline)
    p.setBrush(QtGui.QColor("#ffffff"))
    p.drawRect(QRectF(-b / 2, -b / 2, b, b))
    p.drawRect(QRectF(-b / 2 - gap - pw, -ph / 2, pw, ph))
    p.drawRect(QRectF(b / 2 + gap, -ph / 2, pw, ph))
    p.restore()
    p.end()
    return img

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")
os.makedirs(out, exist_ok=True)
big = draw(1024)
big.save(os.path.join(out, "icon_1024.png"))
sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
paths = []
for s in sizes:
    im = draw(s * 4, small=s <= 32).scaled(s, s, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    pth = os.path.join(out, f"_icon_{s}.png"); im.save(pth); paths.append(pth)
draw(256).save(os.path.join(out, "icon.png"))
from PIL import Image
ims = [Image.open(x).convert("RGBA") for x in paths]
ims[-1].save(os.path.join(out, "icon.ico"), sizes=[(s, s) for s in sizes], append_images=ims[:-1])
for x in paths: os.remove(x)
# превью: крупно и маленькие размеры на тёмном и светлом
prev = Image.new("RGB", (1100, 560), "#1e1e1e")
prev.paste(Image.open(os.path.join(out, "icon_1024.png")).convert("RGBA").resize((512, 512), Image.LANCZOS), (24, 24), Image.open(os.path.join(out, "icon_1024.png")).convert("RGBA").resize((512, 512), Image.LANCZOS))
ico = Image.open(os.path.join(out, "icon.ico"))
x = 580
for s in (256, 64, 48, 32, 24, 16):
    ico.size = (s, s); im = ico.convert("RGBA") if False else Image.open(os.path.join(out, "icon.ico")).copy()
for bgc, y0 in (("#1e1e1e", 40), ("#f3f3f3", 330)):
    prev.paste(Image.new("RGB", (500, 260), bgc), (570, y0 - 20))
    x = 590
    for s in (128, 64, 48, 32, 24, 16):
        ic = Image.open(os.path.join(out, "icon.ico")); ic.size = (s, s); ic.load(); im = ic.convert("RGBA")
        prev.paste(im, (x, y0 + 40), im); x += s + 18
prev.save(os.path.join(out, "preview.png")) if "--preview" in sys.argv else None
print("ok")
