"""Поиск онлайн-приёмников, которые слышат 437.550 МГц.

Источники:
  * rx-tx.info — карта с фильтром 437.550 (через Chromium);
  * ReceiverBook (receiverbook.de) — официальный каталог OpenWebRX, все приёмники
    мира без фильтра по частоте;
  * websdr.org — список WebSDR с диапазонами.
Для каждого OpenWebRX читается status.json: живой ли, есть ли профиль,
накрывающий 437.550, координаты, город. WebSDR берутся, если один из их
диапазонов накрывает 437.550. Годные — в receivers.json (прежние, которые
сейчас не ответили, не выбрасываются).
"""
from __future__ import annotations

import json
import math
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

F0 = 437_550_000
MAP_URL = ("https://rx-tx.info/map-sdr-points?freq_of=437.550&freq_to=437.550"
           "&bands=1030&country=All")

EXTRACT_JS = r"""
() => [...document.querySelectorAll('.geolocation-location[data-lat]')].map(e => {
  const c = e.querySelector('.location-content');
  const t = c ? c.innerText : '';
  const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
  const a = c && c.querySelector('a[href]');
  return {lat: +e.dataset.lat, lon: +e.dataset.lng, title: lines[0] || '',
          url: a ? a.href : '', soft: lines[lines.length - 1] || '',
          place: lines.find(l => /^[A-Z]{2}:/.test(l)) || ''};
})
"""

# Приёмники, проверенные вручную (на случай, если карта недоступна)
BUILTIN = [
    dict(url="https://vhf-goonhilly.batc.org.uk/", kind="websdr", lat=50.0524, lon=-5.1823,
         name="Goonhilly, Корнуолл", short="GB Goonhilly", preset=True),
    dict(url="https://sdr.blackserver.ro/", kind="owrx", lat=46.758, lon=23.557,
         name="YO5BWS, Клуж-Напока", short="RO Cluj"),
    dict(url="http://95.165.144.131:8074/", kind="owrx", lat=55.938, lon=35.775,
         name="Волоколамск", short="RU Volokolamsk"),
    dict(url="http://95.165.144.131:8073/", kind="owrx", lat=55.678, lon=37.507,
         name="Москва, ЗАО", short="RU Moscow"),
    dict(url="http://217.8.127.112:48/", kind="owrx", lat=51.766, lon=55.319,
         name="R9SBY, Оренбург", short="RU Orenburg"),
]


RB_URL = "https://www.receiverbook.de/map"
WEBSDR_URL = "http://websdr.ewi.utwente.nl/~~websdrlistk?v=1&fmt=2&chseq=0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def _get(url, timeout=30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx()).read().decode("utf-8", "replace")


def _get_browser(url) -> str:
    """Запасной путь, если сайт не отдаёт страницу простому запросу."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False)
        pg = br.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=90_000)
        pg.wait_for_timeout(4000)
        html = pg.content()
        br.close()
    return html


def fetch_receiverbook(log=print) -> list[dict]:
    html = ""
    for how in (_get, _get_browser):
        try:
            html = how(RB_URL)
            if "var receivers" in html:
                break
        except Exception as e:  # noqa: BLE001
            log(f"ReceiverBook ({how.__name__}): {e}")
    m = re.search(r"var\s+receivers\s*=\s*(\[.*?\]);\s*$", html, re.M | re.S)
    if not m:
        log("ReceiverBook: список не найден на странице.")
        return []
    data = json.loads(m.group(1))
    rows = []
    for e in data:
        try:
            lon, lat = e["location"]["coordinates"][:2]
        except Exception:  # noqa: BLE001
            continue
        for r in e.get("receivers", []):
            t = (r.get("type") or "")
            rows.append(dict(lat=float(lat), lon=float(lon), title=r.get("label") or e.get("label") or "",
                             url=r.get("url") or "", soft=t, place="", src="receiverbook"))
    log(f"ReceiverBook: приёмников {len(rows)} (OpenWebRX: {sum(1 for r in rows if r['soft'].startswith('OpenWebRX'))})")
    return rows


def fetch_websdr(log=print) -> list[dict]:
    try:
        txt = _get(WEBSDR_URL)
        data = json.loads(re.sub(r"^\s*//.*", "", txt, flags=re.M))
    except Exception as e:  # noqa: BLE001
        log(f"websdr.org: список не получен ({e})")
        return []
    out = []
    for e in data:
        try:
            bands = [(float(b["l"]) * 1e6, float(b["h"]) * 1e6) for b in e.get("bands", [])]
        except Exception:  # noqa: BLE001
            continue
        if not any(lo + 5000 <= F0 <= hi - 5000 for lo, hi in bands):
            continue
        if "lat" not in e or "url" not in e:
            continue
        out.append(dict(lat=float(e["lat"]), lon=float(e["lon"]), title=e.get("desc") or "", url=e["url"],
                        soft="WebSDR", place="", qth=e.get("qth") or "", src="websdr.org"))
    log(f"websdr.org: WebSDR с диапазоном 437.550: {len(out)}")
    return out


_COUNTRIES = None


def country(lat, lon) -> str:
    """Код страны по координатам (контуры Natural Earth лежат в ui/countries.json)."""
    global _COUNTRIES
    if _COUNTRIES is None:
        try:
            _COUNTRIES = json.loads((__import__("paths").UI / "countries.json").read_text(encoding="utf-8"))
            for c in _COUNTRIES:
                c.append([(min(x for x, _ in r), min(y for _, y in r), max(x for x, _ in r), max(y for _, y in r))
                          for r in c[1]])
        except Exception:  # noqa: BLE001
            _COUNTRIES = []
    best, bd = "", 1.0
    for cc, rings, boxes in _COUNTRIES:
        for r, (x0, y0, x1, y1) in zip(rings, boxes):
            if x0 - 1 <= lon <= x1 + 1 and y0 - 1 <= lat <= y1 + 1:
                inside = False
                j = len(r) - 1
                for i in range(len(r)):
                    xi, yi = r[i]
                    xj, yj = r[j]
                    if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
                        inside = not inside
                    j = i
                if inside:
                    return cc
                d = min(abs(lat - y) + abs(lon - x) for x, y in r[::4])     # рядом с берегом
                if d < bd:
                    best, bd = cc, d
    return best


def _city(s: str) -> str:
    s = re.sub(r"\s*\(.*?\)", "", s or "").strip()
    return re.split(r"[,/|;]", s)[0].strip()


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False          # у любительских приёмников часто самоподписанные сертификаты
    c.verify_mode = ssl.CERT_NONE
    return c


def fetch_map(log=print) -> list[dict]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        # окно видимое: так проверка Cloudflare на сайте обычно проходит сама
        br = pw.chromium.launch(headless=False)
        pg = br.new_page()
        pg.goto(MAP_URL, wait_until="domcontentloaded", timeout=90_000)
        for _ in range(60):
            n = pg.evaluate("() => document.querySelectorAll('.geolocation-location[data-lat]').length")
            if n:
                break
            pg.wait_for_timeout(1000)
        rows = pg.evaluate(EXTRACT_JS)
        br.close()
    log(f"На карте rx-tx.info точек: {len(rows)}")
    if not rows:
        log("Карта не отдала точки (возможно, сайт попросил проверку «я не робот»). "
            "Запусти --scan ещё раз и, если в окне появится проверка, пройди её.")
    return rows


def short_name(place: str, title: str) -> str:
    m = re.match(r"([A-Z]{2}):\s*(.*)", place or "")
    if m:
        return f"{m.group(1)} {m.group(2)[:11]}".strip()
    return (title or "?")[:14]


def check_owrx(row: dict, timeout=8.0) -> dict | None:
    base = row["url"] if row["url"].endswith("/") else row["url"] + "/"
    try:
        req = urllib.request.Request(urljoin(base, "status.json"),
                                     headers={"User-Agent": "Mozilla/5.0 iss-sstv-scan"})
        st = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_ctx()).read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    prof, best = None, 2.0
    for sdr in st.get("sdrs", []):
        for p in sdr.get("profiles", []):
            cf, sr = p.get("center_freq"), p.get("sample_rate")
            if cf and sr and abs(F0 - cf) < sr / 2 - 30_000 and abs(F0 - cf) / (sr / 2) < best:
                prof, best = f"{p.get('name', '')}", abs(F0 - cf) / (sr / 2)
    if not prof:
        return None
    rc = st.get("receiver", {}) or {}
    gps = rc.get("gps") or {}
    lat, lon = gps.get("lat"), gps.get("lon")
    # если в status.json координаты пустые или «по умолчанию», берём с карты
    if not isinstance(lat, (int, float)) or (abs(lat) < 0.01 and abs(lon or 0) < 0.01):
        lat, lon = row["lat"], row["lon"]
    if abs(lat) < 0.01 and abs(lon) < 0.01:
        return None
    short = short_name(row["place"], row["title"]) if row.get("place") else \
        f"{country(float(lat), float(lon)) or '??'} {(_city(rc.get('location') or '') or _city(row['title']))[:11]}".strip()
    return dict(url=base, kind="owrx", lat=float(lat), lon=float(lon),
                name=clean_name(rc.get("name") or row["title"], urlsplit(base).netloc), short=short, src=row.get("src", "rx-tx.info"),
                profile=prof, max_clients=st.get("max_clients"), version=st.get("version"))


def scan(out: Path, log=print, workers: int = 40) -> list[dict]:
    rows = []
    try:
        rows += [dict(r, src="rx-tx.info") for r in fetch_map(log)]
    except Exception as e:  # noqa: BLE001
        log(f"Карту rx-tx.info загрузить не удалось ({e}).")
    rows += fetch_receiverbook(log)
    rows += fetch_websdr(log)
    seen, owrx, websdr = set(), [], []
    for r in rows:
        u = r.get("url") or ""
        host = urlsplit(u).netloc.lower()
        if not host or host in seen:
            continue
        seen.add(host)
        if r["soft"].startswith("OpenWebRX"):
            owrx.append(r)
        elif r["soft"] == "WebSDR" and r.get("src") == "websdr.org":
            websdr.append(r)
    log(f"Проверяю {len(owrx)} OpenWebRX (status.json, займёт несколько минут) …")
    good = []
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(check_owrx, owrx):
            done += 1
            if res:
                good.append(res)
            if done % 100 == 0:
                log(f"  проверено {done}/{len(owrx)}, подходят {len(good)}")
    log(f"OpenWebRX живых и с профилем на 437.550: {len(good)}")
    for w in websdr:
        cc = country(w["lat"], w["lon"])
        good.append(dict(url=w["url"] if w["url"].endswith("/") else w["url"] + "/", kind="websdr",
                         lat=w["lat"], lon=w["lon"], name=w["title"][:80],
                         short=f"{cc or '??'} {_city(w.get('qth') or w['title'])[:11]}".strip(), src="websdr.org"))
    # прежние приёмники, которые сейчас не ответили, не теряем
    try:
        old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    except Exception:  # noqa: BLE001
        old = []
    hosts = {urlsplit(g["url"]).netloc.lower() for g in good}
    kept = [o for o in old if urlsplit(o["url"]).netloc.lower() not in hosts]
    if kept:
        log(f"Из прежнего списка оставлено {len(kept)} (сейчас не ответили или не найдены)")
    good += kept
    # встроенные проверенные приёмники всегда в списке
    hosts = {urlsplit(g["url"]).netloc for g in good}
    for b in BUILTIN:
        if urlsplit(b["url"]).netloc not in hosts:
            good.append(dict(b))
    # одинаковые приёмники под разными адресами (одни координаты и имя) — оставляем один
    uniq, keyset = [], set()
    for g in good:
        k = (round(g["lat"], 3), round(g["lon"], 3), g["name"][:30])
        if k in keyset:
            continue
        keyset.add(k)
        uniq.append(g)
    out.write_text(json.dumps(uniq, ensure_ascii=False, indent=1), encoding="utf-8")
    new = len(uniq) - len(old)
    log(f"Сохранено в {out}: {len(uniq)} приёмников" + (f" (новых {new})" if old and new > 0 else ""))
    return uniq


def clean_name(s: str, fallback: str = "") -> str:
    """Название приёмника без HTML: некоторые владельцы пишут туда разметку."""
    import html
    t = re.sub(r"<[^>]*>?", " ", s or "")
    t = re.sub(r"\s+", " ", html.unescape(t)).strip(" -|·,;")
    return t[:80] or fallback


def load(path: Path) -> list[dict]:
    cfgs = None
    if path.exists():
        try:
            cfgs = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfgs = None
    if cfgs is None:
        cfgs = [dict(b) for b in BUILTIN]
    for c in cfgs:
        if "<" in (c.get("name") or "") or "&" in (c.get("name") or ""):
            c["name"] = clean_name(c["name"], urlsplit(c["url"]).netloc)
    return cfgs


def dist_km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))
