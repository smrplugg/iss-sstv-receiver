"""Окно программы приёма SSTV на Qt5. Запуск: run_gui.bat (или python qt_app.py).

Ядро приёма (engine.py) работает в фоновом потоке; окно читает его состояние
по таймеру и отдаёт команды через engine.cmd(). Звук приёмников играется
через QtMultimedia: общая громкость и своя у каждого приёмника.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

try:
    from PyQt5.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioOutput
    HAVE_AUDIO = True
except Exception:  # noqa: BLE001
    HAVE_AUDIO = False

from paths import DATA as HERE, UI as UI_DIR  # noqa: E402
STATE_FILE = HERE / "ui_state.json"

# «Пульт»: чёрный, серый, белый; оранжевый — только то, что идёт сейчас; красный — ошибка
C = dict(bg="#000000", panel="#060606", panel2="#0d0d0d", line="#1f1f1f", text="#f2f2f2", muted="#8c8c8c",
         faint="#4d4d4d", accent="#ffffff", live="#ff6a13", ok="#f2f2f2", warn="#ffb020", dimwarn="#8c8c8c",
         idle="#3a3a3a", bad="#ff3b30", off="#1a1a1a", green="#30d158", sea="#000000", land="#101010",
         coast="#5a5a5a", border="#2c2c2c", grat="#111111")
HEAD, MONO, BODY = "PT Sans Narrow", "JetBrains Mono", "Segoe UI"


def load_fonts():
    global BODY
    for f in (UI_DIR / "fonts").glob("*.ttf"):
        QtGui.QFontDatabase.addApplicationFont(str(f))
    if "Segoe UI" not in QtGui.QFontDatabase().families():
        BODY = HEAD


def hfont(px=12, bold=True, spacing=2.0):
    """Заголовочный шрифт: узкий, прописными, вразрядку."""
    f = QtGui.QFont(HEAD)
    f.setPixelSize(px)
    f.setBold(bold)
    f.setCapitalization(QtGui.QFont.AllUppercase)
    f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, spacing)
    return f


def mfont(px=12):
    f = QtGui.QFont(MONO)
    f.setPixelSize(px)
    return f
Q = {k: QtGui.QColor(v) for k, v in C.items()}
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def tm(t, sec=False):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S" if sec else "%H:%M")


def dm(t):
    return datetime.fromtimestamp(t).strftime("%d.%m")


def wd(t):
    return WD[datetime.fromtimestamp(t).weekday()]


def dur(s):
    s = max(0, int(round(s)))
    h, r = divmod(s, 3600)
    m, x = divmod(r, 60)
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m:02d}:{x:02d}"


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ---------------- фон: долгие вычисления не в потоке окна ----------------
class _Sig(QtCore.QObject):
    done = QtCore.pyqtSignal(object, object)


def run_bg(fn, cb):
    sig = _Sig()
    sig.done.connect(lambda res, s=sig: cb(res))

    def work():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            res = e
        sig.done.emit(res, None)
    run_bg._keep = getattr(run_bg, "_keep", [])[-20:] + [sig]
    threading.Thread(target=work, daemon=True).start()


# ---------------- раскладка «по строкам с переносом» ----------------
class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):  # noqa: N802
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):  # noqa: N802
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, r):  # noqa: N802
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QtCore.QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, r, test):
        x, y, lh, sp = r.x(), r.y(), 0, self.spacing()
        for it in self.items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and lh > 0:
                x, y, lh = r.x(), y + lh + sp, 0
            if not test:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + sp
            lh = max(lh, hint.height())
        return y + lh - r.y()


# ---------------- карта ----------------
def merc_v(lat):
    lat = math.radians(max(-85.0, min(85.0, lat)))
    return (1 - math.log(math.tan(math.pi / 4 + lat / 2)) / math.pi) / 2


def inv_v(v):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * v))))


def proj(lon, lat):
    return (lon + 180) / 360, merc_v(lat)


class World:
    land = lakes = borders = grat = None

    @classmethod
    def load(cls):
        if cls.land is not None:
            return
        d = json.loads((UI_DIR / "world.json").read_text(encoding="utf-8"))
        sc = d["scale"]

        def mk(rings, close):
            p = QtGui.QPainterPath()
            p.setFillRule(Qt.WindingFill)
            for r in rings:
                a = np.asarray(r, np.float64).reshape(-1, 2) / sc
                u = (a[:, 0] + 180) / 360
                lat = np.radians(np.clip(a[:, 1], -85, 85))
                v = (1 - np.log(np.tan(np.pi / 4 + lat / 2)) / np.pi) / 2
                poly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in zip(u, v)])
                if close:
                    p.addPolygon(poly)
                    p.closeSubpath()
                else:
                    p.moveTo(poly[0])
                    for q in list(poly)[1:]:
                        p.lineTo(q)
            return p
        cls.land = mk(d["land"], True)
        cls.lakes = mk(d["lakes"], True)
        cls.borders = mk(d["borders"], False)
        g = QtGui.QPainterPath()
        for lon in range(-180, 181, 30):
            g.moveTo((lon + 180) / 360, merc_v(85))
            g.lineTo((lon + 180) / 360, merc_v(-85))
        for lat in range(-60, 61, 30):
            g.moveTo(0, merc_v(lat))
            g.lineTo(1, merc_v(lat))
        cls.grat = g


def small_circle(lat, lon, rho, n=120):
    f, l0 = math.radians(lat), math.radians(lon)
    out = []
    for i in range(n + 1):
        b = 2 * math.pi * i / n
        la = math.asin(math.sin(f) * math.cos(rho) + math.cos(f) * math.sin(rho) * math.cos(b))
        lo = l0 + math.atan2(math.sin(b) * math.sin(rho) * math.cos(f), math.cos(rho) - math.sin(f) * math.sin(la))
        out.append((math.degrees(la), (math.degrees(lo) + 540) % 360 - 180))
    return out


def foot_radius(alt_km, el_deg):
    R, e = 6371.0, math.radians(el_deg)
    return math.acos(R / (R + alt_km) * math.cos(e)) - e


class MapWidget(QtWidgets.QWidget):
    clicked = QtCore.pyqtSignal(object, float, float)      # данные точки (или None), lon, lat
    followOff = QtCore.pyqtSignal()

    def __init__(self, parent=None, small=False):
        super().__init__(parent)
        World.load()
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)
        self.u, self.v, self.s = 0.5, 0.42, 0.0
        self.points: list[dict] = []
        self.sat = None
        self.track = None
        self.footprints = []
        self.marker = None
        self.home_pt = None       # (lon, lat, подпись) — моё место, зелёная точка
        self.foot = None          # (высота км, мин. угол) — зона видимости рисуется от плавной позиции
        self._sat_prev = None
        self._sat_v = (0.0, 0.0)
        self._anim = QtCore.QTimer(self, interval=33)
        self._anim.timeout.connect(self._animate)
        self.follow = False
        self.hover = None
        self.small = small
        self._drag = None
        self.setCursor(Qt.CrossCursor if small else Qt.OpenHandCursor)

    # --- спутник: позиция между опросами ядра достраивается по скорости ---
    def set_sat(self, lat, lon, label, t):
        prev = self._sat_prev
        if prev and t > prev[0]:
            dt = t - prev[0]
            dlon = (lon - prev[2] + 540) % 360 - 180
            self._sat_v = ((lat - prev[1]) / dt, dlon / dt)
        if not prev or t != prev[0]:
            self._sat_prev = (t, lat, lon)
        self.sat = dict(lat=lat, lon=lon, label=label)
        if not self._anim.isActive():
            self._anim.start()

    def sat_pos(self):
        if not self._sat_prev:
            return None
        t, lat, lon = self._sat_prev
        dt = min(3.0, max(0.0, time.time() - t))
        return (max(-89.9, min(89.9, lat + self._sat_v[0] * dt)),
                (lon + self._sat_v[1] * dt + 540) % 360 - 180)

    def _animate(self):
        if not self.isVisible() or not self.sat:
            return
        if self.follow and self._drag is None:
            lat, lon = self.sat_pos()
            u, v = proj(lon, lat)
            # кратчайший путь по долготе, чтобы не прыгать на линии перемены дат
            self.u += ((u - self.u + 0.5) % 1.0) - 0.5
            self.v = v
            self.clamp()
        self.update()

    # --- геометрия ---
    def home(self):
        self.s = max(self.width(), self.height() * 1.6)
        self.u, self.v = 0.5, 0.42
        self.clamp()
        self.update()

    def min_s(self):
        return max(self.width(), self.height()) * 0.9

    def clamp(self):
        self.s = max(self.min_s(), min(self.width() * 400, self.s))
        hv = self.height() / 2 / self.s
        v0, v1 = merc_v(85), merc_v(-85)
        self.v = 0.5 if (v1 - v0) < 2 * hv else max(v0 + hv, min(v1 - hv, self.v))
        self.u %= 1.0

    def resizeEvent(self, e):  # noqa: N802
        if not self.s:
            self.home()
        self.clamp()

    def to_screen(self, u, v):
        return (u - self.u) * self.s + self.width() / 2, (v - self.v) * self.s + self.height() / 2

    def to_world(self, x, y):
        return (x - self.width() / 2) / self.s + self.u, (y - self.height() / 2) / self.s + self.v

    def lonlat_at(self, x, y):
        u, v = self.to_world(x, y)
        return ((u * 360) % 360) - 180, inv_v(v)

    def zoom_at(self, x, y, k):
        u0, v0 = self.to_world(x, y)
        self.s *= k
        self.clamp()
        u1, v1 = self.to_world(x, y)
        self.u += u0 - u1
        self.v += v0 - v1
        self.clamp()
        self.update()

    def center(self, lon, lat, min_zoom=None):
        if min_zoom and self.s < self.width() * min_zoom:
            self.s = self.width() * min_zoom
        self.u, self.v = proj(lon, lat)
        self.clamp()
        self.update()

    def copies(self, extra=False):
        uL = self.u - self.width() / 2 / self.s
        uR = self.u + self.width() / 2 / self.s
        rng = range(math.floor(uL) - 1, math.ceil(uR) + 1)
        return list(rng) if extra else [k for k in rng if k + 1 > uL and k < uR]

    def hit(self, x, y):
        best, bd = None, 81
        for p in self.points:
            u, v = proj(p["lon"], p["lat"])
            for k in self.copies():
                sx, sy = self.to_screen(u + k, v)
                d = (sx - x) ** 2 + (sy - y) ** 2
                if d < bd:
                    bd, best = d, p
        return best

    # --- мышь ---
    def wheelEvent(self, e):  # noqa: N802
        self.zoom_at(e.pos().x(), e.pos().y(), math.exp(e.angleDelta().y() * 0.0015))

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._drag = dict(x=e.pos().x(), y=e.pos().y(), u=self.u, v=self.v, moved=False)

    def mouseMoveEvent(self, e):  # noqa: N802
        x, y = e.pos().x(), e.pos().y()
        d = self._drag
        if d:
            dx, dy = x - d["x"], y - d["y"]
            if abs(dx) + abs(dy) > 3:
                d["moved"] = True
                self.setCursor(Qt.ClosedHandCursor)
            if d["moved"]:
                self.u, self.v = d["u"] - dx / self.s, d["v"] - dy / self.s
                if self.follow:
                    self.follow = False
                    self.followOff.emit()
                self.clamp()
                self.update()
            return
        h = self.hit(x, y)
        if h is not self.hover:
            self.hover = h
            self.update()
        if h:
            QtWidgets.QToolTip.showText(e.globalPos(), h.get("tip", ""), self)
        else:
            QtWidgets.QToolTip.hideText()

    def mouseReleaseEvent(self, e):  # noqa: N802
        d, self._drag = self._drag, None
        self.setCursor(Qt.CrossCursor if self.small else Qt.OpenHandCursor)
        if d and not d["moved"]:
            x, y = e.pos().x(), e.pos().y()
            lon, lat = self.lonlat_at(x, y)
            h = self.hit(x, y)
            self.clicked.emit(h["data"] if h else None, lon, lat)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.zoom_at(e.pos().x(), e.pos().y(), 2)

    def leaveEvent(self, e):  # noqa: N802
        if self.hover:
            self.hover = None
            self.update()

    # --- рисование ---
    def _line(self, p, pts, k):
        """Ломаная по [lat, lon]. Долгота «разворачивается», поэтому на линии перемены дат
        линия не рвётся, а уходит в соседнюю копию мира (её дорисовывает соседний k)."""
        path = QtGui.QPainterPath()
        pu, off = None, 0.0
        for lat, lon in pts:
            u, v = proj(lon, lat)
            if pu is not None:
                if u + off - pu > 0.5:
                    off -= 1.0
                elif u + off - pu < -0.5:
                    off += 1.0
            u += off
            x, y = self.to_screen(u + k, v)
            if pu is None:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            pu = u
        p.drawPath(path)

    # подложка (суша, границы, сетка) рисуется один раз в картинку с запасом по краям;
    # при перетаскивании и зуме только сдвигается/масштабируется, перерисовка — после паузы
    # или когда запас по краю кончается. Суша заливается без сглаживания (сглаженная
    # заливка в 20–30 раз дороже, а край всё равно закрывает сглаженная линия берега):
    # так перерисовка занимает ~20 мс, и карта не замирает при ведении спутника.
    def _base_params(self):
        return dict(u=self.u, v=self.v, s=self.s, W=self.width(), H=self.height(),
                    dpr=self.devicePixelRatioF())

    @staticmethod
    def _draw_base(u, v, s, W, H, dpr):
        W2, H2 = W * 2, H * 2
        img = QtGui.QImage(max(1, int(W2 * dpr)), max(1, int(H2 * dpr)), QtGui.QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Q["sea"])
        p = QtGui.QPainter(img)

        def cpen(col, w=1.0):
            pen = QtGui.QPen(QtGui.QColor(col), w)
            pen.setCosmetic(True)
            return pen
        uL, uR = u - W2 / 2 / s, u + W2 / 2 / s
        vT, vB = v - H2 / 2 / s, v + H2 / 2 / s
        clip = QtCore.QRectF(0, vT, 1, vB - vT)
        for k in range(math.floor(uL) - 1, math.ceil(uR) + 1):
            if not (k + 1 > uL and k < uR):
                continue
            ox = (k - u) * s + W2 / 2
            oy = -v * s + H2 / 2
            p.setTransform(QtGui.QTransform(s, 0, 0, s, ox, oy))
            # видимая часть этой копии мира в её координатах: остальное не обсчитываем
            vis = clip.intersected(QtCore.QRectF(uL - k, vT, uR - uL, vB - vT))
            p.setClipRect(vis)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.strokePath(World.grat, cpen(C["grat"]))
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            p.fillPath(World.land, Q["land"])
            p.fillPath(World.lakes, Q["sea"])
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.strokePath(World.land, cpen(C["coast"], 0.8))
            if s > W * 1.6:
                p.strokePath(World.borders, cpen(C["border"], 0.7))
        p.end()
        return img

    def _render_base(self):
        """Сразу, в главном потоке — только когда показывать нечего."""
        pr = self._base_params()
        self._base = (self._draw_base(**pr), pr["u"], pr["v"], pr["s"], pr["W"], pr["H"], pr["dpr"])

    def _render_base_async(self):
        """Перерисовать на следующем витке цикла событий (не внутри paintEvent)."""
        if getattr(self, "_base_pending", False):
            return
        self._base_pending = True

        def go():
            self._base_pending = False
            self._render_base()
            self.update()
        QtCore.QTimer.singleShot(0, go)

    def _base_timer(self):
        if not hasattr(self, "_bt"):
            self._bt = QtCore.QTimer(self, singleShot=True, interval=140)
            self._bt.timeout.connect(self._render_base_async)
        self._bt.start()

    def showEvent(self, e):  # noqa: N802
        # окно могло поменять размер или масштаб экрана, пока карта была скрыта
        self._base = None
        super().showEvent(e)

    def paintEvent(self, e):  # noqa: N802
        if not self.s:
            self.home()
        W, H = self.width(), self.height()
        b = getattr(self, "_base", None)
        if b is None or b[4] != W or b[5] != H or b[6] != self.devicePixelRatioF():
            self._render_base()
            b = self._base
        img, bu, bv, bs = b[:4]
        du = self.u - bu
        du -= round(du)                       # перенос через линию перемены дат
        dx, dy = abs(du * self.s), abs((self.v - bv) * self.s)
        if bs != self.s:
            self._base_timer()
        elif dx > W * 0.45 or dy > H * 0.45:
            self._render_base()               # запас кончился, фон не успел — рисуем сразу
            img, bu, bv, bs = self._base[:4]
            du = 0.0
        elif dx > W / 5 or dy > H / 5:
            self._render_base_async()         # заранее, пока до края запаса далеко
        elif (bu, bv) != (self.u, self.v) and not self.follow:
            self._base_timer()
        k = self.s / bs
        x0 = W / 2 - (du * self.s) - W * k
        y0 = H / 2 - ((self.v - bv) * self.s) - H * k
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), Q["sea"])
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, k != 1)
        p.drawImage(QtCore.QRectF(x0, y0, 2 * W * k, 2 * H * k), img)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        W, H = self.width(), self.height()
        cop = self.copies()
        p.setBrush(Qt.NoBrush)
        sp = self.sat_pos()
        if sp and self.foot:
            self.footprints = [
                dict(pts=small_circle(sp[0], sp[1], foot_radius(self.foot[0], 0)),
                     color=QtGui.QColor(255, 255, 255, 70), dash=True),
                dict(pts=small_circle(sp[0], sp[1], foot_radius(self.foot[0], self.foot[1])),
                     color=QtGui.QColor(255, 106, 19, 200), width=1.4)]
        for k in self.copies(extra=True):
            for fp in self.footprints:
                pen = QtGui.QPen(fp["color"], fp.get("width", 1.2))
                if fp.get("dash"):
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                self._line(p, fp["pts"], k)
            if self.track:
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 110), 1.3, Qt.DashLine)
                p.setPen(pen)
                self._line(p, self.track["past"], k)
                p.setPen(QtGui.QPen(QtGui.QColor(255, 106, 19, 220), 1.6))
                self._line(p, self.track["future"], k)
        # приёмники
        r0 = 5 if self.s > W * 3 else 4
        pts = sorted(self.points, key=lambda q: q.get("z", 0))
        f = mfont(10)
        p.setFont(f)
        fm = QtGui.QFontMetrics(f)
        boxes = []
        for k in cop:
            drawn = []
            for q in pts:
                u, v = proj(q["lon"], q["lat"])
                x, y = self.to_screen(u + k, v)
                if x < -20 or y < -20 or x > W + 20 or y > H + 20:
                    continue
                r = r0 + (2 if q is self.hover else 0)
                if q.get("ring"):
                    p.setBrush(Qt.NoBrush)
                    col = QtGui.QColor(q.get("ringColor") or q["color"])
                    col.setAlpha(180)
                    p.setPen(QtGui.QPen(col, 2))
                    rr = r + 4 + q["ring"] * 5
                    p.drawEllipse(QtCore.QPointF(x, y), rr, rr)
                p.setBrush(QtGui.QColor(q["color"]))
                p.setPen(QtGui.QPen(Q["live"] if q.get("selected") else QtGui.QColor(0, 0, 0, 200),
                                    2.5 if q.get("selected") else 1))
                p.drawEllipse(QtCore.QPointF(x, y), r, r)
                drawn.append((q, x, y, r))
            for q, x, y, r in reversed(drawn):
                if not q.get("label") or not (self.s > W * 2.5 or q.get("always")):
                    continue
                w = fm.horizontalAdvance(q["label"])
                bx, by = x + r + 3, y - 7
                if not q.get("always") and any(bx < b[0] + b[2] and bx + w > b[0] and by < b[1] + 13 and by + 13 > b[1]
                                               for b in boxes):
                    continue
                boxes.append((bx, by, w))
                p.setPen(Q["live"] if q.get("always") else QtGui.QColor(200, 200, 200, 190))
                p.drawText(QtCore.QPointF(bx, y + 4), q["label"])
            if self.home_pt:
                u, v = proj(self.home_pt[0], self.home_pt[1])
                x, y = self.to_screen(u + k, v)
                p.setPen(Qt.NoPen)
                p.setBrush(QtGui.QColor(48, 209, 88, 55))
                p.drawEllipse(QtCore.QPointF(x, y), 11, 11)
                p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.5))
                p.setBrush(QtGui.QColor(C["green"]))
                p.drawEllipse(QtCore.QPointF(x, y), 5.5, 5.5)
                p.setFont(hfont(12, True, 1.5))
                p.setPen(QtGui.QColor(C["green"]))
                p.drawText(QtCore.QPointF(x + 10, y + 4), self.home_pt[2])
                p.setFont(f)
            if self.marker:
                u, v = proj(self.marker[0], self.marker[1])
                x, y = self.to_screen(u + k, v)
                p.setPen(QtGui.QPen(Q["live"], 2))
                p.setBrush(Qt.NoBrush)
                p.drawLine(QtCore.QPointF(x - 9, y), QtCore.QPointF(x + 9, y))
                p.drawLine(QtCore.QPointF(x, y - 9), QtCore.QPointF(x, y + 9))
                p.drawEllipse(QtCore.QPointF(x, y), 5, 5)
            if self.sat and sp:
                u, v = proj(sp[1], sp[0])
                x, y = self.to_screen(u + k, v)
                p.setPen(Qt.NoPen)
                p.setBrush(QtGui.QColor(255, 106, 19, 60))
                p.drawEllipse(QtCore.QPointF(x, y), 11, 11)
                p.setPen(QtGui.QPen(QtGui.QColor("#000"), 1.5))
                p.setBrush(QtGui.QColor("#ffffff"))
                p.drawRect(QtCore.QRectF(x - 4, y - 4, 8, 8))
                p.fillRect(QtCore.QRectF(x - 15, y - 1.5, 9, 3), QtGui.QColor("#ffffff"))
                p.fillRect(QtCore.QRectF(x + 6, y - 1.5, 9, 3), QtGui.QColor("#ffffff"))
                p.setFont(hfont(12, True, 1.5))
                p.setPen(QtGui.QColor("#ffffff"))
                p.drawText(QtCore.QPointF(x + 12, y - 8), self.sat.get("label", ""))
                p.setFont(f)
        p.end()


# ---------------- звук ----------------
class Mixer:
    """Смешивает звук выбранных приёмников (12 кГц) и играет через QAudioOutput."""
    PRE = 4800        # 0.4 с запаса перед началом воспроизведения

    def __init__(self, engine):
        self.E = engine
        self.ok = False
        self.seq = -1
        self.fifo: dict[str, np.ndarray] = {}
        self.primed: dict[str, bool] = {}
        self.float = False
        self.err_logged = False
        if not HAVE_AUDIO:
            self.note = "Звук: модуль QtMultimedia не загрузился, звука в программе не будет."
        else:
            try:
                self.note = self._open()
            except Exception as e:  # noqa: BLE001
                self.note = f"Звук недоступен: {e}"
        try:
            engine.log(self.note)
        except Exception:  # noqa: BLE001
            pass

    def _devices(self):
        """Сначала устройство по умолчанию через waveOut (плагин windows) — он принимает почти любой
        формат; затем то же устройство через WASAPI; затем остальные."""
        d0 = QAudioDeviceInfo.defaultOutputDevice()
        name = "" if d0.isNull() else d0.deviceName()
        allv = list(QAudioDeviceInfo.availableDevices(QAudio.AudioOutput))
        same = [d for d in allv if d.deviceName() == name]
        same.sort(key=lambda d: 0 if d.realm() == "windows" else 1)
        rest = [d for d in allv if d.deviceName() != name]
        return ([] if d0.isNull() else same + [d0]) + rest

    def _fmt(self, rate, ch, flt):
        f = QAudioFormat()
        f.setSampleRate(rate)
        f.setChannelCount(ch)
        f.setSampleSize(32 if flt else 16)
        f.setCodec("audio/pcm")
        f.setByteOrder(QAudioFormat.LittleEndian)
        f.setSampleType(QAudioFormat.Float if flt else QAudioFormat.SignedInt)
        return f

    def _try(self, dev, fmt):
        out = QAudioOutput(dev, fmt)
        bpf = fmt.sampleSize() // 8 * fmt.channelCount()
        out.setBufferSize(int(fmt.sampleRate() * 0.35) * bpf)
        io = out.start()
        if io is None or out.error() != QAudio.NoError:
            out.stop()
            return False
        self.out, self.io = out, io
        self.rate, self.ch = fmt.sampleRate(), fmt.channelCount()
        self.float, self.bpf = fmt.sampleType() == QAudioFormat.Float, bpf
        self.ok = True
        return True

    def _open(self):
        devs = self._devices()
        if not devs:
            return "Звук недоступен: Windows не сообщает ни одного устройства вывода."
        tried = []
        for dev in devs:
            if dev.isNull():
                continue
            cands = [self._fmt(r, c, fl) for fl in (False, True) for r in (48000, 44100) for c in (1, 2)]
            cands = [f for f in cands if dev.isFormatSupported(f)]
            near = dev.nearestFormat(self._fmt(48000, 1, False))
            if (near.sampleType(), near.sampleSize()) in ((QAudioFormat.SignedInt, 16), (QAudioFormat.Float, 32)):
                cands.append(near)
            for f in cands:
                if self._try(dev, f):
                    return (f"Звук: {dev.deviceName()} ({dev.realm()}), {f.sampleRate()} Гц, "
                            f"{f.channelCount()} кан., {'float' if self.float else '16 бит'}.")
            tried.append(f"{dev.deviceName()} ({dev.realm()})")
        return "Звук недоступен: ни одно устройство не открылось — " + "; ".join(tried[:4])

    def tick(self, keys, gains, master):
        if not self.ok:
            return
        if not keys or master <= 0:
            self.seq = -1
            self.fifo.clear()
            self.primed.clear()
        else:
            seq, data = self.E.audio_since(self.seq, keys)
            first = self.seq < 0
            self.seq = seq
            if not first:
                import base64
                for k, b64 in data.items():
                    a = np.frombuffer(base64.b64decode(b64), "<i2").astype(np.float32) / 32768
                    f = self.fifo.get(k)
                    f = a if f is None else np.concatenate([f, a])
                    if len(f) > 12000 * 3:                  # отстали — не копим задержку
                        f = f[-6000:]
                    self.fifo[k] = f
            for k in list(self.fifo):
                if k not in keys:
                    self.fifo.pop(k)
                    self.primed.pop(k, None)
        if self.out.state() == QAudio.StoppedState and self.out.error() != QAudio.NoError:
            if not self.err_logged:
                self.err_logged = True
                self.E.log(f"Звук: вывод остановился (ошибка {int(self.out.error())}), перезапускаю.")
            self.io = self.out.start()
            if self.io is None:
                return
        frames = self.out.bytesFree() // self.bpf
        if frames < self.rate // 50:
            return
        n12 = max(1, frames * 12000 // self.rate)
        mix = np.zeros(n12, np.float32)
        for k, f in list(self.fifo.items()):
            if not self.primed.get(k):
                if len(f) < self.PRE:
                    continue
                self.primed[k] = True
            take = f[:n12]
            mix[:len(take)] += take * gains.get(k, 1.0)
            rest = f[n12:]
            self.fifo[k] = rest
            if not len(rest):
                self.primed[k] = False
        mix *= master
        y = np.interp(np.arange(frames) * (12000 / self.rate), np.arange(n12), mix)
        y = np.clip(y, -1, 1)
        y = y.astype("<f4") if self.float else (y * 32767).astype("<i2")
        if self.ch > 1:
            y = np.repeat(y, self.ch)
        self.io.write(y.tobytes())


# ---------------- мелкие виджеты ----------------
def bar(pct, color):
    pb = QtWidgets.QProgressBar()
    pb.setRange(0, 100)
    pb.setTextVisible(False)
    pb.setFixedHeight(8)
    pb.setValue(int(pct))
    pb.setFixedHeight(6)
    pb.setStyleSheet(f"QProgressBar{{background:#141414;border:none;border-radius:0}}"
                     f"QProgressBar::chunk{{background:{color};border-radius:0}}")
    return pb


def qcolor_for(p):
    return "#ffffff" if p >= 60 else C["warn"] if p >= 30 else C["live"] if p > 0 else "#333333"


class RxCard(QtWidgets.QFrame):
    def __init__(self, win, a):
        super().__init__()
        self.win, self.key = win, a["key"]
        self.setObjectName("card")
        self.setFixedWidth(330)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(4)
        hd = QtWidgets.QHBoxLayout()
        hd.setContentsMargins(10, 7, 10, 0)
        self.nm = QtWidgets.QLabel()
        self.nm.setFont(hfont(14, True, 1.8))
        self.full = QtWidgets.QLabel()
        self.full.setStyleSheet(f"color:{C['muted']};font-size:11px")
        hd.addWidget(self.nm)
        hd.addWidget(self.full, 1)
        lay.addLayout(hd)
        self.pic = QtWidgets.QLabel("картинка появится, когда пойдёт SSTV")
        self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setFixedSize(330, 248)
        self.pic.setStyleSheet("background:#050505;color:#3a3a3a;font-size:11px")
        lay.addWidget(self.pic)
        self.wf = Waterfall(330, 60)
        lay.addWidget(self.wf)
        self.st = QtWidgets.QLabel(self.pic)
        self.st.move(6, 6)
        self.st.setStyleSheet("background:rgba(0,0,0,190);padding:1px 6px;color:#f2f2f2;font-size:11px")
        self.cnt = QtWidgets.QLabel(self.pic)
        self.cnt.setStyleSheet(self.st.styleSheet())
        self.m = QtWidgets.QLabel()
        self.m.setStyleSheet(f"font-family:'{MONO}';font-size:12px;padding:2px 10px")
        lay.addWidget(self.m)
        g = QtWidgets.QGridLayout()
        g.setContentsMargins(10, 0, 10, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(3)
        self.qbar, self.lbar = bar(0, "#ffffff"), bar(0, "#6a6a6a")
        self.qv, self.lv = QtWidgets.QLabel(), QtWidgets.QLabel()
        for i, (lb, b, v) in enumerate((("SSTV", self.qbar, self.qv), ("уровень", self.lbar, self.lv))):
            ll = QtWidgets.QLabel(lb)
            ll.setFont(hfont(10, True, 1.5))
            ll.setStyleSheet(f"color:{C['muted']}")
            ll.setFixedWidth(52)
            v.setFixedWidth(40)
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v.setStyleSheet(f"font-family:'{MONO}';font-size:12px")
            g.addWidget(ll, i, 0)
            g.addWidget(b, i, 1)
            g.addWidget(v, i, 2)
        lay.addLayout(g)
        snd = QtWidgets.QHBoxLayout()
        snd.setContentsMargins(10, 2, 10, 0)
        self.btn = QtWidgets.QToolButton()
        self.btn.setCheckable(True)
        self.btn.setFixedWidth(34)
        self.vol = QtWidgets.QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        snd.addWidget(self.btn)
        snd.addWidget(self.vol, 1)
        lay.addLayout(snd)
        s = win.snd(self.key)
        self.btn.setChecked(s["on"])
        self.vol.setValue(s["v"])
        self.btn.toggled.connect(self._toggle)
        self.vol.valueChanged.connect(self._vol)
        self.ver = -1
        self._q = self._l = None

    def _toggle(self, on):
        self.win.snd(self.key)["on"] = on
        self.win.save_state()
        self.win.render_cards()

    def _vol(self, v):
        self.win.snd(self.key)["v"] = v
        self.win.save_state()

    def update_from(self, a, hearing):
        self.nm.setText(a["short"])
        self.full.setText(a["name"])
        self.full.setToolTip(a["url"])
        self.st.setText(a["state"])
        self.st.adjustSize()
        self.cnt.setText(f"снимков {a['imgs']}")
        self.cnt.adjustSize()
        self.cnt.move(self.pic.width() - self.cnt.width() - 6, 6)
        self.cnt.setVisible(bool(a["imgs"]))
        d = a["dop"]
        k = f"color:{C['muted']};font-family:'{HEAD}';font-size:11px"
        self.m.setText(f"<span style='{k}'>УГОЛ</span> {a['el']:.0f}°&nbsp;&nbsp;&nbsp;"
                       f"<span style='{k}'>АЗИМУТ</span> {a['az']:.0f}°&nbsp;&nbsp;&nbsp;"
                       f"<span style='{k}'>ДОПЛЕР</span> {'+' if d > 0 else ''}{d / 1000:.1f}к")
        q = a["strength"]
        self.qbar.setValue(int(q))
        col = qcolor_for(q)
        if col != self._q:
            self._q = col
            self.qbar.setStyleSheet(f"QProgressBar{{background:#141414;border:none;border-radius:0}}"
                                    f"QProgressBar::chunk{{background:{col};border-radius:0}}")
        self.qv.setText(f"{q:.0f}%")
        lv = a["level"]
        self.lbar.setValue(0 if lv is None else int(max(0, min(100, (lv + 60) / 60 * 100))))
        self.lv.setText("—" if lv is None else f"{lv:.0f}")
        on = self.btn.isChecked()
        self.btn.setText("🔊" if on else ("🔉" if hearing else "🔈"))
        self.btn.setToolTip("Звук включён" if on else "Играет как «лучший» сигнал" if hearing else "Звук выключен")
        self.setProperty("hear", hearing)
        if hearing != getattr(self, "_hear", None):
            self._hear = hearing
            self.setStyleSheet(f"#card{{background:{C['panel']};border:1px solid "
                               f"{C['live'] if hearing else C['line']};border-radius:2px}}")
        self.wf.set_rows(self.win.E.waterfall(self.key))
        if a["has_img"] and a["img_ver"] != self.ver:
            self.ver = a["img_ver"]
            b = self.win.E.live_png(self.key)
            if b:
                pm = QtGui.QPixmap()
                pm.loadFromData(b)
                self.pic.setPixmap(pm.scaled(self.pic.size(), Qt.KeepAspectRatio, Qt.FastTransformation))


class PolarPlot(QtWidgets.QWidget):
    """Небо над приёмником: центр — зенит, край — горизонт, север сверху."""

    def __init__(self, size=220):
        super().__init__()
        self.setFixedSize(size, size + 18)
        self.tr = None
        self.now = time.time()

    def set_track(self, tr):
        self.tr = tr if isinstance(tr, dict) else None
        self.update()

    def _xy(self, az, el, cx, cy, R):
        r = R * (90 - max(0.0, el)) / 90
        a = math.radians(az)
        return cx + r * math.sin(a), cy - r * math.cos(a)

    def paintEvent(self, e):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        W = self.width()
        cx, cy, R = W / 2, W / 2, W / 2 - 16
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for el in (0, 30, 60):
            r = R * (90 - el) / 90
            p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        p.drawLine(QtCore.QPointF(cx - R, cy), QtCore.QPointF(cx + R, cy))
        p.drawLine(QtCore.QPointF(cx, cy - R), QtCore.QPointF(cx, cy + R))
        p.setFont(hfont(10, True, 1))
        p.setPen(Q["muted"])
        for t, (dx, dy) in (("С", (0, -1)), ("В", (1, 0)), ("Ю", (0, 1)), ("З", (-1, 0))):
            p.drawText(QtCore.QRectF(cx + dx * (R + 9) - 8, cy + dy * (R + 9) - 8, 16, 16), Qt.AlignCenter, t)
        p.setFont(mfont(9))
        p.drawText(QtCore.QPointF(cx + 3, cy - R * 2 / 3 + 11), "30°")
        p.drawText(QtCore.QPointF(cx + 3, cy - R / 3 + 11), "60°")
        tr = self.tr
        if tr:
            me = tr.get("min_el", 10)
            r = R * (90 - me) / 90
            p.setPen(QtGui.QPen(QtGui.QColor(255, 106, 19, 70), 1, Qt.DashLine))
            p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
            now = self.now
            past, fut = QtGui.QPainterPath(), QtGui.QPainterPath()
            for path, sel in ((past, lambda t: t <= now), (fut, lambda t: t >= now)):
                first = True
                for t, az, el in zip(tr["t"], tr["az"], tr["el"]):
                    if not sel(t) or el < 0:
                        continue
                    x, y = self._xy(az, el, cx, cy, R)
                    (path.moveTo if first else path.lineTo)(x, y)
                    first = False
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 120), 1.4, Qt.DashLine))
            p.drawPath(past)
            p.setPen(QtGui.QPen(Q["live"], 2))
            p.drawPath(fut)
            # начало, конец, сейчас
            vis = [(t, az, el) for t, az, el in zip(tr["t"], tr["az"], tr["el"]) if el >= 0]
            if vis:
                for (t, az, el), col in ((vis[0], "#ffffff"), (vis[-1], "#5a5a5a")):
                    x, y = self._xy(az, el, cx, cy, R)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QtGui.QColor(col))
                    p.drawEllipse(QtCore.QPointF(x, y), 3, 3)
            if tr["t"][0] <= now <= tr["t"][-1]:
                i = min(range(len(tr["t"])), key=lambda k: abs(tr["t"][k] - now))
                if tr["el"][i] >= 0:
                    x, y = self._xy(tr["az"][i], tr["el"][i], cx, cy, R)
                    p.setPen(QtGui.QPen(QtGui.QColor("#000"), 1.5))
                    p.setBrush(QtGui.QColor("#ffffff"))
                    p.drawRect(QtCore.QRectF(x - 4, y - 4, 8, 8))
            p.setPen(Q["muted"])
            p.setFont(mfont(10))
            live = tr["aos"] <= now <= tr["los"]
            txt = (f"идёт · до {tm(tr['los'])} · макс {tr['max_el']:.0f}°" if live else
                   f"{dm(tr['aos'])} {tm(tr['aos'])}–{tm(tr['los'])} · макс {tr['max_el']:.0f}°")
            p.drawText(QtCore.QRectF(0, W, W, 18), Qt.AlignCenter, txt)
        else:
            p.setPen(Q["faint"])
            p.setFont(mfont(10))
            p.drawText(QtCore.QRectF(0, W, W, 18), Qt.AlignCenter, "пролётов нет")
        p.end()


def _wf_lut():
    """Чёрный → тёмно-оранжевый → оранжевый → белый."""
    pts = [(0, (0, 0, 0)), (90, (40, 14, 2)), (170, (255, 106, 19)), (255, (255, 255, 255))]
    lut = []
    for i in range(256):
        for (a, ca), (b, cb) in zip(pts, pts[1:]):
            if a <= i <= b:
                k = (i - a) / max(1, b - a)
                lut.append(QtGui.qRgb(*[int(x + (y - x) * k) for x, y in zip(ca, cb)]))
                break
    return lut


WF_LUT = None


class Waterfall(QtWidgets.QLabel):
    """Водопад звука приёмника 300–3000 Гц: видно тоны SSTV (синхро 1200, картинка 1500–2300)."""
    LO, HI = 300.0, 3000.0

    def __init__(self, w=330, h=64):
        super().__init__()
        self.setFixedSize(w, h)
        self.setStyleSheet("background:#000")
        self.setToolTip("Звук приёмника 300–3000 Гц, новое снизу. Синхроимпульсы SSTV — 1200 Гц, "
                        "картинка — 1500…2300 Гц")

    def set_rows(self, rows):
        global WF_LUT
        if rows is None:
            return
        if WF_LUT is None:
            WF_LUT = _wf_lut()
        r = rows.astype(np.float32)
        lo, hi = np.percentile(r, 20), np.percentile(r, 99.7)       # сам подстраивается под уровень
        a = np.ascontiguousarray(np.clip((r - lo) * (255.0 / max(8.0, hi - lo)), 0, 255).astype(np.uint8))
        h, w = a.shape
        img = QtGui.QImage(a.data, w, h, w, QtGui.QImage.Format_Indexed8)
        img.setColorTable(WF_LUT)
        pm = QtGui.QPixmap.fromImage(img.copy()).scaled(self.width(), self.height(), Qt.IgnoreAspectRatio,
                                                        Qt.SmoothTransformation)
        p = QtGui.QPainter(pm)
        p.setFont(mfont(9))
        for f, lab in ((1200, "1200"), (1500, "1500"), (2300, "2300")):
            x = (f - self.LO) / (self.HI - self.LO) * self.width()
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 1, Qt.DotLine))
            p.drawLine(QtCore.QPointF(x, 10), QtCore.QPointF(x, self.height()))
            p.setPen(QtGui.QColor(255, 255, 255, 140))
            p.drawText(QtCore.QPointF(x - 12, 9), lab)
        p.end()
        self.setPixmap(pm)


class Timeline(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        self.items, self.now = [], time.time()
        self.home = []

    def paintEvent(self, e):  # noqa: N802
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), Q["panel"])
        p.setPen(Q["line"])
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        W, H = self.width(), self.height()
        t0, span = self.now - 3600, 26 * 3600
        x = lambda t: (t - t0) / span * W  # noqa: E731
        p.setFont(mfont(10))
        h = math.ceil(t0 / 3600) * 3600
        while h < t0 + span:
            if datetime.fromtimestamp(h).hour % 2 == 0:
                p.setPen(QtGui.QColor("#1c1c1c"))
                p.drawLine(int(x(h)), 0, int(x(h)), H)
                p.setPen(Q["muted"])
                p.drawText(int(x(h)) + 3, 12, datetime.fromtimestamp(h).strftime("%H:00"))
            h += 3600
        mx = max([s["n_rx"] for s in self.items] + [1])
        for s in self.items:
            if s["b"] < t0 or s["a"] > t0 + span:
                continue
            hh = 8 + 38 * math.sqrt(s["n_rx"] / mx)
            p.fillRect(QtCore.QRectF(x(s["a"]), H - 4 - hh, max(2, x(s["b"]) - x(s["a"])), hh),
                       QtGui.QColor("#262626") if s["skip"] else QtGui.QColor("#9a9a9a"))
        for hp in self.home:
            if hp["los"] < t0 or hp["aos"] > t0 + span:
                continue
            p.fillRect(QtCore.QRectF(x(hp["aos"]), 15, max(2, x(hp["los"]) - x(hp["aos"])), 5),
                       QtGui.QColor(C["green"]) if hp["max_el"] >= 10 else QtGui.QColor(48, 209, 88, 90))
        p.setPen(QtGui.QPen(Q["live"], 2))
        p.drawLine(int(x(self.now)), 0, int(x(self.now)), H)
        p.end()


class Viewer(QtWidgets.QDialog):
    def __init__(self, win, items, i):
        super().__init__(win)
        self.win, self.items, self.i = win, items, i
        self.setWindowTitle("Снимок")
        self.resize(1000, 820)
        self.setStyleSheet("background:#000")
        lay = QtWidgets.QVBoxLayout(self)
        self.img = QtWidgets.QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setMinimumSize(320, 240)
        self.cap = QtWidgets.QLabel()
        self.cap.setAlignment(Qt.AlignCenter)
        self.cap.setTextFormat(Qt.RichText)
        self.cap.setStyleSheet(f"color:{C['text']}")
        lay.addWidget(self.img, 1)
        lay.addWidget(self.cap)
        row = QtWidgets.QHBoxLayout()
        for txt, fn in (("‹ Пред.", lambda: self.go(-1)), ("След. ›", lambda: self.go(1)),
                        ("Открыть папку", self.open_dir), ("Сохранить как…", self.save_as)):
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.show_i()

    def show_i(self):
        g = self.items[self.i]
        self.pm = QtGui.QPixmap(str(self.win.E.out / g["path"]))
        self._fit()
        t = g["t"]
        self.cap.setText(
            f"<b>{esc(g['short'])}</b> — {esc(g['name'])}<br>{wd(t)} {dm(t)} "
            f"{tm(t, True) if g['t_known'] else tm(t) + ' (время сеанса)'} · {esc(g['sat'])} (NORAD {g['norad']})"
            + (f" · качество {round(g['q'] * 100)}" if g.get("q") is not None else "")
            + (f" · строк {g['lines']}" if g.get("lines") else "")
            + (f" · угол {g['el']:.0f}°" if g.get("el") is not None else "")
            + f"<br><span style='color:{C['muted']}'>{esc(g['path'])} · {self.i + 1}/{len(self.items)}</span>")

    def _fit(self):
        if not self.pm.isNull():
            self.img.setPixmap(self.pm.scaled(self.img.size(), Qt.KeepAspectRatio, Qt.FastTransformation))

    def resizeEvent(self, e):  # noqa: N802
        self._fit()

    def go(self, d):
        self.i = (self.i + d) % len(self.items)
        self.show_i()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key_Left:
            self.go(-1)
        elif e.key() == Qt.Key_Right:
            self.go(1)
        else:
            super().keyPressEvent(e)

    def open_dir(self):
        p = (self.win.E.out / self.items[self.i]["path"]).parent
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))

    def save_as(self):
        g = self.items[self.i]
        src = self.win.E.out / g["path"]
        dst, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Сохранить снимок", str(Path.home() / src.name), "PNG (*.png)")
        if dst:
            Path(dst).write_bytes(src.read_bytes())


# ---------------- главное окно ----------------
RULES_VERSION = 1
RULES_HTML = """
<p style='font-size:14px'>Онлайн-приёмники держат радиолюбители — <b>бесплатно и для всех</b>.
У OpenWebRX <b>один тюнер на всех слушателей</b>: когда программа переключает приёмник на 70 см,
все, кто его сейчас слушает, тоже оказываются на 70 см.</p>
<p style='font-size:14px'><b>Правила:</b></p>
<ul style='font-size:14px'>
<li><b>8–12 приёмников одновременно</b> хватает, чтобы вести пролёт от края до края.
Больше почти ничего не даёт, а мешает сразу многим людям и нагружает чужие каналы.</li>
<li>Подключайся только <b>на время пролёта</b> — программа так и делает сама.</li>
<li>Если владелец приёмника просит не переключать диапазон или ограничивает время
и число слушателей — <b>выключи его приёмник</b> во вкладке «Приёмники».</li>
<li>«Проверить все» и поиск приёмников запускай перед акцией, а не постоянно.</li>
<li>Не хочешь трогать чужие настройки совсем — включи
«не переключать диапазон на OpenWebRX» в настройках.</li>
</ul>
<p style='font-size:14px'><b>Отказ от ответственности.</b> Программа распространяется «как есть», без гарантий.
Ни один приёмник не принадлежит автору программы. <b>Ты сам отвечаешь за то, как её используешь</b>:
за число подключений, переключение диапазона у других слушателей, соблюдение правил приёмников,
каталогов и законов своей страны. Автор не отвечает за ущерб, блокировки и претензии,
возникшие из-за использования программы.</p>
"""


def rules_file():
    import paths
    return paths.DATA / "rules_accepted.txt"


def rules_accepted():
    try:
        return int(rules_file().read_text(encoding="utf-8").split()[0]) >= RULES_VERSION
    except Exception:  # noqa: BLE001
        return False


class RulesDialog(QtWidgets.QDialog):
    """Правила работы с чужими приёмниками и отказ от ответственности."""

    def __init__(self, parent=None, first=True):
        super().__init__(parent)
        self.setWindowTitle("Правила — SSTV МКС")
        self.setModal(True)
        self.resize(640, 520)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        h = QtWidgets.QLabel("ЧУЖИЕ ПРИЁМНИКИ · ПРАВИЛА")
        h.setFont(hfont(18, True, 3))
        v.addWidget(h)
        t = QtWidgets.QTextBrowser()
        t.setHtml(RULES_HTML)
        t.setOpenExternalLinks(True)
        t.setStyleSheet(f"QTextBrowser{{background:{C['panel']};border:1px solid {C['line']};padding:8px;color:{C['text']}}}")
        v.addWidget(t, 1)
        r = QtWidgets.QHBoxLayout()
        if first:
            self.chk = QtWidgets.QCheckBox("Прочитал, согласен и буду соблюдать")
            r.addWidget(self.chk, 1)
            quit_b = QtWidgets.QPushButton("Выйти")
            quit_b.setFont(hfont(12, True, 1.8))
            quit_b.clicked.connect(self.reject)
            r.addWidget(quit_b)
            self.ok = QtWidgets.QPushButton("Принять")
            self.ok.setProperty("primary", True)
            self.ok.setFont(hfont(12, True, 1.8))
            self.ok.setEnabled(False)
            self.chk.toggled.connect(self.ok.setEnabled)
            self.ok.clicked.connect(self._accept)
            r.addWidget(self.ok)
        else:
            r.addStretch(1)
            b = QtWidgets.QPushButton("Закрыть")
            b.setFont(hfont(12, True, 1.8))
            b.clicked.connect(self.accept)
            r.addWidget(b)
        v.addLayout(r)

    def _accept(self):
        try:
            import datetime as _dt
            rules_file().write_text(f"{RULES_VERSION} {_dt.datetime.now().isoformat(timespec='seconds')}\n",
                                    encoding="utf-8")
        except OSError:
            pass
        self.accept()


class Main(QtWidgets.QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.E = engine
        self.S = {}
        self.RX = {"ver": 0, "list": []}
        self.RXI = {}
        st = load_state()
        self.vol = st.get("vol", {})
        self.master = st.get("master", 60)
        self.muted = st.get("muted", False)
        self.auto_best = st.get("autoBest", True)
        self.gal_kind = st.get("galKind", "best")
        self.hide_bad = st.get("hideBad", False)
        self.sel_rx = None
        self.form_url = None
        self.track = None
        self._track_t = 0
        self.sched = None
        self.gal = {"ver": -1, "list": []}
        self.thumbs = {}
        self.reports = {}
        import about
        self.setWindowTitle(f"{about.APP_NAME} {about.__version__}" + (f" — {about.AUTHOR}" if about.AUTHOR else ""))
        self.resize(1440, 920)
        if st.get("geom"):
            try:
                self.restoreGeometry(QtCore.QByteArray.fromBase64(st["geom"].encode()))
            except Exception:  # noqa: BLE001
                pass
        self.mixer = Mixer(engine)
        self._build()
        self.tabs.setCurrentIndex(st.get("tab", 0))
        self.t_poll = QtCore.QTimer(self, timeout=self.poll, interval=700)
        self.t_poll.start()
        self.t_snd = QtCore.QTimer(self, timeout=self.sound_tick, interval=40)
        self.t_snd.start()

    # ---------- состояние окна ----------
    def snd(self, k):
        return self.vol.setdefault(k, {"v": 80, "on": False})

    def save_state(self):
        try:
            STATE_FILE.write_text(json.dumps(dict(
                vol=self.vol, master=self.master, muted=self.muted, autoBest=self.auto_best, galKind=self.gal_kind, hideBad=self.hide_bad,
                tab=self.tabs.currentIndex(), geom=bytes(self.saveGeometry().toBase64()).decode()),
                ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def cmd(self, c):
        self.E.cmd(c)

    # ---------- построение ----------
    def _build(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        v = QtWidgets.QVBoxLayout(cw)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # шапка: бренд · связь · аппарат · статус · отсчёт · звук
        top = QtWidgets.QFrame()
        top.setObjectName("top")
        h = QtWidgets.QHBoxLayout(top)
        h.setContentsMargins(20, 9, 20, 9)
        h.setSpacing(20)

        def two(big, small, bf, sf):
            box = QtWidgets.QWidget()
            vb = QtWidgets.QVBoxLayout(box)
            vb.setContentsMargins(0, 0, 0, 0)
            vb.setSpacing(2)
            a1, a2 = QtWidgets.QLabel(big), QtWidgets.QLabel(small)
            a1.setFont(bf)
            a2.setFont(sf)
            a2.setStyleSheet(f"color:{C['muted']}")
            vb.addWidget(a1)
            vb.addWidget(a2)
            return box, a1, a2
        brand, _, _ = two("SSTV · МКС", "пульт приёма", hfont(19, True, 5), hfont(10, True, 3))
        logo = QtWidgets.QLabel()
        lp = QtGui.QPixmap(str(UI_DIR / "icon_1024.png"))
        if not lp.isNull():
            dpr = self.devicePixelRatioF()
            lp = lp.scaled(int(38 * dpr), int(38 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lp.setDevicePixelRatio(dpr)
            logo.setPixmap(lp)
            h.addWidget(logo)
            h.addSpacing(-8)
        h.addWidget(brand)
        conn = QtWidgets.QWidget()
        ch = QtWidgets.QHBoxLayout(conn)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(8)
        self.dot = QtWidgets.QLabel()
        self.dot.setFixedSize(8, 8)
        self.conn = QtWidgets.QLabel("запуск")
        self.conn.setFont(hfont(11, True, 2))
        self.conn.setStyleSheet(f"color:{C['muted']}")
        ch.addWidget(self.dot)
        ch.addWidget(self.conn)
        h.addWidget(conn)
        vessel, self.v_name, self.v_sub = two("—", "", hfont(15, True, 2.5), hfont(10, True, 2))
        h.addWidget(vessel)
        self.st_box = QtWidgets.QFrame()
        self.st_box.setObjectName("stbox")
        sb = QtWidgets.QHBoxLayout(self.st_box)
        sb.setContentsMargins(14, 7, 14, 7)
        self.st_text = QtWidgets.QLabel("ожидание")
        self.st_text.setFont(hfont(15, True, 1.2))
        self.st_text.setMinimumWidth(80)
        self.st_ph = QtWidgets.QLabel("IDLE")
        self.st_ph.setFont(mfont(11))
        self.st_ph.setStyleSheet(f"color:{C['muted']}")
        sb.addWidget(self.st_text, 1)
        sb.addWidget(self.st_ph)
        h.addWidget(self.st_box, 1)
        self.met = QtWidgets.QLabel("T−--:--")
        self.met.setFont(mfont(30))
        h.addWidget(self.met)
        self.mute_btn = QtWidgets.QToolButton()
        self.mute_btn.clicked.connect(self._toggle_mute)
        self.master_sl = QtWidgets.QSlider(Qt.Horizontal)
        self.master_sl.setRange(0, 100)
        self.master_sl.setFixedWidth(110)
        self.master_sl.setValue(self.master)
        self.master_sl.setToolTip("Общая громкость")
        self.master_sl.valueChanged.connect(self._master)
        self.best_cb = QtWidgets.QCheckBox("лучший")
        self.best_cb.setToolTip("Автоматически слушать приёмник с лучшим сигналом SSTV")
        self.best_cb.setChecked(self.auto_best)
        self.best_cb.toggled.connect(self._best)
        h.addWidget(self.mute_btn)
        h.addWidget(self.master_sl)
        h.addWidget(self.best_cb)
        if not self.mixer.ok:
            na = QtWidgets.QLabel("звук недоступен")
            na.setStyleSheet(f"color:{C['bad']}")
            na.setToolTip(self.mixer.note)
            h.addWidget(na)
        self.snd_hint = QtWidgets.QLabel("ничего не слушаю — включи «лучший» или звук на карточке приёмника")
        self.snd_hint.setStyleSheet(f"color:{C['warn']};font-size:12px")
        self.snd_hint.setVisible(False)
        h.addWidget(self.snd_hint)
        self._mute_icon()
        v.addWidget(top)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setFont(hfont(13, True, 2.4))
        v.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_live(), "Эфир")
        self.tabs.addTab(self._tab_map(), "Карта")
        self.tabs.addTab(self._tab_sched(), "Расписание")
        self.tabs.addTab(self._tab_rx(), "Приёмники")
        self.tabs.addTab(self._tab_gal(), "Галерея")
        self.tabs.addTab(self._tab_set(), "Настройки")
        self.tabs.addTab(self._tab_log(), "Журнал")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.status = QtWidgets.QLabel("—")
        self.status.setStyleSheet(f"font-family:'{MONO}';font-size:11px;color:{C['muted']};padding:3px 12px")
        self.statusBar().addWidget(self.status, 1)

    def _btn(self, text, fn, tip=None, danger=False, primary=False):
        b = QtWidgets.QPushButton(text)
        b.clicked.connect(fn)
        if tip:
            b.setToolTip(tip)
        b.setFont(hfont(12, True, 1.8))
        if danger:
            b.setProperty("danger", True)
        if primary:
            b.setProperty("primary", True)
        return b

    # ----- Эфир -----
    def _tab_live(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 8)
        hud = QtWidgets.QFrame()
        hud.setObjectName("hud")
        hl = QtWidgets.QHBoxLayout(hud)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        self.hud = {}
        for i, (k, t) in enumerate((("sat", "МКС"), ("next", "следующий сеанс"), ("rx", "слушаю"), ("img", "снимков за сеанс"))):
            cell = QtWidgets.QFrame()
            cell.setObjectName("hudc" if i < 3 else "hudl")
            cv = QtWidgets.QVBoxLayout(cell)
            cv.setContentsMargins(16, 10, 16, 10)
            cv.setSpacing(1)
            kl, vl, sl = QtWidgets.QLabel(t), QtWidgets.QLabel("—"), QtWidgets.QLabel("")
            kl.setFont(hfont(10, True, 2.6))
            kl.setStyleSheet(f"color:{C['muted']}")
            vl.setFont(mfont(30))
            vl.setTextFormat(Qt.RichText)
            sl.setFont(mfont(11))
            sl.setStyleSheet(f"color:{C['muted']}")
            for x in (kl, vl, sl):
                cv.addWidget(x)
            hl.addWidget(cell, 1)
            self.hud[k] = (vl, sl)
            if k == "sat":
                self.hud_sat_k = kl
        v.addWidget(hud)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 4)
        self.live_head = QtWidgets.QLabel("—")
        self.live_head.setStyleSheet(f"color:{C['muted']}")
        self.live_head.setWordWrap(True)
        row.addWidget(self.live_head, 1)
        self.auto_cb = QtWidgets.QCheckBox("авто-приём")
        self.auto_cb.setToolTip("Принимать сеансы по расписанию автоматически")
        self.auto_cb.clicked.connect(lambda on: self.cmd(dict(cmd="auto", on=on)))
        self.b_test = self._btn("Тест 60 с", lambda: self.cmd(dict(cmd="test", seconds=60)),
                                "Открыть приёмники, которые выше всех сейчас, на 60 с без доплера")
        self.b_skip = self._btn("Пропустить", lambda: self.S.get("next") and self.cmd(dict(cmd="skip", id=self.S["next"]["id"])),
                                "Пропустить ближайший сеанс")
        self.b_stop = self._btn("Стоп", lambda: self.cmd(dict(cmd="stop")), "Остановить сеанс, тест или проверку", danger=True)
        for x in (self.auto_cb, self.b_test, self.b_skip, self.b_stop):
            row.addWidget(x)
        v.addLayout(row)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(14)
        self.comb_panel = QtWidgets.QFrame()
        self.comb_panel.setObjectName("sect")
        self.comb_panel.setFixedWidth(500)
        cp = QtWidgets.QVBoxLayout(self.comb_panel)
        cp.setContentsMargins(10, 8, 10, 10)
        ct = QtWidgets.QLabel("сводная картинка пролёта")
        ct.setFont(hfont(12, True, 2.4))
        ct.setStyleSheet(f"color:{C['muted']}")
        ct.setToolTip("Каждая строка берётся с того приёмника, где она принята лучше. Обновляется каждые 3 с.")
        self.comb_img = QtWidgets.QLabel("ждём первых строк")
        self.comb_img.setAlignment(Qt.AlignCenter)
        self.comb_img.setFixedSize(480, 360)
        self.comb_img.setStyleSheet("background:#050505;color:#3a3a3a")
        self.comb_txt = QtWidgets.QLabel()
        self.comb_txt.setWordWrap(True)
        self.comb_txt.setStyleSheet(f"font-family:'{MONO}';font-size:11px;color:{C['muted']}")
        self.comb_bar = bar(0, C["live"])
        for x in (ct, self.comb_img, self.comb_bar, self.comb_txt):
            cp.addWidget(x)
        cp.addStretch(1)
        self.comb_ver = -1
        self.comb_panel.hide()
        body.addWidget(self.comb_panel, 0, Qt.AlignTop)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        iv = QtWidgets.QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        self.cards_box = QtWidgets.QWidget()
        self.cards_flow = FlowLayout(self.cards_box, 10)
        iv.addWidget(self.cards_box)
        self.no_active = QtWidgets.QLabel("Сейчас приёмники не слушаются. Карточки появятся, когда начнётся сеанс или тест.")
        self.no_active.setAlignment(Qt.AlignCenter)
        self.no_active.setStyleSheet(f"color:{C['muted']};padding:30px")
        iv.addWidget(self.no_active)
        self.vis_title = QtWidgets.QLabel()
        self.vis_title.setFont(hfont(12, True, 2.4))
        self.vis_title.setStyleSheet(f"color:{C['muted']};margin-top:12px")
        iv.addWidget(self.vis_title)
        self.vis_list = QtWidgets.QLabel()
        self.vis_list.setWordWrap(True)
        self.vis_list.setTextFormat(Qt.RichText)
        iv.addWidget(self.vis_list)
        iv.addStretch(1)
        sc.setWidget(inner)
        body.addWidget(sc, 1)
        v.addLayout(body, 1)
        self.cards = {}
        return w

    # ----- Карта -----
    def _tab_map(self):
        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self.map = MapWidget()
        self.map.clicked.connect(lambda d, lo, la: d and self.show_rx_info(d))
        self.map.followOff.connect(lambda: self.b_follow.setChecked(False))
        g.addWidget(self.map, 0, 0)
        ctl = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(ctl)
        cv.setContentsMargins(12, 12, 0, 0)
        cv.setSpacing(4)
        for txt, fn, tip in (("+", lambda: self.map.zoom_at(self.map.width() / 2, self.map.height() / 2, 1.8), "Приблизить"),
                             ("−", lambda: self.map.zoom_at(self.map.width() / 2, self.map.height() / 2, 1 / 1.8), "Отдалить"),
                             ("⌂", self._map_home, "Весь мир")):
            b = QtWidgets.QPushButton(txt)
            b.setFixedSize(34, 34)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            cv.addWidget(b)
        self.b_follow = QtWidgets.QPushButton("🛰")
        self.b_follow.setCheckable(True)
        self.b_follow.setFixedSize(34, 34)
        self.b_follow.setToolTip("Следить за спутником")
        self.b_follow.toggled.connect(self._follow)
        cv.addWidget(self.b_follow)
        cv.addStretch(1)
        g.addWidget(ctl, 0, 0, Qt.AlignLeft | Qt.AlignTop)
        leg = QtWidgets.QLabel(
            "".join(f"<div><span style='color:{c}'>●</span> {t}</div>" for c, t in (
                (C["live"], "слушаю"), ("#ffffff", "спутник выше мин. угла"), (C["muted"], "спутник над горизонтом"),
                ("#5a5a5a", "ждёт"), (C["bad"], "не прошёл проверку"), ("#2a2a2a", "выключен")))
            + f"<div><span style='color:{C['live']}'>━</span> трасса &nbsp;<span style='color:#aaa'>┄</span> пройдено"
            + f" &nbsp;<span style='color:{C['live']}'>○</span> зона выше мин. угла</div>"
            + f"<div><span style='color:{C['green']}'>●</span> моё место (задаётся в настройках)</div>")
        leg.setObjectName("overlay")
        g.addWidget(leg, 0, 0, Qt.AlignLeft | Qt.AlignBottom)
        self.map_info_box = QtWidgets.QFrame()
        self.map_info_box.setObjectName("infobox")
        self.map_info_box.setFixedWidth(320)
        ib = QtWidgets.QVBoxLayout(self.map_info_box)
        ib.setContentsMargins(12, 10, 12, 10)
        self.map_info = QtWidgets.QLabel()
        self.map_info.setTextFormat(Qt.RichText)
        self.map_info.setWordWrap(True)
        self.map_polar = PolarPlot(230)
        ib.addWidget(self.map_info)
        ib.addWidget(self.map_polar, 0, Qt.AlignHCenter)
        self.map_info_box.hide()
        g.addWidget(self.map_info_box, 0, 0, Qt.AlignRight | Qt.AlignTop)
        leg.setContentsMargins(10, 8, 10, 8)
        return w

    def _map_home(self):
        self.b_follow.setChecked(False)
        self.map.home()

    def _follow(self, on):
        self.map.follow = on
        if on and self.S.get("sat"):
            self.map.center(self.S["sat"]["lon"], self.S["sat"]["lat"], min_zoom=2.5)

    # ----- Расписание -----
    def _tab_sched(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 8)
        self.sched_head = QtWidgets.QLabel("—")
        v.addWidget(self.sched_head)
        self.timeline = Timeline()
        v.addWidget(self.timeline)
        self.sched_tbl = QtWidgets.QTableWidget(0, 7)
        self.sched_tbl.setHorizontalHeaderLabels(["Дата", "Время", "Длит.", "Приёмников", "Лучшие", "Состояние", ""])
        self._table_style(self.sched_tbl)
        self.sched_tbl.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self.sched_tbl, 1)
        hp = QtWidgets.QFrame()
        hp.setObjectName("sect")
        hp.setFixedWidth(380)
        hv = QtWidgets.QVBoxLayout(hp)
        hv.setContentsMargins(12, 10, 12, 10)
        self.home_title = QtWidgets.QLabel("над моим местом")
        self.home_title.setFont(hfont(12, True, 2.4))
        self.home_title.setStyleSheet(f"color:{C['green']}")
        self.home_next = QtWidgets.QLabel()
        self.home_next.setFont(mfont(26))
        self.home_next.setTextFormat(Qt.RichText)
        self.home_sub = QtWidgets.QLabel()
        self.home_sub.setStyleSheet(f"font-family:'{MONO}';font-size:11px;color:{C['muted']}")
        self.home_sub.setWordWrap(True)
        self.home_polar = PolarPlot(240)
        self.home_tbl = QtWidgets.QTableWidget(0, 4)
        self.home_tbl.setHorizontalHeaderLabels(["Дата", "Время", "Макс", "Откуда → куда"])
        self._table_style(self.home_tbl)
        self.home_tbl.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.home_hint = QtWidgets.QLabel("Задай свои координаты во вкладке «Настройки» → «Моё место», "
                                          "и здесь появятся пролёты МКС над тобой.")
        self.home_hint.setWordWrap(True)
        self.home_hint.setStyleSheet(f"color:{C['muted']}")
        for x in (self.home_title, self.home_next, self.home_sub):
            hv.addWidget(x)
        hv.addWidget(self.home_polar, 0, Qt.AlignHCenter)
        hv.addWidget(self.home_tbl, 1)
        hv.addWidget(self.home_hint)
        body.addWidget(hp)
        v.addLayout(body, 1)
        self.home_ps = None
        self._home_key = None
        return w

    def _table_style(self, t):
        t.verticalHeader().hide()
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.horizontalHeader().setHighlightSections(False)
        t.horizontalHeader().setFont(hfont(11, True, 2))

    # ----- Приёмники -----
    def _tab_rx(self):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(14, 12, 14, 8)
        left = QtWidgets.QVBoxLayout()
        r1 = QtWidgets.QHBoxLayout()
        self.rx_search = QtWidgets.QLineEdit()
        self.rx_search.setPlaceholderText("поиск: страна, город, адрес")
        self.rx_search.setClearButtonEnabled(True)
        self.rx_filter = QtWidgets.QComboBox()
        for k, t in (("all", "все"), ("ok", "рабочие"), ("bad", "не прошли проверку"), ("off", "выключенные"),
                     ("user", "добавленные вручную"), ("vis", "видят спутник сейчас"),
                     ("useless", "без картинок за 3+ сеанса"), ("good", "дают картинку чаще всего")):
            self.rx_filter.addItem(t, k)
        self.rx_count = QtWidgets.QLabel()
        self.rx_count.setStyleSheet(f"color:{C['muted']}")
        r1.addWidget(self.rx_search, 2)
        r1.addWidget(self.rx_filter, 1)
        r1.addWidget(self.rx_count)
        r1.addStretch(1)
        left.addLayout(r1)
        r2 = QtWidgets.QHBoxLayout()
        for txt, fn, tip in (
                ("Проверить выбранные", lambda: self._need_sel() and self.cmd(dict(cmd="check", urls=self._sel())),
                 "Открыть выбранные и проверить звук и профиль 437.550"),
                ("Проверить все", self._check_all, "Проверить все (≈10 минут)"),
                ("Тест выбранных", self._test_sel, "Слушать выбранные 60 секунд прямо сейчас"),
                ("Включить", lambda: self._need_sel() and self.cmd(dict(cmd="rx_enable", urls=self._sel(), on=True)), None),
                ("Выключить", lambda: self._need_sel() and self.cmd(dict(cmd="rx_enable", urls=self._sel(), on=False)), None),
                ("Выключить неработающие", self._off_dead,
                 "Выключить все приёмники, не прошедшие проверку (по желанию — и без картинок за 3+ сеанса)"),
                ("Сбросить проверку", lambda: self._need_sel() and self.cmd(dict(cmd="rx_unbad", urls=self._sel())),
                 "Забыть результат проверки"),
                ("Искать приёмники", self._scan, "Собрать список заново: rx-tx.info, ReceiverBook и websdr.org (5–10 минут)")):
            r2.addWidget(self._btn(txt, fn, tip))
        r2.addStretch(1)
        left.addLayout(r2)
        self.rx_tbl = QtWidgets.QTableWidget(0, 8)
        self.rx_tbl.setHorizontalHeaderLabels(["", "В работе", "Приёмник", "Тип", "Координаты", "Угол", "Проверка", "История"])
        self._table_style(self.rx_tbl)
        hh = self.rx_tbl.horizontalHeader()
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        for i, wdt in ((0, 32), (1, 70), (3, 64), (4, 150), (5, 56), (6, 210), (7, 90)):
            self.rx_tbl.setColumnWidth(i, wdt)
        self.rx_tbl.itemChanged.connect(self._rx_item_changed)
        self.rx_tbl.cellClicked.connect(self._rx_cell)
        left.addWidget(self.rx_tbl, 1)
        h.addLayout(left, 1)
        # форма
        right = QtWidgets.QVBoxLayout()
        self.form_title = QtWidgets.QLabel("<b>Добавить приёмник</b>")
        right.addWidget(self.form_title)
        f = QtWidgets.QFormLayout()
        self.f_url = QtWidgets.QLineEdit()
        self.f_url.setPlaceholderText("http://host:8073/")
        self.f_kind = QtWidgets.QComboBox()
        self.f_kind.addItem("OpenWebRX / OpenWebRX+", "owrx")
        self.f_kind.addItem("WebSDR (PA3FWM)", "websdr")
        self.f_lat = QtWidgets.QLineEdit()
        self.f_lat.setPlaceholderText("55.7558")
        self.f_lon = QtWidgets.QLineEdit()
        self.f_lon.setPlaceholderText("37.6173")
        self.f_name = QtWidgets.QLineEdit()
        self.f_name.setPlaceholderText("Москва, R3XXX")
        self.f_short = QtWidgets.QLineEdit()
        self.f_short.setMaxLength(14)
        self.f_short.setPlaceholderText("RU Moscow")
        f.addRow("Адрес", self.f_url)
        f.addRow("Тип", self.f_kind)
        f.addRow("Широта", self.f_lat)
        f.addRow("Долгота", self.f_lon)
        f.addRow("Название", self.f_name)
        f.addRow("Короткое имя", self.f_short)
        right.addLayout(f)
        hint = QtWidgets.QLabel("Координаты: градусы через точку, юг и запад — с минусом. Или щёлкни по карте.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{C['muted']};font-size:11px")
        right.addWidget(hint)
        self.pick = MapWidget(small=True)
        self.pick.setFixedHeight(220)
        self.pick.clicked.connect(self._pick)
        right.addWidget(self.pick)
        r3 = QtWidgets.QHBoxLayout()
        r3.addWidget(self._btn("Сохранить", self._rx_save, primary=True))
        r3.addWidget(self._btn("Новый", lambda: self.fill_form(None)))
        self.b_rxdel = self._btn("Убрать ручные данные", self._rx_del, danger=True)
        self.b_rxdel.hide()
        r3.addWidget(self.b_rxdel)
        r3.addStretch(1)
        right.addLayout(r3)
        self.form_msg = QtWidgets.QLabel()
        self.form_msg.setWordWrap(True)
        right.addWidget(self.form_msg)
        self.rx_passes = QtWidgets.QLabel()
        self.rx_passes.setTextFormat(Qt.RichText)
        right.addWidget(self.rx_passes)
        self.rx_polar = PolarPlot(200)
        self.rx_polar.hide()
        right.addWidget(self.rx_polar, 0, Qt.AlignHCenter)
        right.addStretch(1)
        rw = QtWidgets.QWidget()
        rw.setLayout(right)
        rsc = QtWidgets.QScrollArea()
        rsc.setWidgetResizable(True)
        rsc.setFrameShape(QtWidgets.QFrame.NoFrame)
        rsc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rsc.setWidget(rw)
        rsc.setFixedWidth(390)
        h.addWidget(rsc)
        self.rx_search.textChanged.connect(self.render_rx)
        self.rx_filter.currentIndexChanged.connect(self.render_rx)
        self.rx_checked: set = set()
        return w

    # ----- Галерея -----
    def _tab_gal(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 8)
        row = QtWidgets.QHBoxLayout()
        self.gal_btns = QtWidgets.QButtonGroup(self)
        for k, t in (("best", "лучшее"), ("all", "все"), ("live", "в эфире"), ("redecoded", "пересборка"),
                     ("combined", "склейки"), ("stacked", "стопки")):
            b = QtWidgets.QPushButton(t)
            b.setCheckable(True)
            b.setChecked(k == self.gal_kind)
            b.setProperty("k", k)
            self.gal_btns.addButton(b)
            row.addWidget(b)
        self.gal_btns.buttonClicked.connect(self._gal_kind)
        self.gal_sat = QtWidgets.QComboBox()
        self.gal_sat.setMinimumWidth(150)
        self.gal_search = QtWidgets.QLineEdit()
        self.gal_search.setPlaceholderText("приёмник")
        self.gal_search.setClearButtonEnabled(True)
        self.gal_sort = QtWidgets.QComboBox()
        self.gal_sort.addItem("по времени", "t")
        self.gal_sort.addItem("по качеству", "q")
        self.gal_count = QtWidgets.QLabel()
        self.gal_count.setStyleSheet(f"color:{C['muted']}")
        for x in (self.gal_sat, self.gal_search, self.gal_sort, self.gal_count):
            row.addWidget(x)
        row.addStretch(1)
        row.addWidget(self._btn("Пересобрать записи", self._redecode, "Пересобрать все записи нынешним декодером"))
        v.addLayout(row)
        self.gal_scroll = QtWidgets.QScrollArea()
        self.gal_scroll.setWidgetResizable(True)
        self.gal_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        v.addWidget(self.gal_scroll, 1)
        self.gal_sat.currentIndexChanged.connect(self.render_gallery)
        self.gal_search.textChanged.connect(self.render_gallery)
        self.gal_sort.currentIndexChanged.connect(self.render_gallery)
        return w

    # ----- Настройки -----
    def _tab_set(self):
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 8)
        gb = self._section(v, "Спутник")
        f = QtWidgets.QFormLayout(gb)
        self.sat_info = QtWidgets.QLabel("—")
        self.sat_info.setStyleSheet(f"font-family:'{MONO}';font-size:12px;color:{C['muted']}")
        f.addRow(self.sat_info)
        self.s_preset = QtWidgets.QComboBox()
        self.s_preset.addItem("МКС (ISS), NORAD 25544", "25544")
        self.s_preset.addItem("другой…", "")
        self.s_preset.activated.connect(self._preset)
        self.s_norad = QtWidgets.QLineEdit()
        self.s_norad.setValidator(QtGui.QIntValidator(1, 999999))
        self.s_tle = QtWidgets.QPlainTextEdit()
        self.s_tle.setPlaceholderText("ISS (ZARYA)\n1 25544U 98067A   …\n2 25544  51.6…")
        self.s_tle.setFixedHeight(70)
        self.s_tle.setStyleSheet(f"font-family:'{MONO}';font-size:12px")
        f.addRow("Готовый", self.s_preset)
        f.addRow("NORAD ID", self.s_norad)
        f.addRow("TLE вручную", self.s_tle)
        hint = QtWidgets.QLabel("Если TLE вписан, он используется вместо скачивания. Пусто — TLE берётся из сети по NORAD ID.")
        hint.setStyleSheet(f"color:{C['muted']};font-size:11px")
        hint.setWordWrap(True)
        f.addRow(hint)
        f.addRow(self._btn("Скачать TLE заново", lambda: (self.cmd(dict(cmd="refresh_tle")),
                                                          self.set_msg.setText("Скачиваю TLE…"))))
        gb = self._section(v, "Сигнал")
        f = QtWidgets.QFormLayout(gb)
        self.s_freq = QtWidgets.QDoubleSpinBox()
        self.s_freq.setDecimals(4)
        self.s_freq.setRange(1, 3000)
        self.s_freq.setSuffix(" МГц")
        self.s_mode = QtWidgets.QComboBox()
        self.s_mode.addItem("Robot 36")
        f.addRow("Частота", self.s_freq)
        f.addRow("Режим SSTV", self.s_mode)
        hint = QtWidgets.QLabel("Список приёмников проверялся на 437.550 МГц. На другой частоте у части OpenWebRX "
                                "может не оказаться подходящего профиля.")
        hint.setStyleSheet(f"color:{C['muted']};font-size:11px")
        hint.setWordWrap(True)
        f.addRow(hint)
        gb = self._section(v, "Приём")
        f = QtWidgets.QFormLayout(gb)

        def spin(lo, hi, step=1, dec=0, suf=""):
            s = QtWidgets.QDoubleSpinBox() if dec else QtWidgets.QSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            if dec:
                s.setDecimals(dec)
            s.setSuffix(suf)
            return s
        self.s_max = spin(1, 9999)
        self.s_max.setToolTip("Каждый приёмник — отдельная скрытая страница браузера, примерно 100–150 МБ памяти")
        self.s_minel = spin(0, 60, 1, 1, "°")
        self.s_sep = spin(0, 2000, 5, 1, " км")
        self.s_lead = spin(0, 600, 10, 0, " с")
        self.s_tail = spin(0, 600, 10, 0, " с")
        self.s_yield = spin(0, 600, 5, 0, " с")
        self.s_until = QtWidgets.QDateTimeEdit()
        self.s_until.setCalendarPopup(True)
        self.s_until.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.s_noswitch = QtWidgets.QCheckBox("не переключать диапазон на OpenWebRX")
        self.s_headless = QtWidgets.QCheckBox("скрывать окна приёмников")
        self.s_auto = QtWidgets.QCheckBox("принимать сеансы автоматически")
        self.s_hist = QtWidgets.QCheckBox("при выборе приёмников учитывать, давали ли они картинку раньше")
        self.s_max_warn = QtWidgets.QLabel(
            "Больше 12 почти ничего не добавляет к приёму, но мешает другим слушателям и нагружает чужие каналы.")
        self.s_max_warn.setWordWrap(True)
        self.s_max_warn.setStyleSheet(f"color:{C['warn']};font-size:12px")
        self.s_max_warn.setVisible(False)
        self.s_max.valueChanged.connect(lambda n: self.s_max_warn.setVisible(n > 12))
        f.addRow("Приёмников одновременно", self.s_max)
        f.addRow("", self.s_max_warn)
        f.addRow("Мин. угол спутника", self.s_minel)
        f.addRow("Не брать ближе друг к другу", self.s_sep)
        f.addRow("Начинать раньше", self.s_lead)
        f.addRow("Слушать после", self.s_tail)
        f.addRow("Вернуть диапазон через", self.s_yield)
        f.addRow("Работать до", self.s_until)
        f.addRow(self.s_noswitch)
        f.addRow(self.s_headless)
        f.addRow(self.s_auto)
        f.addRow(self.s_hist)
        gb = self._section(v, "Моё место")
        f = QtWidgets.QFormLayout(gb)
        self.s_hname = QtWidgets.QLineEdit()
        self.s_hname.setPlaceholderText("Я")
        self.s_hname.setMaxLength(16)
        self.s_hlat = QtWidgets.QLineEdit()
        self.s_hlat.setPlaceholderText("55.7558")
        self.s_hlon = QtWidgets.QLineEdit()
        self.s_hlon.setPlaceholderText("37.6173")
        self.s_halt = QtWidgets.QSpinBox()
        self.s_halt.setRange(-400, 9000)
        self.s_halt.setSuffix(" м")
        self.s_hshow = QtWidgets.QCheckBox("показывать моё место на карте (зелёная точка)")
        f.addRow("Подпись", self.s_hname)
        f.addRow("Широта", self.s_hlat)
        f.addRow("Долгота", self.s_hlon)
        f.addRow("Высота", self.s_halt)
        f.addRow(self.s_hshow)
        hint = QtWidgets.QLabel("Градусы через точку, юг и запад — с минусом. Можно щёлкнуть по карте ниже. "
                                "Пустые поля — место не задано. Пролёты над тобой — во вкладке «Расписание».")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{C['muted']};font-size:11px")
        f.addRow(hint)
        self.s_hpick = MapWidget(small=True)
        self.s_hpick.setFixedHeight(220)
        self.s_hpick.clicked.connect(self._home_pick)
        f.addRow(self.s_hpick)
        gb = self._section(v, "Карта")
        f = QtWidgets.QFormLayout(gb)
        self.s_hidebad = QtWidgets.QCheckBox("скрыть с карты приёмники, не прошедшие проверку")
        self.s_hidebad.setChecked(self.hide_bad)
        self.s_hidebad.toggled.connect(self._hidebad)
        f.addRow(self.s_hidebad)
        hint = QtWidgets.QLabel("Действует сразу, кнопку «Сохранить» нажимать не нужно.")
        hint.setStyleSheet(f"color:{C['muted']};font-size:11px")
        f.addRow(hint)
        r = QtWidgets.QHBoxLayout()
        r.setContentsMargins(0, 12, 0, 0)
        r.addWidget(self._btn("Сохранить", self._save_settings, primary=True))
        self.set_msg = QtWidgets.QLabel()
        r.addWidget(self.set_msg, 1)
        v.addLayout(r)
        import about
        gb = self._section(v, "О программе")
        al = QtWidgets.QVBoxLayout(gb)
        lines = [f"<b>{esc(about.APP_NAME)}</b> &nbsp;<span style='color:{C['muted']}'>версия {about.__version__}</span>"]
        if about.AUTHOR:
            lines.append(f"Автор: {esc(about.AUTHOR)}")
        if about.REPO_URL:
            lines.append(f"Исходники: <a href='{esc(about.REPO_URL)}' style='color:#fff'>{esc(about.REPO_URL)}</a>")
            lines.append(f"Ошибки и пожелания: <a href='{esc(about.issues_url())}' style='color:{C['live']}'>"
                         f"написать на GitHub</a>")
        lines.append(f"<span style='color:{C['muted']};font-size:11px'>Карта: Natural Earth. Шрифты: PT Sans Narrow, "
                     f"JetBrains Mono (SIL OFL). Орбиты: CelesTrak.</span>")
        ab = QtWidgets.QLabel("<br>".join(lines))
        ab.setTextFormat(Qt.RichText)
        ab.setOpenExternalLinks(True)
        ab.setWordWrap(True)
        al.addWidget(ab)
        rb = QtWidgets.QHBoxLayout()
        rb.addWidget(self._btn("Правила и отказ от ответственности",
                               lambda: RulesDialog(self, first=False).exec_()))
        rb.addStretch(1)
        al.addLayout(rb)
        v.addStretch(1)
        w.setMaximumWidth(820)
        sc.setWidget(w)
        return sc

    def _section(self, v, title):
        lab = QtWidgets.QLabel(title)
        lab.setFont(hfont(12, True, 2.6))
        lab.setStyleSheet(f"color:{C['muted']};margin-top:10px")
        v.addWidget(lab)
        box = QtWidgets.QFrame()
        box.setObjectName("sect")
        v.addWidget(box)
        return box

    def _home_pick(self, data, lon, lat):
        self.s_hlat.setText(f"{lat:.4f}")
        self.s_hlon.setText(f"{lon:.4f}")
        self.s_hpick.home_pt = (lon, lat, self.s_hname.text().strip() or "Я")
        self.s_hpick.update()

    def _hidebad(self, on):
        self.hide_bad = on
        self.save_state()
        self.update_map()
        self.render_rx()

    def _tab_log(self):
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setStyleSheet(f"font-family:'{MONO}';font-size:12px;color:#cfcfcf;border:none")
        self.log_seq = 0
        return self.log

    # ---------- звук ----------
    def _mute_icon(self):
        self.mute_btn.setText("🔇" if self.muted else "🔊")
        self.mute_btn.setToolTip("Звук выключен" if self.muted else "Звук включён")

    def _toggle_mute(self):
        self.muted = not self.muted
        self._mute_icon()
        self.save_state()

    def _master(self, v):
        self.master = v
        self.save_state()

    def _best(self, on):
        self.auto_best = on
        self.save_state()
        self.render_cards()

    def best_key(self):
        act = self.S.get("active") or []
        good = [a for a in act if a["strength"] >= 15]
        b = max(good, key=lambda a: a["strength"]) if good else (max(act, key=lambda a: a["el"]) if act else None)
        return b and b["key"]

    def playing(self):
        best = self.best_key() if self.auto_best else None
        return [a["key"] for a in self.S.get("active") or [] if self.snd(a["key"])["on"] or a["key"] == best]

    def sound_tick(self):
        keys = self.playing()
        self.snd_hint.setVisible(self.mixer.ok and not keys and bool(self.S.get("active")))
        gains = {k: (self.snd(k)["v"] / 100) ** 2 * 1.4 for k in keys}
        master = 0 if self.muted else (self.master / 100) ** 2 * 1.5
        try:
            self.mixer.tick(keys, gains, master)
        except Exception as e:  # noqa: BLE001
            if not getattr(self, "_snd_err", False):
                self._snd_err = True
                self.E.log(f"Звук: ошибка воспроизведения: {e}")

    # ---------- опрос ядра ----------
    def poll(self):
        try:
            self.S = json.loads(self.E.snapshot)
        except Exception:  # noqa: BLE001
            return
        if not self.S:
            return
        self.render_head()
        if self.S["rx_ver"] != self.RX["ver"]:
            self.RX = self.E.receivers_json()
            self.RXI = {r["url"]: i for i, r in enumerate(self.RX["list"])}
            self.render_rx()
        self.render_cards()
        t = self.tabs.currentIndex()
        if t == 0:
            self.render_visible()
        elif t == 1:
            self.update_map()
            if self.map_info_box.isVisible():
                self.map_polar.now = self.S["now"]
                self.map_polar.update()
        elif t == 2:
            key = f"{self.S['n_sessions']}|{self.S['mode']}|{self.S['rx_ver']}"
            if key != getattr(self, "_sk", None) or time.time() - getattr(self, "_sk_t", 0) > 15:
                self._sk, self._sk_t = key, time.time()
                self.load_schedule()
            else:
                self.timeline.now = self.S["now"]
                self.timeline.update()
                self.render_home()
        elif t == 3 and time.time() - getattr(self, "_rx_t", 0) > 5:
            self._rx_t = time.time()
            self.render_rx(angles_only=True)
        elif t == 4 and self.S["gallery_ver"] != self.gal["ver"]:
            self.load_gallery()
        elif t == 5:
            self.render_sat_info()
        self.poll_log()

    def _tab_changed(self, i):
        self.save_state()
        if i == 1:
            self.update_map()
        elif i == 2:
            self.load_schedule()
        elif i == 3:
            self.render_rx()
        elif i == 4:
            self.load_gallery()
        elif i == 5:
            self.load_settings()

    def poll_log(self):
        if self.S.get("log_seq") == self.log_seq:
            return
        with self.E.lock:
            rows = [l for s, l in self.E.logs if s > self.log_seq]
            self.log_seq = self.E.log_seq
        if rows:
            sb = self.log.verticalScrollBar()
            at_end = sb.value() >= sb.maximum() - 4
            self.log.appendPlainText("\n".join(rows))
            if at_end:
                sb.setValue(sb.maximum())

    # ---------- шапка ----------
    def render_head(self):
        s = self.S
        sat = s.get("sat")
        now = s["now"]
        m = s["mode"]
        live = m in ("сеанс", "тест")
        busy = m in ("проверка", "поиск", "пересборка", "расчёт", "запуск")
        dc = C["live"] if live else C["warn"] if busy else "#e8e8e8"
        self.dot.setStyleSheet(f"background:{dc};border-radius:4px")
        self.conn.setText(m)
        self.v_name.setText(sat["name"] if sat else "орбита не загружена")
        self.v_sub.setText(f"NORAD {sat['norad']} · TLE {sat['age']:.1f} сут · {s['freq']:.3f} МГц" if sat else "")
        if sat:
            self.v_name.setToolTip(f"Источник TLE: {sat['src']}")
        ph = {"сеанс": "SESSION", "тест": "TEST", "проверка": "CHECK", "поиск": "SCAN", "пересборка": "REDECODE",
              "расчёт": "PLAN", "запуск": "BOOT"}.get(m, "IDLE" if s["auto"] else "HOLD")
        if s.get("error"):
            txt, bc = s["error"], C["bad"]
        elif s.get("session"):
            ss = s["session"]
            txt = (f"{'тест' if ss['test'] else 'сеанс'} {tm(ss['a'])}–{tm(ss['b'])} · осталось {dur(ss['b'] - now)} · "
                   f"слушаю {len(s['active'])}")
            bc = C["live"]
        elif s.get("next"):
            nx = s["next"]
            txt = f"следующий сеанс {dm(nx['a'])} {tm(nx['a'])}–{tm(nx['b'])} · приёмников {nx['n_rx']}"
            if not s["auto"]:
                txt += " · авто-приём выключен"
            bc = C["warn"] if busy or not s["auto"] else "#ffffff"
        else:
            txt, bc = "сеансов до конца срока нет", C["faint"]
        self.st_text.setText(txt)
        self.st_text.setToolTip(txt)
        self.st_ph.setText(ph)
        if bc != getattr(self, "_stc", None):
            self._stc = bc
            self.st_box.setStyleSheet(f"#stbox{{background:#0b0b0b;border:none;border-left:2px solid {bc}}}"
                                      f"#stbox QLabel{{background:transparent}}")
        if s.get("session"):
            self.met.setText("T+" + dur(now - s["session"]["a"]))
            self.met.setStyleSheet(f"color:{C['live']}")
        elif s.get("next"):
            self.met.setText("T−" + dur(s["next"]["a"] - now))
            self.met.setStyleSheet(f"color:{C['text']}")
        else:
            self.met.setText("T−--:--")
        self.status.setText(("⚠ " + s["error"]) if s.get("error") else (s.get("status") or "—"))
        # табло
        if sat:
            self.hud_sat_k.setText(sat["name"][:22])
            self.hud["sat"][0].setText(f"{sat['lat']:+.1f}° {sat['lon']:+.1f}°")
            self.hud["sat"][1].setText(f"высота {sat['alt']:.0f} км")
        if s.get("next"):
            nx = s["next"]
            self.hud["next"][0].setText(f"{tm(nx['a'])}<span style='font-size:13px;color:{C['muted']}'> {dm(nx['a'])}</span>")
            self.hud["next"][1].setText(f"через {dur(nx['a'] - now)} · {nx['n_rx']} пр.")
        else:
            self.hud["next"][0].setText("—")
            self.hud["next"][1].setText("")
        el = s.get("el") or []
        nvis = sum(1 for e in el if e > 0)
        n = len(s["active"])
        self.hud["rx"][0].setText(f"<span style='color:{C['live'] if n else C['text']}'>{n}</span>"
                                  f"<span style='font-size:16px;color:{C['muted']}'> / {s['max_rx']}</span>")
        self.hud["rx"][1].setText(f"видят спутник: {nvis}")
        self.hud["img"][0].setText(str(s["images"]) if s.get("session") else "—")
        self.hud["img"][1].setText(f"всего в галерее: {len(self.gal['list'])}" if self.gal["list"] else "")
        if s.get("session"):
            lh = "Сейчас слушаю: " + ", ".join(esc(a["short"]) for a in s["active"])
        elif s.get("next"):
            nx = s["next"]
            lh = "Лучшие в ближайшем сеансе: " + ", ".join(f"{esc(b[0])} {round(b[1])}°" for b in nx["best"])
        else:
            lh = ""
        self.live_head.setText(lh)
        self.auto_cb.setChecked(bool(s["auto"]))
        busy = m in ("сеанс", "тест", "проверка", "поиск", "пересборка")
        self.b_stop.setEnabled(m in ("сеанс", "тест", "проверка"))
        self.b_skip.setEnabled(bool(s.get("next")))
        self.b_test.setEnabled(not busy)

    # ---------- эфир ----------
    def render_cards(self):
        act = self.S.get("active") or []
        keys = {a["key"] for a in act}
        for k in list(self.cards):
            if k not in keys:
                c = self.cards.pop(k)
                self.cards_flow.removeWidget(c)
                c.deleteLater()
        hear = set(self.playing()) if not self.muted else set()
        for a in act:
            c = self.cards.get(a["key"])
            if c is None:
                c = RxCard(self, a)
                self.cards[a["key"]] = c
                self.cards_flow.addWidget(c)
            c.update_from(a, a["key"] in hear)
        self.no_active.setVisible(not act)
        self.cards_box.setVisible(bool(act))
        cb = self.S.get("comb")
        in_sess = bool(self.S.get("session"))
        self.comb_panel.setVisible(bool(cb) or in_sess)
        if cb and cb["ver"] != self.comb_ver:
            self.comb_ver = cb["ver"]
            b = self.E.live_comb_png()
            if b:
                pm = QtGui.QPixmap()
                pm.loadFromData(b)
                self.comb_img.setPixmap(pm.scaled(self.comb_img.size(), Qt.KeepAspectRatio, Qt.FastTransformation))
            self.comb_txt.setText(cb["text"])
            self.comb_bar.setValue(int(cb["filled"] / 240 * 100))
        elif not cb and self.comb_ver != -1 and in_sess:
            self.comb_ver = -1
            self.comb_img.clear()
            self.comb_img.setText("ждём первых строк")
            self.comb_txt.setText("")
            self.comb_bar.setValue(0)

    def render_visible(self):
        el = self.S.get("el") or []
        act = {a["url"] for a in self.S.get("active") or []}
        lst = [(r, el[i]) for i, r in enumerate(self.RX["list"]) if i < len(el) and el[i] > 0]
        lst.sort(key=lambda x: -x[1])
        self.vis_title.setText(f"Видят спутник сейчас · {len(lst)}")
        if not lst:
            self.vis_list.setText(f"<span style='color:{C['muted']}'>Спутник сейчас не над приёмниками из списка.</span>")
            return
        parts = []
        for r, e in lst[:80]:
            col = C["live"] if r["url"] in act else (C["text"] if e >= self.S["min_el"] else C["muted"])
            parts.append(f"<span style='color:{col}'>{esc(r['short'])}&nbsp;<b>{e:.0f}°</b></span>")
        self.vis_list.setText(" &nbsp;·&nbsp; ".join(parts))

    # ---------- карта ----------
    def rx_color(self, r, e, active):
        if active:
            return C["live"]
        if r["disabled"]:
            return "#2a2a2a"
        if r.get("bad") and not r.get("builtin"):
            return C["bad"]
        if e is not None and e >= self.S.get("min_el", 10):
            return "#ffffff"
        if e is not None and e > 0:
            return C["muted"]
        return "#5a5a5a"

    def map_points(self, sel_url=None, labels=True):
        act = {a["url"]: a for a in self.S.get("active") or []}
        el = self.S.get("el") or []
        out = []
        hide_bad = getattr(self, "hide_bad", False)
        for i, r in enumerate(self.RX["list"]):
            e = el[i] if i < len(el) else None
            a = act.get(r["url"])
            if hide_bad and r.get("bad") and not r.get("builtin") and not a and r["url"] != sel_url:
                continue
            out.append(dict(
                lon=r["lon"], lat=r["lat"], color=self.rx_color(r, e, a), data=r,
                z=3 if a else (2 if (e or -1) > 0 else 0 if r["disabled"] else 1), selected=r["url"] == sel_url,
                ring=(a["strength"] / 100) if a else 0, ringColor=qcolor_for(a["strength"]) if a else None,
                label=r["short"] if labels else None, always=bool(a) and labels,
                tip=f"{r['short']} — {r['name']}" + (f" · {e:.0f}°" if e is not None else "")
                    + (f" · SSTV {a['strength']:.0f}%" if a else "") + (" · не прошёл проверку" if r.get("bad") else "")
                    + (" · выключен" if r["disabled"] else "")
                    + (f" · картинка в {r['hist']['success']} из {r['hist']['listened']} сеансов" if r.get("hist") else "")))
        return out

    def update_map(self):
        m = self.map
        m.points = self.map_points(self.sel_rx and self.sel_rx["url"])
        h = self.S.get("home")
        m.home_pt = (h["lon"], h["lat"], h["name"]) if h and h["show"] else None
        sat = self.S.get("sat")
        if sat:
            m.set_sat(sat["lat"], sat["lon"], sat["name"], self.S["now"])
            m.foot = (sat["alt"], self.S["min_el"])
        if time.time() - self._track_t > 20:
            self._track_t = time.time()
            run_bg(self.E.track_json, self._got_track)
        m.update()
        if self.sel_rx:
            self.show_rx_info(self.sel_rx, refresh=True)

    def _got_track(self, t):
        if not (isinstance(t, dict) and t.get("past")):
            self._track_t = time.time() - 17          # орбита ещё не загружена — повторить через 3 с
            return
        if True:
            self.map.track = t
            self.map.update()

    def _passes_html(self, ps):
        if isinstance(ps, Exception):
            return f"<span style='color:{C['bad']}'>{esc(ps)}</span>"
        if not ps:
            return f"<span style='color:{C['muted']}'>пролётов выше мин. угла нет</span>"
        return (f"<table cellspacing=0 cellpadding=2 style=\"font-family:'{MONO}';font-size:12px\">"
                + "".join(f"<tr><td>{wd(p['aos'])} {dm(p['aos'])}</td><td>&nbsp;{tm(p['aos'])}–{tm(p['los'])}</td>"
                          f"<td align=right>&nbsp;{p['max_el']:.0f}°</td></tr>" for p in ps) + "</table>")

    def show_rx_info(self, r, refresh=False):
        self.sel_rx = r
        i = self.RXI.get(r["url"])
        el = self.S.get("el") or []
        e = el[i] if i is not None and i < len(el) else None
        a = next((x for x in self.S.get("active") or [] if x["url"] == r["url"]), None)
        if not refresh or getattr(self, "_info_url", None) != r["url"]:
            self._info_url = r["url"]
            self._info_passes = f"<span style='color:{C['muted']}'>считаю пролёты…</span>"
            url = r["url"]

            def done(ps, url=url):
                if self._info_url == url:
                    self._info_passes = self._passes_html(ps)
                    self.show_rx_info(self.sel_rx, refresh=True)
            run_bg(lambda: self.E.passes_json(r["key"]), done)
            run_bg(lambda: self.E.pass_track(r["key"]),
                   lambda tr, url=url: self._info_url == url and self.map_polar.set_track(tr))
        self.map_polar.now = self.S.get("now", time.time())
        self.map_polar.update()
        h = r.get("hist")
        html = (f"<b style='font-size:14px'>{esc(r['short'])}</b><br><span style='color:{C['muted']}'>{esc(r['name'])}</span><br>"
                f"<a href='{esc(r['url'])}' style='color:#ffffff'>{esc(r['url'])}</a><br>"
                f"{'OpenWebRX' if r['kind'] == 'owrx' else 'WebSDR'} · {r['lat']:.4f}, {r['lon']:.4f}"
                + (f" · спутник {e:.0f}°" if e is not None else "")
                + (f"<br><span style='color:{C['live']}'>слушаю: {esc(a['state'])}, SSTV {a['strength']:.0f}%</span>" if a else "")
                + (f"<br><span style='color:{C['bad']}'>не прошёл проверку: {esc(r['bad'])}</span>" if r.get("bad") else "")
                + (f"<br><span style='color:{C['muted']}'>выключен</span>" if r["disabled"] else "")
                + f"<br><span style='color:{C['muted']}'>снимков с него: {r['images']}"
                + (f" · картинка в {h['success']} сеансах из {h['listened']}" if h else " · истории нет") + "</span>"
                + f"<br><br>Ближайшие пролёты:<br>{self._info_passes}"
                + "<br>Небо над приёмником, текущий или ближайший пролёт:"
                + f"<br><a href='close' style='color:{C['muted']}'>закрыть</a>")
        self.map_info.setText(html)
        try:
            self.map_info.linkActivated.disconnect()
        except TypeError:
            pass
        self.map_info.setOpenExternalLinks(False)
        self.map_info.linkActivated.connect(self._info_link)
        self.map_info_box.show()
        if not refresh:
            self.update_map()

    def _info_link(self, link):
        if link == "close":
            self.sel_rx = None
            self.map_info_box.hide()
            self.update_map()
        else:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(link))

    # ---------- расписание ----------
    def load_schedule(self):
        self.sched = self.E.schedule_json()
        self.render_schedule()
        s = self.E.settings
        key = (s.get("home_lat"), s.get("home_lon"), s.get("home_alt"), self.S.get("sat") and self.S["sat"]["norad"])
        if key != self._home_key or time.time() - getattr(self, "_home_t", 0) > 300:
            self._home_key, self._home_t = key, time.time()
            run_bg(self.E.home_passes, self._got_home)
            run_bg(lambda: self.E.pass_track("__home__", self.S.get("min_el", 10)), self.home_polar.set_track)
        else:
            self.render_home()

    def _got_home(self, ps):
        self.home_ps = ps if isinstance(ps, list) else None
        self.timeline.home = self.home_ps or []
        self.timeline.update()
        self.render_home()

    def render_home(self):
        ps = self.home_ps
        has = ps is not None
        for x in (self.home_next, self.home_sub, self.home_polar, self.home_tbl):
            x.setVisible(has)
        self.home_hint.setVisible(not has)
        h = self.S.get("home")
        self.home_title.setText(f"над моим местом · {h['name']}" if h else "над моим местом")
        if not has:
            return
        now = self.S.get("now", time.time())
        nxt = next((p for p in ps if p["max_el"] >= self.S.get("min_el", 10)), ps[0] if ps else None)
        if nxt:
            if nxt["aos"] <= now <= nxt["los"]:
                self.home_next.setText(f"<span style='color:{C['green']}'>сейчас</span>"
                                       f"<span style='font-size:13px;color:{C['muted']}'> до {tm(nxt['los'])}</span>")
            else:
                self.home_next.setText(f"{tm(nxt['aos'])}<span style='font-size:13px;color:{C['muted']}'> "
                                       f"{wd(nxt['aos'])} {dm(nxt['aos'])} · через {dur(nxt['aos'] - now)}</span>")
            self.home_sub.setText(f"следующий хороший пролёт: до {nxt['max_el']:.0f}°, {nxt['dir']}, "
                                  f"{dur(nxt['los'] - nxt['aos'])}"
                                  + ("" if nxt["sstv"] else " · уже после конца SSTV"))
        else:
            self.home_next.setText("—")
            self.home_sub.setText("пролётов в ближайшие 3 суток нет")
        self.home_polar.now = now
        self.home_polar.update()
        t = self.home_tbl
        t.setRowCount(len(ps))
        for i, p in enumerate(ps):
            good = p["max_el"] >= self.S.get("min_el", 10)
            vals = [f"{wd(p['aos'])} {dm(p['aos'])}", f"{tm(p['aos'])}–{tm(p['los'])}", f"{p['max_el']:.0f}°", p["dir"]]
            for j, val in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(val)
                it.setFont(mfont(12))
                live = p["aos"] <= now <= p["los"]
                it.setForeground(QtGui.QColor(C["green"]) if live else Q["text"] if good else Q["muted"])
                if not p["sstv"]:
                    it.setToolTip("после окончания передач SSTV")
                t.setItem(i, j, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)

    def render_schedule(self):
        sc = self.sched
        if not sc:
            return
        now = self.S.get("now", time.time())
        lst = sc["list"]
        fut = [x for x in lst if not x["done"]]
        self.sched_head.setText(f"Сеансов впереди: <b>{len(fut)}</b> · до {dm(sc['until'])} {tm(sc['until'])} · "
                                f"мин. угол {self.S.get('min_el', 10):g}° · каждый сеанс начинается заранее на «начинать раньше»")
        self.timeline.items, self.timeline.now = lst, now
        self.timeline.update()
        rows = [x for x in lst if not (x["done"] and x["b"] < now - 6 * 3600)]
        t = self.sched_tbl
        t.setRowCount(len(rows))
        last = ""
        for i, x in enumerate(rows):
            day = f"{wd(x['a'])} {dm(x['a'])}"
            st = ("идёт" if x["running"] else "прошёл" if x["done"] else "пропущу" if x["skip"]
                  else f"через {dur(x['a'] - now)}")
            vals = [day if day != last else "", f"{tm(x['a'])}–{tm(x['b'])}", dur(x["b"] - x["a"]), str(x["n_rx"]),
                    ", ".join(f"{b[0]} {round(b[1])}°" for b in x["best"]), st]
            last = day
            col = (Q["live"] if x["running"] else Q["muted"] if (x["done"] or x["skip"]) else Q["text"])
            for j, val in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(val)
                it.setForeground(col)
                if j in (0, 1, 2, 3):
                    it.setFont(mfont(12))
                if x["skip"] and j < 5:
                    f = it.font()
                    f.setStrikeOut(True)
                    it.setFont(f)
                t.setItem(i, j, it)
            if not x["done"]:
                b = QtWidgets.QPushButton("Вернуть" if x["skip"] else "Пропустить")
                b.setFont(hfont(10, True, 1.4))
                b.setStyleSheet("padding:2px 8px")
                b.clicked.connect(lambda _, sid=x["id"]: (self.cmd(dict(cmd="skip", id=sid)),
                                                          QtCore.QTimer.singleShot(400, self.load_schedule)))
                t.setCellWidget(i, 6, b)
            else:
                t.removeCellWidget(i, 6)
        t.resizeColumnsToContents()
        t.setColumnWidth(6, 130)
        t.verticalHeader().setDefaultSectionSize(32)
        t.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)

    # ---------- приёмники ----------
    def _sel(self):
        return sorted(self.rx_checked)

    def _need_sel(self):
        if not self.rx_checked:
            QtWidgets.QMessageBox.information(self, "Приёмники", "Отметь приёмники галочками в первом столбце.")
            return False
        return True

    def _check_all(self):
        if QtWidgets.QMessageBox.question(
                self, "Проверка", "Проверить все приёмники? Это займёт около 10 минут. "
                "Проверка сама прервётся за 20 с до сеанса.") == QtWidgets.QMessageBox.Yes:
            self.cmd(dict(cmd="check"))

    def _off_dead(self):
        L = self.RX["list"]
        bad = [r["url"] for r in L if r.get("bad") and not r["disabled"] and not r.get("builtin")]
        useless = [r["url"] for r in L if not r["disabled"] and not r.get("bad") and r.get("hist")
                   and r["hist"]["listened"] >= 3 and r["hist"]["success"] == 0]
        if not bad and not useless:
            QtWidgets.QMessageBox.information(self, "Приёмники", "Включённых неработающих приёмников нет.")
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Выключить неработающие")
        box.setText(f"Выключить {len(bad)} приёмников, не прошедших проверку?\n"
                    "Их можно включить обратно зелёной кнопкой или фильтром «выключенные».")
        cb = QtWidgets.QCheckBox(f"и ещё {len(useless)}, не давших ни одной картинки за 3+ сеанса")
        cb.setEnabled(bool(useless))
        box.setCheckBox(cb)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if box.exec_() != QtWidgets.QMessageBox.Yes:
            return
        urls = bad + (useless if cb.isChecked() else [])
        if urls:
            self.cmd(dict(cmd="rx_enable", urls=urls, on=False))

    def _test_sel(self):
        if self._need_sel():
            self.cmd(dict(cmd="test", urls=self._sel(), seconds=60))
            self.tabs.setCurrentIndex(0)

    def _scan(self):
        if QtWidgets.QMessageBox.question(
                self, "Обновление списка", "Искать приёмники на rx-tx.info, ReceiverBook и websdr.org? Откроется окно сайта, "
                "проверка всех найденных займёт 5–10 минут. Прежние приёмники не теряются.") == QtWidgets.QMessageBox.Yes:
            self.cmd(dict(cmd="scan"))

    def _rx_match(self, r, i):
        q = self.rx_search.text().strip().lower()
        f = self.rx_filter.currentData()
        if q and q not in (r["short"] + " " + r["name"] + " " + r["url"]).lower():
            return False
        el = self.S.get("el") or []
        e = el[i] if i < len(el) else None
        return {"ok": not r.get("bad") and not r["disabled"], "bad": bool(r.get("bad")), "off": r["disabled"],
                "user": r["user"] or r["edited"], "vis": e is not None and e > 0,
                "useless": bool(r.get("hist")) and r["hist"]["listened"] >= 3 and r["hist"]["success"] == 0,
                "good": bool(r.get("hist")) and r["hist"]["rating"] >= 0.6}.get(f, True)

    def render_rx(self, *_, angles_only=False):
        t = self.rx_tbl
        el = self.S.get("el") or []
        act = {a["url"] for a in self.S.get("active") or []}
        if angles_only and getattr(self, "_rx_rows", None):
            for row, i in enumerate(self._rx_rows):
                e = el[i] if i < len(el) else None
                it = t.item(row, 5)
                if it:
                    it.setText("" if e is None else f"{e:.0f}°" if e > 0 else "—")
            return
        t.blockSignals(True)
        rows = [i for i, r in enumerate(self.RX["list"]) if self._rx_match(r, i)]
        self._rx_rows = rows
        t.setRowCount(len(rows))
        mono = mfont(12)
        for row, i in enumerate(rows):
            r = self.RX["list"][i]
            e = el[i] if i < len(el) else None
            c0 = QtWidgets.QTableWidgetItem()
            c0.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            c0.setCheckState(Qt.Checked if r["url"] in self.rx_checked else Qt.Unchecked)
            c0.setData(Qt.UserRole, r["url"])
            c1 = QtWidgets.QTableWidgetItem()
            tags = (" · вручную" if r["user"] else " · изменён" if r["edited"] else "") + (" · встроенный" if r["builtin"] else "")
            c2 = QtWidgets.QTableWidgetItem(f"{r['short']}{tags}\n{r['name']}")
            c2.setToolTip(r["url"])
            c3 = QtWidgets.QTableWidgetItem("OWRX" if r["kind"] == "owrx" else "WebSDR")
            c4 = QtWidgets.QTableWidgetItem(f"{r['lat']:.3f}, {r['lon']:.3f}")
            c5 = QtWidgets.QTableWidgetItem("" if e is None else f"{e:.0f}°" if e > 0 else "—")
            if r["url"] in act:
                st, col = "слушаю", Q["live"]
            elif r.get("bad"):
                st, col = r["bad"][:40], Q["bad"]
            elif r.get("checked"):
                st, col = "работает", Q["text"]
            else:
                st, col = "не проверялся", Q["muted"]
            c6 = QtWidgets.QTableWidgetItem(st + (f"\n{r['checked'].replace('T', ' ')}" if r.get("checked") else ""))
            c6.setForeground(col)
            c6.setToolTip(r.get("bad") or "")
            h = r.get("hist")
            c7 = QtWidgets.QTableWidgetItem(f"{h['success']}/{h['listened']}" if h else "—")
            if h:
                c7.setToolTip(f"Картинка в {h['success']} сеансах из {h['listened']}, снимков {h['images']}, "
                              f"рейтинг {h['rating']:.2f}")
                c7.setForeground(Q["text"] if h["rating"] >= 0.6 else Q["bad"] if h["rating"] <= 0.2 else Q["muted"])
            else:
                c7.setToolTip("Ещё ни разу не слушали в сеансе")
                c7.setForeground(Q["faint"])
            for c in (c3, c4, c5, c7):
                c.setFont(mono)
            for j, c in enumerate((c0, c1, c2, c3, c4, c5, c6, c7)):
                if j >= 2:
                    c.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                t.setItem(row, j, c)
            t.setCellWidget(row, 1, self._onoff(r))
            if r["disabled"]:
                for j in (2, 3, 4, 5):
                    t.item(row, j).setForeground(Q["muted"])
        t.resizeRowsToContents()
        t.blockSignals(False)
        self.rx_count.setText(f"{len(rows)} из {len(self.RX['list'])}")
        self.pick.points = self.map_points(self.form_url, labels=False)
        self.pick.update()

    def _rx_item_changed(self, it):
        url = it.data(Qt.UserRole)
        if it.column() == 0:
            (self.rx_checked.add if it.checkState() == Qt.Checked else self.rx_checked.discard)(url)

    def _onoff(self, r):
        """Кнопка вкл/выкл приёмника: зелёная — используется в сеансах, красная — выключен."""
        w = QtWidgets.QWidget()
        w.setAttribute(Qt.WA_TranslucentBackground)
        w.setStyleSheet("background:transparent")
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        b = QtWidgets.QPushButton()
        b.setCheckable(True)
        b.setChecked(not r["disabled"])
        b.setFixedSize(58, 24)
        b.setFont(hfont(11, True, 1.6))
        b.setCursor(Qt.PointingHandCursor)

        def paint(on):
            col, bg = (C["green"], "rgba(48,209,88,40)") if on else (C["bad"], "rgba(255,59,48,40)")
            b.setText("вкл" if on else "выкл")
            b.setToolTip("Используется в сеансах — нажми, чтобы выключить" if on else "Выключен — нажми, чтобы включить")
            b.setStyleSheet(f"QPushButton{{color:{col};background:{bg};border:1px solid {col};border-radius:2px;padding:0}}")
        paint(b.isChecked())
        b.toggled.connect(lambda on, url=r["url"]: (paint(on), self.cmd(dict(cmd="rx_enable", urls=[url], on=on))))
        lay.addWidget(b)
        return w

    def _rx_cell(self, row, col):
        if col >= 2 and row < len(self._rx_rows):
            self.fill_form(self.RX["list"][self._rx_rows[row]])

    def fill_form(self, r):
        self.form_url = r["url"] if r else None
        self.f_url.setText(r["url"] if r else "")
        self.f_kind.setCurrentIndex(1 if r and r["kind"] == "websdr" else 0)
        self.f_lat.setText(f"{r['lat']}" if r else "")
        self.f_lon.setText(f"{r['lon']}" if r else "")
        self.f_name.setText(r["name"] if r else "")
        self.f_short.setText(r["short"] if r else "")
        self.form_title.setText(f"<b>Приёмник: {esc(r['short'])}</b>" if r else "<b>Добавить приёмник</b>")
        self.b_rxdel.setVisible(bool(r and (r["user"] or r["edited"])))
        self.b_rxdel.setText("Удалить приёмник" if r and r["user"] else "Вернуть исходные данные")
        self.form_msg.setText("")
        self.pick.marker = (r["lon"], r["lat"]) if r else None
        if r:
            self.pick.center(r["lon"], r["lat"], min_zoom=6)
        self.pick.points = self.map_points(self.form_url, labels=False)
        self.pick.update()
        self.rx_passes.setText("")
        self.rx_polar.setVisible(bool(r))
        self.rx_polar.set_track(None)
        if r:
            self.rx_polar.now = self.S.get("now", time.time())
            run_bg(lambda: self.E.pass_track(r["key"]),
                   lambda tr, url=r["url"]: self.form_url == url and self.rx_polar.set_track(tr))
        if r:
            url = r["url"]
            run_bg(lambda: self.E.passes_json(r["key"]),
                   lambda ps: self.form_url == url and self.rx_passes.setText(
                       f"<span style='color:{C['muted']}'>Ближайшие пролёты над ним:</span><br>"
                       + self._passes_html(ps[:6] if isinstance(ps, list) else ps)))

    def _pick(self, data, lon, lat):
        if data is not None and not self.form_url and not self.f_url.text():
            self.fill_form(data)
            return
        self.f_lat.setText(f"{lat:.4f}")
        self.f_lon.setText(f"{lon:.4f}")
        self.pick.marker = (lon, lat)
        self.pick.update()

    def _msg(self, lab, text, ok):
        lab.setText(text)
        lab.setStyleSheet(f"color:{C['text'] if ok else C['bad']}")

    def _rx_save(self):
        try:
            lat = float(self.f_lat.text().replace(",", "."))
            lon = float(self.f_lon.text().replace(",", "."))
            assert -90 <= lat <= 90 and -180 <= lon <= 180
        except Exception:  # noqa: BLE001
            return self._msg(self.form_msg, "Координаты: широта −90…90, долгота −180…180.", False)
        if not self.f_url.text().strip():
            return self._msg(self.form_msg, "Нужен адрес приёмника.", False)
        self.cmd(dict(cmd="rx_save", data=dict(
            url=self.f_url.text().strip(), kind=self.f_kind.currentData(), lat=lat, lon=lon,
            name=self.f_name.text().strip(), short=self.f_short.text().strip(), orig_url=self.form_url or "")))
        busy = self.S.get("mode") in ("сеанс", "тест")
        self._msg(self.form_msg, "Сохранено. Расписание пересчитается" + (" после сеанса." if busy else "."), True)

    def _rx_del(self):
        if self.form_url:
            self.cmd(dict(cmd="rx_delete", url=self.form_url))
            self.fill_form(None)

    # ---------- галерея ----------
    def _gal_kind(self, b):
        self.gal_kind = b.property("k")
        self.save_state()
        self.render_gallery()

    def _redecode(self):
        if QtWidgets.QMessageBox.question(
                self, "Пересборка", "Пересобрать все записи нынешним декодером и сложить стопки повторов? "
                "Это займёт несколько минут; старые картинки не удаляются.") == QtWidgets.QMessageBox.Yes:
            self.cmd(dict(cmd="redecode"))

    def show_report(self, rep):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle(f"Отчёт сеанса {rep['session']}")
        d.resize(760, 520)
        v = QtWidgets.QVBoxLayout(d)
        good = sum(1 for x in rep["rx"] if x["images"])
        head = QtWidgets.QLabel(f"{rep['sat']} · {dm(rep['a'])} {tm(rep['a'])}–{tm(rep['b'])} · приёмников {len(rep['rx'])}, "
                                f"с картинкой {good} · картинок {rep['images']} · склеек {len(rep['combined'])}")
        head.setFont(hfont(13, True, 1.5))
        v.addWidget(head)
        t = QtWidgets.QTableWidget(len(rep["rx"]), 6)
        t.setHorizontalHeaderLabels(["Приёмник", "Макс. угол", "Слушал", "SSTV до", "Картинок", "Лучшая, строк"])
        self._table_style(t)
        t.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for i, x in enumerate(rep["rx"]):
            vals = [f"{x['short']}  {x['name'][:40]}", f"{x['max_el']:.0f}°", dur(x["secs"]), f"{x['max_strength']}%",
                    str(x["images"]), str(x["max_lines"] or "")]
            for j, val in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(val)
                if j:
                    it.setFont(mfont(12))
                it.setForeground(Q["text"] if x["images"] else Q["muted"])
                t.setItem(i, j, it)
        v.addWidget(t, 1)
        d.exec_()

    def load_gallery(self):
        def done(g):
            if isinstance(g, Exception):
                return
            self.reports = self.E.reports()
            self.gal = dict(ver=self.S.get("gallery_ver"), list=g)
            sats = sorted({x["sat"] for x in g})
            cur = self.gal_sat.currentData()
            self.gal_sat.blockSignals(True)
            self.gal_sat.clear()
            self.gal_sat.addItem("все спутники", "")
            for s in sats:
                self.gal_sat.addItem(s, s)
            i = self.gal_sat.findData(cur)
            self.gal_sat.setCurrentIndex(max(0, i))
            self.gal_sat.blockSignals(False)
            self.render_gallery()
        self.gal["ver"] = self.S.get("gallery_ver")
        run_bg(self.E.gallery, done)

    def thumb(self, path):
        pm = self.thumbs.get(path)
        if pm is None:
            pm = QtGui.QPixmap(str(self.E.out / path))
            pm = pm.scaled(176, 132, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation) if not pm.isNull() else pm
            self.thumbs[path] = pm
        return pm

    def render_gallery(self):
        L = list(self.gal["list"])
        k = self.gal_kind
        if k == "best":
            red = {g["session"] for g in L if g["kind"] == "redecoded"}
            L = [g for g in L if g["kind"] in ("combined", "stacked")
                 or (g["kind"] == "redecoded" if g["session"] in red else g["kind"] == "live")]
        elif k != "all":
            L = [g for g in L if g["kind"] == k]
        sat = self.gal_sat.currentData()
        if sat:
            L = [g for g in L if g["sat"] == sat]
        q = self.gal_search.text().strip().lower()
        if q:
            L = [g for g in L if q in (g["short"] + " " + g["name"] + " " + g["rx"]).lower()]
        by_q = self.gal_sort.currentData() == "q"
        L.sort(key=(lambda g: -(g.get("q") or 0)) if by_q else (lambda g: -g["t"]))
        self.gal_view = L
        self.gal_count.setText(f"снимков: {len(L)}")
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        if not L:
            e = QtWidgets.QLabel("Снимков пока нет.")
            e.setAlignment(Qt.AlignCenter)
            e.setStyleSheet(f"color:{C['muted']};padding:30px")
            v.addWidget(e)
        last, flow = None, None
        for i, g in enumerate(L):
            key = "q" if by_q else g["session"]
            if key != last:
                last = key
                if not by_q:
                    n = sum(1 for x in L if x["session"] == g["session"])
                    try:
                        t = datetime.strptime(g["session"][:13], "%Y%m%d_%H%M").timestamp()
                        txt = f"Сеанс <b>{wd(t)} {dm(t)} {tm(t)}</b> · {esc(g['sat'])} · снимков {n}"
                    except ValueError:
                        txt = (f"<b>Стопки</b> · одна и та же картинка, сложенная из многих пролётов · {n}"
                               if g["session"] == "stack" else esc(g["session"]))
                    rep_ = self.reports.get(g["session"])
                    hrow = QtWidgets.QHBoxLayout()
                    hrow.setContentsMargins(0, 12, 0, 2)
                    if rep_:
                        good = sum(1 for x in rep_["rx"] if x["images"])
                        txt += f" · приёмников {len(rep_['rx'])}, с картинкой {good}"
                    hl = QtWidgets.QLabel(txt)
                    hl.setStyleSheet(f"color:{C['muted']}")
                    hrow.addWidget(hl)
                    if rep_:
                        rb = QtWidgets.QPushButton("отчёт")
                        rb.setFont(hfont(10, True, 1.4))
                        rb.setStyleSheet("padding:2px 10px")
                        rb.clicked.connect(lambda _, r=rep_: self.show_report(r))
                        hrow.addWidget(rb)
                    hrow.addStretch(1)
                    v.addLayout(hrow)
                box = QtWidgets.QWidget()
                flow = FlowLayout(box, 8)
                v.addWidget(box)
            b = QtWidgets.QToolButton()
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIcon(QtGui.QIcon(self.thumb(g["path"])))
            b.setIconSize(QtCore.QSize(176, 132))
            kind = {"combined": "склейка", "redecoded": "пересборка", "stacked": "стопка"}.get(g["kind"], "в эфире")
            tt = tm(g["t"], True) if g["t_known"] else f"{dm(g['t'])} {tm(g['t'])}"
            qq = f" · кач. {round(g['q'] * 100)}" if g.get("q") is not None else ""
            b.setText(f"{g['short'][:22]}\n{tt}{qq}\n{kind}" + (f" · {g['el']:.0f}°" if g.get("el") is not None else ""))
            b.setToolTip(f"{g['name']}\n{g['path']}")
            b.setObjectName("gitem_c" if g["combined"] else "gitem")
            b.setFixedWidth(190)
            b.clicked.connect(lambda _, i=i: Viewer(self, self.gal_view, i).exec_())
            flow.addWidget(b)
        v.addStretch(1)
        self.gal_scroll.setWidget(w)

    # ---------- настройки ----------
    def load_settings(self):
        s = self.E.settings
        self.s_norad.setText(str(s["norad"]))
        self.s_tle.setPlainText(s.get("tle_text") or "")
        self.s_preset.setCurrentIndex(0 if str(s["norad"]) == "25544" and not s.get("tle_text") else 1)
        self.s_freq.setValue(float(s["freq_mhz"]))
        self.s_max.setValue(int(s["max_rx"]))
        self.s_minel.setValue(float(s["min_el"]))
        self.s_sep.setValue(float(s["min_sep_km"]))
        self.s_lead.setValue(int(s["lead"]))
        self.s_tail.setValue(int(s["tail"]))
        self.s_yield.setValue(int(float(s["yield_s"])))
        try:
            dt = self.E.until().astimezone().replace(tzinfo=None)
            self.s_until.setDateTime(QtCore.QDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute))
        except Exception:  # noqa: BLE001
            pass
        self.s_noswitch.setChecked(bool(s["no_switch"]))
        self.s_headless.setChecked(bool(s["headless"]))
        self.s_auto.setChecked(bool(s["auto"]))
        self.s_hist.setChecked(bool(s.get("use_history", True)))
        self.s_hname.setText(s.get("home_name") or "")
        self.s_hlat.setText("" if s.get("home_lat") is None else f"{s['home_lat']}")
        self.s_hlon.setText("" if s.get("home_lon") is None else f"{s['home_lon']}")
        self.s_halt.setValue(int(s.get("home_alt") or 0))
        self.s_hshow.setChecked(bool(s.get("home_show", True)))
        if s.get("home_lat") is not None and s.get("home_lon") is not None:
            self.s_hpick.home_pt = (s["home_lon"], s["home_lat"], s.get("home_name") or "Я")
            self.s_hpick.center(s["home_lon"], s["home_lat"], min_zoom=4)
        self.render_sat_info()

    def render_sat_info(self):
        s = self.S.get("sat")
        self.sat_info.setText(f"Сейчас: {s['name']}, NORAD {s['norad']}\nTLE: {s['src']}, возраст {s['age']:.2f} сут.\n"
                              f"Высота {s['alt']:.0f} км, над точкой {s['lat']:.2f}°, {s['lon']:.2f}°"
                              if s else "орбита не загружена")

    def _preset(self, i):
        d = self.s_preset.itemData(i)
        if d:
            self.s_norad.setText(d)
            self.s_tle.setPlainText("")

    def _save_settings(self):
        tle = self.s_tle.toPlainText().strip()
        norad = self.s_norad.text().strip()
        if tle:
            import tle as tlemod
            try:
                nm, l1, l2 = tlemod.parse_tle(tle)
                norad = str(int(l1[2:7]))
            except Exception as e:  # noqa: BLE001
                return self._msg(self.set_msg, f"TLE не распознан: {e}", False)
        if not norad.isdigit():
            return self._msg(self.set_msg, "NORAD ID — целое число.", False)
        hl, ho = self.s_hlat.text().strip().replace(",", "."), self.s_hlon.text().strip().replace(",", ".")
        if bool(hl) != bool(ho):
            return self._msg(self.set_msg, "Моё место: нужны и широта, и долгота (или оба поля пустые).", False)
        try:
            hlat = float(hl) if hl else None
            hlon = float(ho) if ho else None
            assert hlat is None or (-90 <= hlat <= 90 and -180 <= hlon <= 180)
        except Exception:  # noqa: BLE001
            return self._msg(self.set_msg, "Моё место: широта −90…90, долгота −180…180.", False)
        dt = self.s_until.dateTime().toPyDateTime()
        self.cmd(dict(cmd="settings", data=dict(
            norad=int(norad), tle_text=tle, freq_mhz=self.s_freq.value(), mode=self.s_mode.currentText(),
            max_rx=self.s_max.value(), min_el=self.s_minel.value(), min_sep_km=self.s_sep.value(),
            lead=self.s_lead.value(), tail=self.s_tail.value(), yield_s=self.s_yield.value(),
            until=dt.strftime("%Y-%m-%dT%H:%M"), no_switch=self.s_noswitch.isChecked(),
            headless=self.s_headless.isChecked(), auto=self.s_auto.isChecked(),
            use_history=self.s_hist.isChecked(), home_lat=hlat, home_lon=hlon, home_alt=self.s_halt.value(),
            home_name=self.s_hname.text().strip() or "Я", home_show=self.s_hshow.isChecked())))
        self._home_key = None
        busy = self.S.get("mode") in ("сеанс", "тест")
        self._msg(self.set_msg, "Сохранено" + (". Спутник и расписание сменятся после сеанса." if busy else "."), True)

    # ---------- выход ----------
    def closeEvent(self, e):  # noqa: N802
        self.save_state()
        if self.S.get("mode") in ("сеанс", "тест", "проверка") and QtWidgets.QMessageBox.question(
                self, "Выход", "Идёт приём. Остановить его и выйти? Записанное сохранится и склеится.") != QtWidgets.QMessageBox.Yes:
            e.ignore()
            return
        self.t_poll.stop()
        self.t_snd.stop()
        self.hide()
        QtWidgets.QApplication.processEvents()
        print("Завершаю работу …")
        self.E.shutdown(timeout=90)
        e.accept()


CHECK_SVG = (UI_DIR / "check.svg").as_posix()
STYLE = f"""
QWidget {{ background:{C['bg']}; color:{C['text']}; }}
QFrame#top {{ background:#000; border-bottom:1px solid {C['line']}; }}
QFrame#top QLabel, QFrame#top QCheckBox, QFrame#top QToolButton, QFrame#top QSlider, QFrame#top QWidget {{ background:transparent; }}
QFrame#hud {{ background:{C['panel']}; border:1px solid {C['line']}; }}
QFrame#hudc {{ background:transparent; border:none; border-right:1px solid {C['line']}; }}
QFrame#hudl {{ background:transparent; border:none; }}
QFrame#hudc QLabel, QFrame#hudl QLabel {{ background:transparent; }}
QFrame#sect {{ background:{C['panel']}; border:1px solid {C['line']}; border-radius:2px; }}
QFrame#sect QLabel, QFrame#sect QCheckBox {{ background:transparent; }}
QFrame#infobox {{ background:rgba(0,0,0,225); border:1px solid {C['line']}; border-radius:2px; }}
QFrame#infobox QLabel {{ background:transparent; font-size:12px; }}
QLabel#overlay {{ background:rgba(0,0,0,215); border:1px solid {C['line']}; border-radius:2px; font-size:12px; }}
QTabWidget::pane {{ border:none; }}
QTabBar {{ background:#000; qproperty-drawBase:0; }}
QTabBar::tab {{ background:#000; color:{C['muted']}; padding:9px 18px; border:none; border-bottom:2px solid transparent; }}
QTabBar::tab:selected {{ color:{C['text']}; border-bottom:2px solid {C['live']}; }}
QTabBar::tab:hover {{ color:{C['text']}; }}
QPushButton, QToolButton {{ background:transparent; border:1px solid {C['line']}; border-radius:2px; padding:7px 12px; color:{C['text']}; }}
QPushButton:hover, QToolButton:hover {{ border-color:rgba(255,255,255,110); }}
QPushButton:pressed {{ padding-top:8px; padding-bottom:6px; }}
QPushButton:checked, QToolButton:checked {{ background:{C['text']}; color:#000; border-color:#fff; }}
QPushButton:disabled {{ color:#3a3a3a; border-color:#141414; }}
QPushButton[primary="true"] {{ background:{C['text']}; color:#000; border-color:#fff; }}
QPushButton[primary="true"]:hover {{ background:#fff; }}
QPushButton[danger="true"] {{ border-color:{C['bad']}; color:{C['bad']}; }}
QPushButton[danger="true"]:hover {{ background:{C['bad']}; color:#fff; }}
QPushButton[danger="true"]:disabled {{ border-color:#2a0d0b; color:#5a1a16; }}
QToolButton#gitem, QToolButton#gitem_c {{ background:{C['panel']}; padding:0 0 6px 0; font-size:11px; color:#cfcfcf; }}
QToolButton#gitem:hover, QToolButton#gitem_c:hover {{ border-color:#fff; }}
QToolButton#gitem_c {{ border-color:{C['live']}; }}
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{ background:#000; border:1px solid {C['line']}; border-radius:2px; padding:5px 8px; selection-background-color:#444; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus {{ border-color:rgba(255,255,255,140); }}
QComboBox QAbstractItemView {{ background:#111; border:1px solid {C['line']}; selection-background-color:#333; }}
QTableWidget {{ background:#000; alternate-background-color:#121212; border:1px solid {C['line']}; border-radius:2px; gridline-color:#111; selection-background-color:#262626; selection-color:{C['text']}; }}
QHeaderView::section {{ background:#000; color:{C['muted']}; border:none; border-bottom:1px solid {C['line']}; padding:7px 8px; }}
QTableWidget::item {{ border-bottom:1px solid #111; padding:4px; }}
QScrollArea {{ border:none; }}
QScrollBar:vertical {{ background:transparent; width:6px; }}
QScrollBar::handle:vertical {{ background:#333; min-height:30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QScrollBar:horizontal {{ background:transparent; height:6px; }}
QScrollBar::handle:horizontal {{ background:#333; }}
QSlider::groove:horizontal {{ height:2px; background:#333; }}
QSlider::handle:horizontal {{ background:#fff; width:10px; margin:-6px 0; border-radius:0; }}
QSlider::sub-page:horizontal {{ background:#fff; }}
QProgressBar {{ border:none; }}
QStatusBar {{ background:#000; border-top:1px solid {C['line']}; }}
QStatusBar QLabel {{ background:transparent; }}
QToolTip {{ background:#000; color:{C['text']}; border:1px solid #444; padding:4px; }}
QCheckBox {{ spacing:8px; }}
QCheckBox::indicator, QTableWidget::indicator {{ width:14px; height:14px; border:1px solid #6a6a6a; border-radius:2px; background:#000; }}
QCheckBox::indicator:hover, QTableWidget::indicator:hover {{ border-color:#fff; }}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{ background:{C['text']}; border-color:#fff; image:url({CHECK_SVG}); }}
QFrame#card QLabel {{ background:transparent; }}
QMessageBox, QDialog {{ background:#000; }}
QCalendarWidget QWidget {{ alternate-background-color:#111; }}
"""


def _quiet_console():
    """Запуск без консоли (pythonw): ошибки пишутся в sstv_out/console.log, журнал — как обычно в log.txt."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    logdir = HERE / "sstv_out"
    logdir.mkdir(exist_ok=True)
    f = open(logdir / "console.log", "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== запуск {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    if sys.stdout is None:
        import os
        sys.stdout = open(os.devnull, "w", encoding="utf-8")     # журнал и так пишется в sstv_out/log.txt
    if sys.stderr is None:
        sys.stderr = f


def _excepthook(tp, val, tb):
    """Необработанная ошибка в окне не роняет программу, а пишется в журнал."""
    import traceback
    txt = "".join(traceback.format_exception(tp, val, tb))
    try:
        sys.stderr.write(txt)
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        import iss_sstv
        iss_sstv.log(f"Ошибка интерфейса: {val!r} (подробности в sstv_out/console.log)")
    except Exception:  # noqa: BLE001
        pass


def main():
    import paths
    paths.ensure_defaults()
    _quiet_console()
    sys.excepthook = _excepthook
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)   # цвета в консоли
        except Exception:  # noqa: BLE001
            pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("sstv.iss.receiver")
        except Exception:  # noqa: BLE001
            pass
    QtWidgets.QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("SSTV МКС")
    icon = QtGui.QIcon(str(UI_DIR / "icon.ico"))
    icon.addFile(str(UI_DIR / "icon_1024.png"))
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    load_fonts()
    f = QtGui.QFont(BODY)
    f.setPixelSize(13)
    app.setFont(f)
    app.setStyleSheet(STYLE)
    if not rules_accepted() and RulesDialog(first=True).exec_() != QtWidgets.QDialog.Accepted:
        sys.exit(0)
    from engine import Engine
    eng = Engine(HERE)
    eng.start()
    w = Main(eng)
    w.setWindowIcon(icon)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
