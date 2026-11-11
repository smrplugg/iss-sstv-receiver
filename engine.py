"""Ядро для оконного интерфейса: расписание, сеансы, приёмники, галерея.

Работает в отдельном потоке и использует те же классы, что и консольная
версия (iss_sstv.py). Интерфейс читает готовый «снимок» состояния и шлёт
команды через очередь — ядро выполняет их между шагами приёма.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import queue
import re
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import iss_sstv as iss
import scan

from paths import DATA as HERE  # noqa: E402

DEFAULTS = dict(
    norad=25544, tle_text="", freq_mhz=437.550, mode="Robot 36",
    max_rx=8, min_el=10.0, min_sep_km=40.0, lead=60, tail=20, yield_s=20,
    no_switch=False, until="", auto=True, retune_khz=1.0,
    headless=True, disabled=[], use_history=True,
    home_lat=None, home_lon=None, home_alt=0.0, home_name="Я", home_show=True,
)
COMPASS = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ", "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]


def compass(az):
    return COMPASS[int((az % 360) / 22.5 + 0.5) % 16]
WF_N, WF_HOP, WF_ROWS = 512, 600, 140          # водопад: окно БПФ, шаг 50 мс, строк (7 с)
WF_LO, WF_HI = 13, 129                        # 300…3000 Гц при 12 кГц / 512


def _now():
    return datetime.now(timezone.utc)


def _ts(dt):
    return dt.timestamp() if dt else None


def subvis(vis, idx):
    v = object.__new__(iss.Visibility)
    v.t0, v.times = vis.t0, vis.times
    v.el = vis.el[:, idx]
    v.rxs = [vis.rxs[i] for i in idx]
    return v


class Engine(threading.Thread):
    def __init__(self, here: Path = HERE):
        super().__init__(daemon=True, name="engine")
        self.here = here
        self.out = here / "sstv_out"
        self.out.mkdir(exist_ok=True)
        iss.LOGFILE = self.out / "log.txt"
        self.p_settings = here / "settings.json"
        self.p_rx = here / "receivers.json"
        self.p_user = here / "receivers_user.json"
        self.p_check = here / "check.json"
        self.p_index = self.out / "gallery.json"
        iss.CHECK_PATH = self.p_check

        self.settings = dict(DEFAULTS)
        try:
            self.settings.update(json.loads(self.p_settings.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
        self.args = argparse.Namespace(test=False)
        self._apply_args()

        self.lock = threading.RLock()
        self.cmds: queue.Queue = queue.Queue()
        self.logs: deque = deque(maxlen=800)
        self.log_seq = 0
        self.status_line = ""
        self.audio: dict[str, deque] = {}
        self.aseq = 0
        self.orbit = None
        self.cfgs: list[dict] = []
        self.rxs: list = []
        self.by_key: dict = {}
        self.vis = None
        self.sessions: list[dict] = []
        self.skip: set[int] = set()
        self.mode = "запуск"
        self.cur = None             # текущий сеанс {a, b, test, outdir}
        self.stop_session = False
        self.quit = False
        self.need_reload = False
        self.need_replan = False
        self.job = None
        self.rx_ver = 1
        self.gallery_ver = 1
        self.snapshot = "{}"
        self._snap_t = 0.0
        self._track = (0, None)
        self.error = ""
        self.history: dict = {}
        self.wf: dict = {}             # ключ → (буфер звука, строки водопада)
        self.sess_stats: dict = {}
        self.live_comb = None          # склейка во время пролёта: dict(rgb, ver, text)
        self._comb_t = 0.0
        self._comb_ver = 0
        try:
            self.index = json.loads(self.p_index.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.index = {}
        iss.LOG_HOOKS.append(self._on_log)
        iss.STATUS_HOOKS.append(self._on_status)

    # ---------------- журнал ----------------
    def _on_log(self, line):
        with self.lock:
            self.log_seq += 1
            self.logs.append((self.log_seq, line))

    def _on_status(self, msg):
        self.status_line = msg

    def log(self, msg):
        iss.log(msg)

    # ---------------- настройки ----------------
    def _apply_args(self):
        s = self.settings
        a = self.args
        a.max_rx = int(s["max_rx"])
        a.min_sep_km = float(s["min_sep_km"])
        a.min_el = float(s["min_el"])
        a.lead = int(s["lead"])
        a.tail = int(s["tail"])
        a.yield_min = float(s["yield_s"]) / 60
        a.no_switch = bool(s["no_switch"])
        a.retune_khz = float(s["retune_khz"])
        a.headless = bool(s["headless"])
        a.use_history = bool(s.get("use_history", True))
        iss.F0_HZ = int(round(float(s["freq_mhz"]) * 1e6))

    def save_settings(self):
        try:
            self.p_settings.write_text(json.dumps(self.settings, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self.log(f"Не удалось сохранить настройки: {e}")

    def until(self):
        """До какого момента принимать. Пусто — неделя вперёд от текущего момента."""
        if not self.settings.get("until"):
            return _now() + timedelta(days=7)
        try:
            u = datetime.fromisoformat(self.settings["until"])
            if u.tzinfo is None:
                u = u.astimezone()
            return u
        except Exception:  # noqa: BLE001
            return _now() + timedelta(days=7)

    # ---------------- приёмники ----------------
    def load_receivers(self):
        cfgs = scan.load(self.p_rx)
        builtin = {b["url"] for b in scan.BUILTIN}
        try:
            user = json.loads(self.p_user.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            user = []
        by_url = {c["url"]: c for c in cfgs}
        for u in user:
            if u["url"] in by_url:
                by_url[u["url"]].update({k: v for k, v in u.items() if k != "user"})
                by_url[u["url"]]["edited"] = True
            else:
                c = dict(u, user=True)
                cfgs.append(c)
                by_url[c["url"]] = c
        try:
            chk = json.loads(self.p_check.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            chk = {}
        dis = set(self.settings.get("disabled") or [])
        for c in cfgs:
            c["builtin"] = c["url"] in builtin
            r = chk.get(c["url"])
            c.pop("bad", None)
            c["check"] = r
            if r and not r.get("ok"):
                c["bad"] = r.get("why") or "не прошёл проверку"
            c["disabled"] = c["url"] in dis
        self.cfgs = cfgs

    def build_rxs(self):
        old = {r.cfg["url"]: r for r in self.rxs}
        rxs = []
        for c in self.cfgs:
            r = old.get(c["url"])
            if r is not None and not r.active:
                r = None
            if r is None:
                r = iss.Rx(c, self.orbit, self.args)
            r.cfg, r.orbit = c, self.orbit
            r.topo = iss.Orbit.topo(c)
            r.on_audio = self._on_audio
            r.on_image = self._on_image
            h = self.history.get(r.key)
            r.rating = h["rating"] if h else 0.5
            rxs.append(r)
        self.rxs = rxs
        self.by_key = {r.key: r for r in rxs}
        self.rx_ver += 1

    def save_user(self, user):
        self.p_user.write_text(json.dumps(user, ensure_ascii=False, indent=1), encoding="utf-8")

    def user_list(self):
        try:
            return json.loads(self.p_user.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

    # ---------------- орбита и расписание ----------------
    def load_orbit(self, force=False):
        s = self.settings
        if force and not s.get("tle_text"):
            import tle as tlemod
            tlemod.get_tle(int(s["norad"]), log=self.log, force=True)
        try:
            self.orbit = iss.Orbit(None, norad=int(s["norad"]), tle_text=s.get("tle_text") or None)
            self.error = ""
        except Exception as e:  # noqa: BLE001
            self.error = f"Орбита не загружена: {e}"
            self.log(self.error)
            if self.orbit is None:
                self.orbit = iss.Orbit(None, norad=25544)

    def plan(self):
        t0 = time.time()
        start = _now()
        end = self.until()
        if end <= start + timedelta(minutes=5):
            end = start + timedelta(hours=6)
        self.vis = iss.Visibility(self.orbit, self.rxs, start, end)
        ok = [i for i, r in enumerate(self.rxs)
              if not r.cfg.get("disabled") and not (r.cfg.get("bad") and not r.cfg.get("builtin"))]
        self.svis = subvis(self.vis, ok)
        self.sessions = [x for x in iss.build_sessions(self.svis, self.args) if x["b"] > start] if ok else []
        for x in self.sessions:
            x["id"] = int(x["a"].timestamp())
        self.planned_at = time.time()
        self.log(f"Расписание: сеансов {len(self.sessions)} до {iss.local(self.until(), '%d.%m %H:%M')} "
                 f"({time.time() - t0:.1f} с)")

    def reload_all(self, force_tle=False):
        self.mode = "расчёт"
        self._apply_args()
        self.load_orbit(force_tle)
        self.load_receivers()
        self.compute_history()
        self.build_rxs()
        self.plan()
        self.need_reload = self.need_replan = False
        self.mode = "ожидание"

    def next_session(self):
        now = _now()
        for x in self.sessions:
            if x["b"] > now and x["id"] not in self.skip and not x.get("done"):
                return x
        return None

    # ---------------- звук и картинки ----------------
    def _on_audio(self, rx, x):
        self._wf_feed(rx.key, x)
        b = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        with self.lock:
            self.aseq += 1
            d = self.audio.setdefault(rx.key, deque(maxlen=60))
            d.append((self.aseq, b))

    def _wf_feed(self, key, x):
        buf, rows = self.wf.get(key) or (np.zeros(0, np.float32), np.zeros((WF_ROWS, WF_HI - WF_LO), np.uint8))
        buf = np.concatenate([buf, np.asarray(x, np.float32)])
        win = np.hanning(WF_N).astype(np.float32)
        new = []
        while len(buf) >= WF_N:
            sp = np.abs(np.fft.rfft(buf[:WF_N] * win))[WF_LO:WF_HI]
            db = 20 * np.log10(sp + 1e-6)
            new.append(np.clip((db + 10) * 4.5, 0, 255).astype(np.uint8))   # ≈ −10…+46 дБ
            buf = buf[WF_HOP:]
        if new:
            rows = np.vstack([rows[len(new):], np.array(new)]) if len(new) < WF_ROWS else np.array(new[-WF_ROWS:])
        self.wf[key] = (buf, rows)

    def waterfall(self, key):
        w = self.wf.get(key)
        return None if w is None else w[1]

    def audio_since(self, since: int, keys):
        out = {}
        with self.lock:
            cur = self.aseq
            if since < 0:
                return cur, out
            for k in keys:
                d = self.audio.get(k)
                if not d:
                    continue
                parts = [b for s, b in list(d) if s > since]
                if parts:
                    out[k] = base64.b64encode(b"".join(parts)).decode()
        return cur, out

    def _rel(self, p: Path):
        try:
            return Path(p).resolve().relative_to(self.out.resolve()).as_posix()
        except Exception:  # noqa: BLE001
            return None

    def _on_image(self, rx, p):
        rel = self._rel(p)
        if not rel:
            return
        m = getattr(rx.dec, "last_meta", None) or {}
        self.index[rel] = dict(t=m.get("w0") or time.time(), rx=rx.key, rx_name=rx.cfg.get("name", ""),
                               short=rx.short, sat=self.orbit.name, norad=self.orbit.norad,
                               q=m.get("q"), lines=m.get("lines"), start=m.get("start"),
                               el=round(rx.el, 1), kind="live")
        self._save_index()

    def _save_index(self):
        self.gallery_ver += 1
        try:
            self.p_index.write_text(json.dumps(self.index, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---------------- интерфейс сеанса (вызывается из run_session) ----------------
    def session_started(self, sess, outdir, test):
        self.stop_session = False
        self.sess_stats = {}
        self.live_comb = None
        self.wf = {}
        self.cur = dict(a=sess["a"], b=sess["b"], test=test, outdir=outdir, id=sess.get("id"))
        self.mode = "тест" if test else "сеанс"

    def session_finished(self, sess, outdir, records, comb, test):
        try:
            self.write_report(sess, outdir, records, comb, test)
        except Exception as e:  # noqa: BLE001
            self.log(f"Отчёт не записан: {e}")
            traceback.print_exc()
        if not test:
            self.compute_history()
            for r in self.rxs:
                h = self.history.get(r.key)
                r.rating = h["rating"] if h else 0.5
        for p in comb:
            rel = self._rel(p)
            if rel:
                self.index[rel] = dict(t=sess["a"].timestamp(), rx="combined", rx_name="склейка",
                                       short="склейка", sat=self.orbit.name, norad=self.orbit.norad,
                                       kind="combined")
        self._save_index()

    def tick(self, rxs=None):
        self._process_cmds()
        if self.mode == "проверка" and not self.stop_session:
            nx = self.next_session()
            if nx and self.settings["auto"] and (nx["a"] - _now()).total_seconds() < 20:
                self.log("Скоро сеанс — прерываю проверку.")
                self.stop_session = True
        if self.mode in ("сеанс",) and self.cur and self.cur.get("id") in self.skip:
            self.stop_session = True
        if self.mode in ("сеанс", "тест"):
            self._track_session()
            if time.time() - self._comb_t > 3:
                self._comb_t = time.time()
                try:
                    self._live_combine()
                except Exception as e:  # noqa: BLE001
                    print("живая склейка:", e)
        if time.time() - self._snap_t > 0.5:
            self._snap()

    # ---------------- статистика сеанса, живая склейка, отчёт ----------------
    def _track_session(self):
        now = time.time()
        for r in self.rxs:
            if not r.active:
                continue
            st = self.sess_stats.setdefault(r.key, dict(short=r.short, name=r.cfg.get("name", ""), max_el=-90.0,
                                                        max_str=0.0, secs=0.0, t=now, opened=0))
            dt = min(2.0, now - st["t"])
            st["t"] = now
            if r.state == "running" and r.dec is not None:
                st["secs"] += dt
                st["max_el"] = max(st["max_el"], float(r.el))
                st["max_str"] = max(st["max_str"], float(r.strength))
            st["opened"] = max(st["opened"], r.starts)

    def _live_combine(self):
        import combine as cmb
        recs = []
        for r in self.rxs:
            for rec in list(r.session_records):
                recs.append(dict(rec, rx=r.short[:24]))
            if r.active and r.dec is not None:
                for rec in list(r.dec.records):
                    recs.append(dict(rec, rx=r.short[:24]))
                pr = r.dec.partial_rec
                if pr is not None and r.dec.partial is not None:
                    recs.append(dict(pr, rx=r.short[:24], partial=True))
        recs = [x for x in recs if x.get("w0") is not None]
        if not recs:
            return
        last = cmb.group(recs)[-1]
        if time.time() - cmb._span(last[-1])[1] > 120:      # передача давно закончилась
            return
        res = cmb.combine(last) if len(last) > 1 else None
        if res and res[0] is not None:
            rgb, src, used, notes = res
            filled = int((src >= 0).sum())
            names = sorted({u["rx"] for u in used})
            text = f"склейка {len(names)} приёмников: {', '.join(names)} · строк {filled}/240"
        else:
            best = max(last, key=lambda x: float((x["q"] > 0.15).sum()))
            rgb = best["rgb"]
            filled = int((best["q"] > 0.15).sum())
            text = f"{best['rx']} · строк {filled}/240" + (" (идёт приём)" if best.get("partial") else "")
            if len(last) > 1:
                text += " · другие куски не совместились"
        self._comb_ver += 1
        self.live_comb = dict(rgb=np.asarray(rgb), ver=self._comb_ver, text=text, filled=filled)

    def live_comb_png(self):
        lc = self.live_comb
        if not lc:
            return None
        from PIL import Image
        b = io.BytesIO()
        Image.fromarray(lc["rgb"]).save(b, "PNG")
        return b.getvalue()

    def write_report(self, sess, outdir, records, comb, test):
        outdir = Path(outdir)
        by_rx = {}
        for rec in records:
            d = by_rx.setdefault(rec["rx"], dict(images=0, best_q=0.0, max_lines=0))
            d["images"] += 1
            d["best_q"] = max(d["best_q"], float(np.mean(rec["q"][rec["q"] > 0])) if (rec["q"] > 0).any() else 0)
            d["max_lines"] = max(d["max_lines"], int(rec.get("lines") or 0))
        rows = []
        for key, st in self.sess_stats.items():
            d = by_rx.get(st["short"], dict(images=0, best_q=0.0, max_lines=0))
            rows.append(dict(key=key, short=st["short"], name=st["name"], secs=round(st["secs"]),
                             max_el=round(st["max_el"], 1), max_strength=round(st["max_str"]), **d))
        rows.sort(key=lambda x: (-x["images"], -x["max_lines"], -x["max_strength"]))
        rep = dict(session=outdir.name, a=sess["a"].timestamp(), b=sess["b"].timestamp(), test=test,
                   sat=self.orbit.name, norad=self.orbit.norad, rx=rows, images=len(records),
                   combined=[Path(p).name for p in comb])
        (outdir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        good = [x for x in rows if x["images"]]
        lines = [f"Отчёт сеанса {outdir.name} ({self.orbit.name}): приёмников {len(rows)}, с картинками {len(good)}, "
                 f"картинок {len(records)}, склеек {len(comb)}"]
        for x in rows[:12]:
            lines.append(f"  {x['short']:<16} угол до {x['max_el']:>4.0f}°  слушал {x['secs'] // 60:.0f}:{x['secs'] % 60:02.0f}  "
                         f"SSTV до {x['max_strength']:>3}%  картинок {x['images']}"
                         + (f", лучшая {x['max_lines']} строк" if x["images"] else ""))
        (outdir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for l in lines:
            self.log(l)

    def reports(self):
        out = {}
        for p in self.out.glob("2*/report.json"):
            try:
                out[p.parent.name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return out

    # ---------------- история приёмников ----------------
    def compute_history(self):
        """По всем прошлым сеансам: где приёмник слушали и где он дал картинку."""
        hist = {}
        min_bytes = 12000 * 2 * 20            # запись хотя бы 20 с
        for sdir in self.out.iterdir() if self.out.exists() else []:
            if not sdir.is_dir() or not sdir.name[:8].isdigit():
                continue
            red = sdir / "redecoded"
            red_names = [p.stem for p in red.glob("*.png")] if red.exists() else []
            for rdir in sdir.iterdir():
                if not rdir.is_dir() or rdir.name == "redecoded":
                    continue
                wavs = [w for w in rdir.glob("audio*.wav") if w.stat().st_size > min_bytes]
                if not wavs:
                    continue
                k = rdir.name
                live = list(rdir.glob("*.png"))
                redn = [n for n in red_names if n.startswith(k + "_") and n[len(k) + 1:].replace("_", "").isdigit()]
                h = hist.setdefault(k, dict(listened=0, success=0, images=0, best_q=0.0))
                h["listened"] += 1
                n = max(len(live), len(redn))
                h["images"] += n
                if n:
                    h["success"] += 1
                for p in live:
                    q = (self.index.get(self._rel(p) or "") or {}).get("q")
                    if q:
                        h["best_q"] = max(h["best_q"], float(q))
        for h in hist.values():
            h["rating"] = (h["success"] + 0.5) / (h["listened"] + 1)
        self.history = hist

    def home_topo(self):
        s = self.settings
        if s.get("home_lat") is None or s.get("home_lon") is None:
            return None
        from skyfield.api import wgs84
        return wgs84.latlon(float(s["home_lat"]), float(s["home_lon"]), elevation_m=float(s.get("home_alt") or 0))

    def home_passes(self, days=3, min_el=5.0):
        """Пролёты над моим местом: время, максимум, откуда и куда, попадает ли в срок SSTV."""
        topo, o = self.home_topo(), self.orbit
        if topo is None or o is None:
            return None
        now = _now()
        end = now + timedelta(days=days)
        ps = o.passes(topo, now - timedelta(minutes=15), end, min_el, horizon=0.0)
        until = self.until()
        out = []
        for p in ps:
            if not p.get("los") or p["los"] < now:
                continue
            _, az_a, _ = o.look(topo, p["aos"])
            _, az_b, _ = o.look(topo, p["los"])
            out.append(dict(aos=p["aos"].timestamp(), los=p["los"].timestamp(), tca=p["tca"].timestamp(),
                            max_el=round(float(p["max_el"]), 1), az_a=round(float(az_a)), az_b=round(float(az_b)),
                            dir=f"{compass(az_a)} → {compass(az_b)}", sstv=p["aos"] < until))
        return out

    def pass_track(self, key, min_max_el=0.0):
        """Небесная траектория текущего или ближайшего пролёта над приёмником (для полярной диаграммы)."""
        o = self.orbit
        if key == "__home__":
            topo = self.home_topo()
        else:
            r = self.by_key.get(key)
            topo = r.topo if r else None
        if topo is None or o is None:
            return None
        now = _now()
        ps = o.passes(topo, now - timedelta(minutes=12), now + timedelta(days=2), min_max_el, horizon=0.0)
        ps = [p for p in ps if p.get("los") and p["los"] > now]
        if not ps:
            return None
        p = ps[0]
        a, b = p["aos"], p["los"]
        n = max(2, int((b - a).total_seconds() // 10) + 1)
        ts = [a + (b - a) * i / (n - 1) for i in range(n)]
        alt, az, _ = (o.sat - topo).at(o.ts.from_datetimes(ts)).altaz()
        return dict(aos=a.timestamp(), los=b.timestamp(), tca=p["tca"].timestamp(), max_el=float(p["max_el"]),
                    t=[t.timestamp() for t in ts], el=[float(x) for x in alt.degrees], az=[float(x) for x in az.degrees],
                    min_el=self.args.min_el)

    # ---------------- команды ----------------
    def cmd(self, c: dict):
        self.cmds.put(c)

    def _process_cmds(self):
        while True:
            try:
                c = self.cmds.get_nowait()
            except queue.Empty:
                return
            try:
                self._do(c)
            except Exception as e:  # noqa: BLE001
                self.log(f"Ошибка команды {c.get('cmd')}: {e}")
                traceback.print_exc()

    def busy(self):
        return self.mode in ("сеанс", "тест", "проверка", "поиск", "пересборка")

    def _do(self, c):
        k = c.get("cmd")
        if k == "settings":
            new = {kk: v for kk, v in c.get("data", {}).items() if kk in DEFAULTS}
            for kk in ("max_rx", "lead", "tail", "norad"):
                if kk in new:
                    new[kk] = int(float(new[kk]))
            for kk in ("min_el", "min_sep_km", "yield_s", "freq_mhz", "retune_khz", "home_alt"):
                if kk in new:
                    new[kk] = float(new[kk])
            for kk in ("home_lat", "home_lon"):
                if kk in new:
                    v = new[kk]
                    new[kk] = None if v in (None, "") else float(str(v).replace(",", "."))
            if new.get("home_lat") is not None and not -90 <= new["home_lat"] <= 90:
                raise ValueError("широта вне диапазона")
            if new.get("home_lon") is not None and not -180 <= new["home_lon"] <= 180:
                raise ValueError("долгота вне диапазона")
            if new.get("tle_text"):
                import tle as tlemod
                tlemod.parse_tle(new["tle_text"])        # проверка до сохранения
            heavy = any(new.get(kk, self.settings[kk]) != self.settings[kk]
                        for kk in ("norad", "tle_text", "freq_mhz", "until", "min_el", "lead", "tail"))
            self.settings.update(new)
            self.save_settings()
            self._apply_args()
            self.log("Настройки сохранены" + (" — пересчитываю расписание" if heavy else ""))
            if heavy:
                self.need_reload = True
        elif k == "refresh_tle":
            self.need_reload = "tle"
        elif k == "stop":
            self.stop_session = True
        elif k == "skip":
            sid = int(c["id"])
            (self.skip.discard if sid in self.skip else self.skip.add)(sid)
        elif k == "auto":
            self.settings["auto"] = bool(c.get("on"))
            self.save_settings()
        elif k == "rx_enable":
            dis = set(self.settings.get("disabled") or [])
            for u in c.get("urls") or [c.get("url")]:
                (dis.discard if c.get("on") else dis.add)(u)
                for cf in self.cfgs:
                    if cf["url"] == u:
                        cf["disabled"] = not c.get("on")
            self.settings["disabled"] = sorted(dis)
            self.save_settings()
            self.rx_ver += 1
            self.need_replan = True
        elif k == "rx_save":
            d = c["data"]
            url = d["url"].strip()
            if not re.match(r"https?://", url):
                url = "http://" + url
            if not url.endswith("/") and d.get("kind", "owrx") == "owrx":
                url += "/"
            ent = dict(url=url, kind=d.get("kind") or "owrx", lat=float(d["lat"]), lon=float(d["lon"]),
                       name=(d.get("name") or url)[:80], short=(d.get("short") or d.get("name") or url)[:14])
            if not (-90 <= ent["lat"] <= 90 and -180 <= ent["lon"] <= 180):
                raise ValueError("координаты вне диапазона")
            user = [u for u in self.user_list() if u["url"] != url and u["url"] != d.get("orig_url")]
            user.append(ent)
            self.save_user(user)
            self.log(f"Приёмник сохранён: {ent['name']} ({ent['lat']:.4f}, {ent['lon']:.4f})")
            self.need_reload = True
        elif k == "rx_delete":
            user = [u for u in self.user_list() if u["url"] != c["url"]]
            self.save_user(user)
            self.log("Ручные данные приёмника удалены.")
            self.need_reload = True
        elif k == "rx_unbad":
            try:
                chk = json.loads(self.p_check.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                chk = {}
            for u in c.get("urls") or []:
                chk.pop(u, None)
            self.p_check.write_text(json.dumps(chk, ensure_ascii=False, indent=1), encoding="utf-8")
            self.need_reload = True
        elif k in ("check", "scan", "redecode", "test"):
            if self.busy():
                self.log("Сейчас идёт другая работа — дождись её окончания или нажми «Стоп».")
                return
            nx = self.next_session()
            if k in ("scan", "redecode") and nx and self.settings["auto"] and (nx["a"] - _now()).total_seconds() < 15 * 60:
                self.log(f"До сеанса меньше 15 минут ({iss.local(nx['a'], '%H:%M')}) — "
                         f"{'поиск' if k == 'scan' else 'пересборку'} лучше запустить после него.")
                return
            self.job = c

    # ---------------- задания ----------------
    def _run_job(self, c):
        k = c["cmd"]
        self.stop_session = False
        try:
            if k == "check":
                urls = set(c.get("urls") or [])
                rxs = [r for r in self.rxs if not urls or r.cfg["url"] in urls]
                self.mode = "проверка"
                self.cur = None
                self.args.max_rx = int(self.settings["max_rx"])
                iss.run_check(rxs, self.args, self.out, self.p_check, ctl=self)
                self.args.test = False
                self.need_reload = True
            elif k == "scan":
                self.mode = "поиск"
                scan.scan(self.p_rx, log=self.log)
                self.need_reload = True
            elif k == "redecode":
                self.mode = "пересборка"
                import redecode
                redecode.redecode_tree(self.out, log=self.log, skip=("test", "check", "best"))
                self.gallery_ver += 1
            elif k == "test":
                urls = set(c.get("urls") or [])
                secs = int(c.get("seconds") or 60)
                if urls:
                    idx = [i for i, r in enumerate(self.rxs) if r.cfg["url"] in urls]
                else:
                    idx = [i for i, r in enumerate(self.rxs)
                           if not r.cfg.get("disabled") and not r.cfg.get("bad")]
                if not urls:
                    # самые высокие сейчас — так тест похож на настоящий сеанс
                    j = self.vis.idx(_now())
                    idx.sort(key=lambda i: -self.vis.el[j, i])
                if not idx:
                    self.log("Тест: нет подходящих приёмников.")
                    return
                now = _now()
                self.args.test = True
                v = iss.Visibility(self.orbit, [self.rxs[i] for i in idx], now, now + timedelta(seconds=secs + 30))
                sess = dict(a=now, b=now + timedelta(seconds=secs), id=None)
                old = self.args.max_rx
                if urls:
                    self.args.max_rx = len(idx)
                self.log(f"Тест: {min(len(idx), self.args.max_rx)} приёмников на {secs} с, без доплера.")
                try:
                    iss.run_session(sess, v, self.args, self.out, test=True, ctl=self)
                finally:
                    self.args.test = False
                    self.args.max_rx = old
        except Exception as e:  # noqa: BLE001
            self.log(f"Ошибка: {e}")
            traceback.print_exc()
        finally:
            self.args.test = False
            self.cur = None
            self.mode = "ожидание"
            self.rx_ver += 1

    # ---------------- главный цикл ----------------
    def run(self):
        try:
            self.reload_all()
        except Exception as e:  # noqa: BLE001
            self.error = f"Ошибка запуска: {e}"
            self.log(self.error)
            traceback.print_exc()
        last_tle = time.time()
        while not self.quit:
            try:
                self.tick()
                if self.need_reload:
                    self.reload_all(force_tle=self.need_reload == "tle")
                    last_tle = time.time()
                elif self.need_replan:
                    self.mode = "расчёт"
                    self.plan()
                    self.need_replan = False
                    self.mode = "ожидание"
                if time.time() - last_tle > 6 * 3600 and not self.settings.get("tle_text"):
                    last_tle = time.time()
                    self.reload_all()
                if self.job:
                    c, self.job = self.job, None
                    self._run_job(c)
                    continue
                nx = self.next_session()
                if nx and self.settings["auto"] and _now() >= nx["a"]:
                    self.log(f"Сеанс {iss.local(nx['a'], '%H:%M')}–{iss.local(nx['b'], '%H:%M')}: "
                             f"{iss.describe(nx)}")
                    try:
                        iss.run_session(nx, self.svis, self.args, self.out, ctl=self)
                    except Exception as e:  # noqa: BLE001
                        self.log(f"Сеанс прерван ошибкой: {e}")
                        traceback.print_exc()
                    nx["done"] = True
                    self.cur = None
                    self.mode = "ожидание"
                    self.rx_ver += 1
                    continue
                time.sleep(0.3)
            except Exception as e:  # noqa: BLE001
                self.log(f"Ошибка ядра: {e}")
                traceback.print_exc()
                time.sleep(2)
        if self.cur:
            self.stop_session = True

    def shutdown(self, timeout=60):
        self.stop_session = True
        self.quit = True
        self.join(timeout)

    # ---------------- снимок состояния ----------------
    def _snap(self):
        self._snap_t = time.time()
        now = _now()
        o = self.orbit
        sat = None
        if o is not None:
            try:
                la, lo, h = o.subpoint([now])
                sat = dict(name=o.name, norad=o.norad, src=o.src, age=round(o.age, 2), manual=o.manual,
                           lat=float(la[0]), lon=float(lo[0]), alt=float(h[0]))
            except Exception:  # noqa: BLE001
                pass
        el = []
        if self.vis is not None and len(self.vis.rxs) == len(self.rxs):
            j = self.vis.idx(now)
            el = [round(float(v), 1) for v in self.vis.el[j]]
        active = []
        for r in self.rxs:
            if not r.active:
                continue
            dec = r.dec
            prog = dec.progress if dec else None
            if r.state != "running":
                st = "открываю"
            elif r.yielded:
                st = "уступил"
            elif dec is None:
                st = "нет звука"
            elif prog is not None:
                st = f"картинка {prog}/240"
            else:
                st = "слушаю"
            active.append(dict(
                key=r.key, url=r.cfg["url"], short=r.short, name=r.cfg.get("name", ""), kind=r.cfg["kind"],
                el=round(r.el, 1), az=round(r.az, 0), dop=round(r.dop), level=None if r.level is None else round(r.level, 1),
                strength=round(r.strength), state=st, prog=prog,
                imgs=len(r.images) + len(r.session_images),
                img_ver=(dec.partial_ver if dec else 0) + 1000 * len(r.images),
                has_img=bool(dec is not None and (dec.partial is not None or dec.last_rgb is not None)) or bool(r.images),
                lat=r.cfg["lat"], lon=r.cfg["lon"]))
        nx = self.next_session()
        cur = self.cur
        snap = dict(
            now=now.timestamp(), mode=self.mode, status=self.status_line[:300], error=self.error,
            sat=sat, freq=self.settings["freq_mhz"], auto=self.settings["auto"],
            session=None if not cur else dict(a=_ts(cur["a"]), b=_ts(cur["b"]), test=cur["test"],
                                              name=Path(cur["outdir"]).name),
            next=None if not nx else dict(id=nx["id"], a=_ts(nx["a"]), b=_ts(nx["b"]), n_rx=nx["n_rx"],
                                          best=nx["best"]),
            active=active, el=el, rx_ver=self.rx_ver, log_seq=self.log_seq, gallery_ver=self.gallery_ver,
            images=sum(a["imgs"] for a in active), min_el=self.args.min_el, max_rx=self.args.max_rx,
            n_sessions=len(self.sessions), pending_reload=bool(self.need_reload),
            home=(dict(lat=self.settings["home_lat"], lon=self.settings["home_lon"],
                       name=self.settings.get("home_name") or "Я", show=bool(self.settings.get("home_show", True)))
                  if self.settings.get("home_lat") is not None and self.settings.get("home_lon") is not None else None),
            comb=None if not self.live_comb else dict(ver=self.live_comb["ver"], text=self.live_comb["text"],
                                                      filled=self.live_comb["filled"]),
        )
        self.snapshot = json.dumps(snap, ensure_ascii=False)

    # ---------------- данные по запросу интерфейса ----------------
    def receivers_json(self):
        counts = {}
        for g in self.gallery():
            counts[g["rx"]] = counts.get(g["rx"], 0) + 1
        out = []
        for r in self.rxs:
            c = r.cfg
            out.append(dict(key=r.key, url=c["url"], kind=c["kind"], lat=c["lat"], lon=c["lon"],
                            name=c.get("name", ""), short=r.short, bad=c.get("bad"), builtin=c.get("builtin"),
                            user=bool(c.get("user")), edited=bool(c.get("edited")), disabled=bool(c.get("disabled")),
                            checked=(c.get("check") or {}).get("when"), profile=c.get("profile"),
                            max_clients=c.get("max_clients"), images=counts.get(r.key, 0),
                            hist=self.history.get(r.key)))
        return dict(ver=self.rx_ver, list=out)

    def schedule_json(self):
        now = _now()
        out = []
        for x in self.sessions:
            out.append(dict(id=x["id"], a=_ts(x["a"]), b=_ts(x["b"]), n_rx=x["n_rx"], best=x["best"],
                            skip=x["id"] in self.skip, done=bool(x.get("done")) or x["b"] < now,
                            running=bool(self.cur and self.cur.get("id") == x["id"])))
        return dict(list=out, until=self.until().timestamp())

    def track_json(self):
        t, cached = self._track
        if cached is not None and time.time() - t < 20 and cached.get("norad") == getattr(self.orbit, "norad", None):
            return cached
        o = self.orbit
        if o is None:
            return {}
        now = _now()
        ts = [now + timedelta(seconds=s) for s in range(-45 * 60, 95 * 60 + 1, 30)]
        la, lo, h = o.subpoint(ts)
        k = 45 * 2
        res = dict(norad=o.norad, past=[[round(float(a), 2), round(float(b), 2)] for a, b in zip(la[:k + 1], lo[:k + 1])],
                   future=[[round(float(a), 2), round(float(b), 2)] for a, b in zip(la[k:], lo[k:])],
                   alt=float(h[k]))
        self._track = (time.time(), res)
        return res

    def passes_json(self, key):
        """Ближайшие пролёты над одним приёмником."""
        r = self.by_key.get(key)
        if not r or self.orbit is None:
            return []
        now = _now()
        ps = self.orbit.passes(r.topo, now, min(self.until(), now + timedelta(days=3)), self.args.min_el)
        return [dict(aos=_ts(p["aos"]), los=_ts(p["los"]), tca=_ts(p["tca"]), max_el=round(float(p["max_el"]), 1))
                for p in ps[:12]]

    def live_png(self, key):
        r = self.by_key.get(key)
        rgb = None
        if r is not None and r.dec is not None:
            rgb = r.dec.partial if r.dec.partial is not None else r.dec.last_rgb
        if rgb is None and r is not None and r.images:
            return Path(r.images[-1]).read_bytes()
        if rgb is None:
            return None
        from PIL import Image
        b = io.BytesIO()
        Image.fromarray(np.asarray(rgb)).save(b, "PNG")
        return b.getvalue()

    @staticmethod
    def _rx_of(stem, known):
        best = ""
        for k in known:
            if stem.startswith(k) and len(k) > len(best):
                best = k
        return best or re.sub(r"(_\d)?_\d\d$", "", stem)

    def gallery(self):
        res = []
        rx_names = {r.key: (r.short, r.cfg.get("name", "")) for r in self.rxs}
        if not self.out.exists():
            return res
        for sdir in sorted(self.out.iterdir()):
            if not sdir.is_dir() or not sdir.name[:8].isdigit():
                continue
            try:
                st = datetime.strptime(sdir.name[:13], "%Y%m%d_%H%M").astimezone().timestamp()
            except ValueError:
                continue
            for p in sorted(sdir.rglob("*.png")):
                rel = self._rel(p)
                m = self.index.get(rel) or {}
                parts = p.relative_to(sdir).parts
                if len(parts) == 1:
                    kind, rx = ("combined" if p.name.startswith("combined") else "live"), \
                               ("combined" if p.name.startswith("combined") else "goonhilly")
                elif parts[0] == "redecoded":
                    kind = "redecoded"
                    rx = "combined" if p.name.startswith("combined") else self._rx_of(p.stem, rx_names)
                else:
                    kind, rx = "live", parts[0]
                if p.name.startswith("combined"):
                    kind_c = "combined" if kind != "redecoded" else "redecoded"
                    rx = "combined"
                    kind = kind_c
                short, name = rx_names.get(rx, (m.get("short") or rx, m.get("rx_name") or ""))
                if rx == "combined":
                    short, name = "склейка", "склейка нескольких приёмников"
                elif rx == "goonhilly":
                    short, name = "GB Goonhilly", "Goonhilly, Корнуолл"
                res.append(dict(path=rel, session=sdir.name, t=m.get("t") or st, t_known=bool(m.get("t")),
                                rx=rx, short=short, name=name, sat=m.get("sat") or "ISS (ZARYA)",
                                norad=m.get("norad") or 25544, q=m.get("q"), lines=m.get("lines"),
                                el=m.get("el"), kind=kind, combined=rx == "combined"))
        return res
