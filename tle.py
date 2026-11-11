"""Получение TLE с кэшем и запасными источниками.

Кэш: tle_cache.json рядом с программой, ключ — NORAD ID.
Свежий кэш (< MAX_AGE_H часов) используется без запросов в сеть, чтобы
CelesTrak не блокировал адрес за частые обращения (он отвечает 403).
Порядок: свежий кэш → CelesTrak → tle.ivanstanojevic.me → AMSAT (только
известные там спутники) → устаревший кэш → встроенный TLE (только МКС).
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request

CACHE = __import__("paths").DATA / "tle_cache.json"
MAX_AGE_H = 6
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

FALLBACK = {
    25544: ("ISS (ZARYA)",
            "1 25544U 98067A   26312.88495931  .00008565  00000-0  16189-3 0  9993",
            "2 25544  51.6321 304.0500 0007249   4.0569 356.0477 15.49264925594184"),
}


def _get(url: str, timeout=15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def _checksum_ok(line: str) -> bool:
    if len(line) < 69:
        return False
    s = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return line[68].isdigit() and s % 10 == int(line[68])


def parse_tle(text: str, norad: int | None = None):
    """Ищет пару строк 1/2 (опционально по NORAD). Возвращает (name, l1, l2)."""
    lines = [l.rstrip() for l in text.replace("\r", "").split("\n") if l.strip()]
    for i in range(len(lines) - 1):
        l1, l2 = lines[i].strip(), lines[i + 1].strip()
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue
        n = int(l1[2:7])
        if norad is not None and n != norad:
            continue
        if int(l2[2:7]) != n:
            continue
        name = lines[i - 1].strip() if i > 0 and not lines[i - 1].strip()[:2] in ("1 ", "2 ") else f"NORAD {n}"
        if name.startswith("0 "):
            name = name[2:]
        return name, l1, l2
    raise ValueError("TLE не найден" + (f" для {norad}" if norad else ""))


def epoch_age_days(l1: str) -> float:
    yy = int(l1[18:20])
    doy = float(l1[20:32])
    year = 2000 + yy if yy < 57 else 1900 + yy
    import datetime as dt
    ep = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=doy - 1)
    return (dt.datetime.now(dt.timezone.utc) - ep).total_seconds() / 86400


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(c: dict):
    try:
        CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _src_celestrak(norad):
    return parse_tle(_get(f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=TLE"), norad)


def _src_ivan(norad):
    d = json.loads(_get(f"https://tle.ivanstanojevic.me/api/tle/{norad}"))
    return d.get("name") or f"NORAD {norad}", d["line1"].strip(), d["line2"].strip()


def _src_amsat(norad):
    return parse_tle(_get("https://www.amsat.org/tle/current/nasabare.txt"), norad)


SOURCES = [("CelesTrak", _src_celestrak), ("tle.ivanstanojevic.me", _src_ivan), ("AMSAT", _src_amsat)]


def get_tle(norad: int = 25544, log=print, force=False, max_age_h=MAX_AGE_H):
    """Возвращает (name, l1, l2, источник)."""
    norad = int(norad)
    cache = _load_cache()
    ent = cache.get(str(norad))
    if ent and not force and time.time() - ent.get("fetched", 0) < max_age_h * 3600:
        return ent["name"], ent["l1"], ent["l2"], f"кэш ({ent.get('src', '?')})"
    errs = []
    for name, fn in SOURCES:
        try:
            nm, l1, l2 = fn(norad)
            if not (_checksum_ok(l1) and _checksum_ok(l2)):
                raise ValueError("контрольная сумма")
            # не откатываемся на более старую эпоху, чем в кэше
            if ent and epoch_age_days(l1) > epoch_age_days(ent["l1"]) + 0.01:
                nm, l1, l2 = ent["name"], ent["l1"], ent["l2"]
            cache[str(norad)] = dict(name=nm, l1=l1, l2=l2, src=name, fetched=time.time())
            _save_cache(cache)
            return nm, l1, l2, name
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    log("TLE не скачан (" + "; ".join(errs) + ")")
    if ent:
        return ent["name"], ent["l1"], ent["l2"], f"старый кэш ({ent.get('src', '?')})"
    if norad in FALLBACK:
        return (*FALLBACK[norad], "встроенный")
    raise RuntimeError(f"Нет TLE для NORAD {norad}")
