"""Приём SSTV с МКС (437.550 МГц FM, Robot 36) сразу через много онлайн-приёмников.

Список приёмников берётся с карты rx-tx.info (--scan) и проверяется: живые
OpenWebRX с профилем на 437.550 сохраняются в receivers.json. Во время
пролёта программа держит открытыми до --max-rx лучших приёмников (те, над
которыми МКС дольше и выше), по мере движения МКС закрывает ушедшие за
горизонт и открывает следующие по трассе. У каждого свой доплер. Звук
пишется в WAV и сразу декодируется, в конце сеанса куски одной картинки
склеиваются по лучшим строкам.

Запуск:  python iss_sstv.py --scan          — найти и проверить приёмники (раз в день)
         python iss_sstv.py --check         — открыть все приёмники и отметить нерабочие
         python iss_sstv.py --list          — расписание сеансов
         python iss_sstv.py --test          — открыть приёмники на минуту прямо сейчас
         python iss_sstv.py                 — ждать и принимать все сеансы
         python iss_sstv.py --decode rec.wav — декодировать готовую запись
         python iss_sstv.py --redecode      — пересобрать все записи и склейки (лучшие — в sstv_out\\best)
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import scan
from combine import combine_session
from robot36 import FS as DEC_FS, LiveDecoder, decode_wav

F0_HZ = 437_550_000
C_KMS = 299_792.458

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
FALLBACK_TLE = (
    "1 25544U 98067A   26312.88495931  .00008565  00000-0  16189-3 0  9993",
    "2 25544  51.6321 304.0500 0007249   4.0569 356.0477 15.49264925594184",
)
DEFAULT_UNTIL = None          # не задано — неделя вперёд от запуска

TAP_JS = r"""
(() => {
  const orig = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    const r = orig.call(this, dest, ...rest);
    try {
      if (dest instanceof AudioDestinationNode && !this.__issTap) {
        this.__issTap = true;
        const ctx = this.context;
        const sp = ctx.createScriptProcessor(4096, 1, 1);
        sp.onaudioprocess = (e) => {
          const d = e.inputBuffer.getChannelData(0);
          const i16 = new Int16Array(d.length);
          for (let i = 0; i < d.length; i++) {
            const v = d[i] < -1 ? -1 : d[i] > 1 ? 1 : d[i];
            i16[i] = v * 32767;
          }
          const u8 = new Uint8Array(i16.buffer);
          let s = '';
          for (let i = 0; i < u8.length; i += 0x8000)
            s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
          if (window.__issPcm) window.__issPcm(btoa(s), ctx.sampleRate);
        };
        orig.call(this, sp);
        orig.call(sp, ctx.destination);   // выход sp — тишина, нужен только чтобы узел работал
      }
    } catch (err) { console.log('iss tap error', err); }
    return r;
  };
})();
"""

# Настройка страницы OpenWebRX: профиль с 437.550, NFM, широкая полоса, без шумоподавителя.
OWRX_SETUP_JS = r"""
async ([f, allowSwitch, testOnly]) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const hasUI = (n) => typeof UI !== 'undefined' && typeof UI[n] === 'function';
  const getD = () => (typeof getDemodulators === 'function' ? getDemodulators()[0]
      : (typeof demodulators !== 'undefined' ? demodulators[0] : null));
  const setMod = (m) => {
    if (hasUI('setModulation')) return UI.setModulation(m);
    try { return $('#openwebrx-panel-receiver').demodulatorPanel().setMode(m); } catch (e) {}
    if (typeof demodulator_analog_replace === 'function') return demodulator_analog_replace(m);
  };
  const setFreq = (f) => {
    if (hasUI('setFrequency')) return UI.setFrequency(f, false);
    const d = getD();
    if (d && typeof d.set_offset_frequency === 'function') return d.set_offset_frequency(f - center_freq);
    if (d) { d.offset_frequency = f - center_freq; d.set && d.set(); }
  };

  const note = [];
  for (let i = 0; i < 40 && !getD(); i++) await sleep(250);
  if (!getD()) return {ok: false, note: 'страница не загрузилась'};
  // запуск звука, если страница просит клик
  for (const sel of ['#openwebrx-autoplay-overlay', '.openwebrx-autoplay-overlay']) {
    const el = document.querySelector(sel); if (el && el.offsetParent !== null) el.click();
  }
  try { audioEngine.resume && audioEngine.resume(); } catch (e) {}
  const covers = () => typeof center_freq === 'number' && Math.abs(f - center_freq) < bandwidth / 2 - 20000;
  if (!covers()) {
    let st = null;
    try { st = await (await fetch('status.json')).json(); } catch (e) {}
    const lb = document.querySelector('#openwebrx-sdr-profiles-listbox');
    let target = null, best = 2;
    if (st && lb) {
      for (const sdr of st.sdrs) for (const p of sdr.profiles) {
        const r = Math.abs(f - p.center_freq) / (p.sample_rate / 2);
        if (Math.abs(f - p.center_freq) < p.sample_rate / 2 - 20000 && r < best) {
          const o = [...lb.options].find(o => o.textContent.trim().endsWith(p.name.trim()));
          if (o) { target = o; best = r; }
        }
      }
    }
    if (!target) return {ok: false, note: 'нет профиля с 437.550'};
    const clients = document.querySelector('#openwebrx-bar-clients')?.textContent || '';
    if (testOnly) return {ok: true, note: 'тест: приёмник на другом диапазоне (' + clients.trim() + '), не переключаю, проверяю только звук'};
    if (!allowSwitch) return {ok: false, busy: true, note: 'приёмник на другом диапазоне (' + clients.trim() + '), не переключаю'};
    lb.value = target.value; sdr_profile_changed();
    note.push('профиль переключён на «' + target.textContent.trim() + '» (' + clients.trim() + ')');
    for (let i = 0; i < 40 && !covers(); i++) await sleep(250);
    if (!covers()) return {ok: false, note: 'профиль не переключился'};
  }
  if (testOnly && !covers()) return {ok: true, note: note.join('; ')};
  setMod('nfm'); await sleep(500);
  const d = getD();
  try { d.setBandpass({low_cut: -7500, high_cut: 7500}); } catch (e) {}
  try { d.setSquelch(-150); } catch (e) {}
  setFreq(f);
  return {ok: true, note: note.join('; '), ctx: audioEngine.audioContext && audioEngine.audioContext.state};
}
"""

WEBSDR_SETUP_JS = r"""
async (hz) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const khz = String(((hz || 437550000) / 1000).toFixed(2));
  await sleep(1500);
  const b = document.getElementById('autoplay-start');
  if (b && b.offsetParent !== null) b.click();
  await sleep(800);
  const a = [...document.querySelectorAll('[onclick]')].find(e => /437\.550\s*MHz\s*FM/i.test(e.textContent));
  if (a) { a.click(); await sleep(800); return {ok: true, note: ''}; }
  // другой WebSDR: частота в поле (сам переключит диапазон) и режим FM
  const inp = document.querySelector('input[name=frequency]');
  if (inp) {
    inp.value = khz;
    if (typeof setfreqif === 'function') { try { setfreqif(khz); } catch (e) {} }
    inp.dispatchEvent(new Event('change', {bubbles: true}));
  }
  await sleep(700);
  const fm = [...document.querySelectorAll('input[type=button],button')].find(e => /^\s*FM\s*$/i.test(e.value || e.textContent));
  if (fm) fm.click();
  else if (typeof set_mode === 'function') { try { set_mode('fm'); } catch (e) {} }
  await sleep(500);
  return {ok: !!inp, note: inp ? 'пресета нет: частота и FM выставлены вручную' : 'не найдено поле частоты'};
}
"""

# Запуск настройки без ожидания: результат появится в window.__issSetup
RUN_SETUP_JS = r"""
([src, args]) => {
  window.__issSetup = null;
  const fn = eval('(' + src + ')');
  Promise.resolve(fn(args)).then(r => { window.__issSetup = r || {ok: true}; })
    .catch(e => { window.__issSetup = {ok: false, note: String(e)}; });
  return true;
}
"""

OWRX_TUNE_JS = r"""
(f) => {
  const hasUI = (n) => typeof UI !== 'undefined' && typeof UI[n] === 'function';
  const getD = () => (typeof getDemodulators === 'function' ? getDemodulators()[0]
      : (typeof demodulators !== 'undefined' ? demodulators[0] : null));
  const setMod = (m) => {
    if (hasUI('setModulation')) return UI.setModulation(m);
    try { return $('#openwebrx-panel-receiver').demodulatorPanel().setMode(m); } catch (e) {}
    if (typeof demodulator_analog_replace === 'function') return demodulator_analog_replace(m);
  };
  const setFreq = (f) => {
    if (hasUI('setFrequency')) return UI.setFrequency(f, false);
    const d = getD();
    if (d && typeof d.set_offset_frequency === 'function') return d.set_offset_frequency(f - center_freq);
    if (d) { d.offset_frequency = f - center_freq; d.set && d.set(); }
  };
  if (typeof center_freq !== 'number' || Math.abs(f - center_freq) > bandwidth / 2 - 20000) return false;
  const d = getD();
  if (!d) return false;
  try {
    if (d.get_modulation && d.get_modulation() !== 'nfm') { setMod('nfm'); d.setBandpass({low_cut: -7500, high_cut: 7500}); }
  } catch (e) {}
  setFreq(f);
  return true;
}
"""


LOGFILE: Path | None = None
LOG_HOOKS: list = []          # интерфейс подписывается на сообщения журнала
STATUS_HOOKS: list = []


def log(msg: str):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    try:
        sys.stdout.write(("\r\033[K" if _tty() else "") + line + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    for h in LOG_HOOKS:
        try:
            h(line)
        except Exception:  # noqa: BLE001
            pass
    if LOGFILE is not None:
        try:
            with open(LOGFILE, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except Exception:  # noqa: BLE001
            pass


def _tty() -> bool:
    try:
        return bool(sys.stdout) and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def status(msg: str):
    # строка не должна переноситься, иначе \r затирает только хвост и всё слипается
    import shutil
    for h in STATUS_HOOKS:
        try:
            h(msg)
        except Exception:  # noqa: BLE001
            pass
    if not _tty():          # без консоли (окно, pythonw) строку состояния в файл не пишем
        return
    width = shutil.get_terminal_size((100, 20)).columns - 1
    try:
        sys.stdout.write("\r\033[K" + msg[:width])
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def fmt_dur(sec: float) -> str:
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def local(dt: datetime, fmt="%d.%m %H:%M:%S") -> str:
    return dt.astimezone().strftime(fmt)


# ---------------- орбита ----------------
class Orbit:
    def __init__(self, tle_file: str | None = None, norad: int = 25544, tle_text: str | None = None):
        from skyfield.api import EarthSatellite, load
        import tle as tlemod
        self.loaded = time.time()
        self.ts = load.timescale(builtin=True)
        if tle_text:
            nm, l1, l2 = tlemod.parse_tle(tle_text)
            src = "введён вручную"
        elif tle_file:
            nm, l1, l2 = tlemod.parse_tle(Path(tle_file).read_text())
            src = tle_file
        else:
            nm, l1, l2, src = tlemod.get_tle(norad, log=log)
        self.name, self.l1, self.l2, self.src = nm, l1, l2, src
        self.norad = int(l1[2:7])
        self.manual = bool(tle_text or tle_file)
        self.sat = EarthSatellite(l1, l2, nm, self.ts)
        self.age = (datetime.now(timezone.utc) - self.sat.epoch.utc_datetime()).total_seconds() / 86400
        log(f"TLE: {nm} ({self.norad}), {src}, возраст {self.age:.1f} сут.")
        if self.age > 3:
            log("ВНИМАНИЕ: TLE старше 3 суток, время пролётов может уйти на минуту и больше.")

    def subpoint(self, dts):
        """Подспутниковые точки (lat, lon, высота км) для списка datetime."""
        from skyfield.api import wgs84
        g = self.sat.at(self.ts.from_datetimes(list(dts)))
        sp = wgs84.subpoint_of(g)
        h = wgs84.height_of(g).km
        return sp.latitude.degrees, sp.longitude.degrees, h

    @staticmethod
    def topo(cfg):
        from skyfield.api import wgs84
        return wgs84.latlon(cfg["lat"], cfg["lon"], elevation_m=cfg.get("alt_m", 0))

    def passes(self, topo, start: datetime, end: datetime, min_max_el: float, horizon: float = 3.0):
        t, ev = self.sat.find_events(topo, self.ts.from_datetime(start),
                                     self.ts.from_datetime(end), altitude_degrees=horizon)
        out, cur = [], {}
        for ti, e in zip(t, ev):
            dt = ti.utc_datetime()
            if e == 0:
                cur = {"aos": dt}
            elif e == 1:
                cur.setdefault("aos", start)   # пролёт уже идёт
                cur["tca"] = dt
                cur["max_el"] = self.look(topo, dt)[0]
            elif e == 2 and "tca" in cur:
                cur["los"] = dt
                if cur["max_el"] >= min_max_el:
                    out.append(cur)
                cur = {}
        return out

    def look(self, topo, dt: datetime):
        """угол места, азимут, доплеровская поправка (Гц)."""
        p = (self.sat - topo).at(self.ts.from_datetime(dt))
        alt, az, _ = p.altaz()
        rr = p.frame_latlon_and_rates(topo)[5].km_per_s
        return alt.degrees, az.degrees, -F0_HZ * rr / C_KMS


# ---------------- один приёмник ----------------
class Rx:
    def __init__(self, cfg: dict, orbit: Orbit, args):
        from urllib.parse import urlsplit
        self.cfg, self.orbit, self.args = cfg, orbit, args
        u = urlsplit(cfg["url"])
        self.key = re.sub(r"[^A-Za-z0-9._-]+", "_", (u.netloc + u.path).strip("/"))[:60]
        self.short = cfg.get("short") or self.key[:14]
        self.fail_count = 0
        self.starts = 0
        self.topo = Orbit.topo(cfg)
        self.page = None
        self.chunks: list[tuple[np.ndarray, int]] = []
        self.session_records: list[dict] = []
        self.session_images: list[Path] = []
        self.el = 0.0
        self.az = 0.0
        self.dop = 0.0
        self.strength = 0.0
        self._str_t = 0.0
        self.on_audio = None          # (rx, float32 12 кГц) — для звука в интерфейсе
        self.on_image = None          # (rx, path) — для галереи
        self.reset()

    def reset(self):
        self.active = False
        self.failed_at = None
        self.wav = None
        self.dec = None
        self.images: list[Path] = []
        self.tuned_hz = None
        self.last_sound = time.time()
        self.opened_at = time.time()
        self.reloads = 0
        self.level = None
        self.warned = False
        self.yielded = False
        self.yield_at = 0.0
        self.switched_at = 0.0
        self.state = "idle"
        self.t_open = time.time()
        self.got_sound = False

    def may_switch(self) -> bool:
        """Можно ли переключать общий тюнер OpenWebRX на 70 см."""
        if self.args.no_switch:
            return False
        return not self.yielded or time.time() - self.yield_at > self.args.yield_min * 60

    # --- страница ---
    def _on_pcm(self, b64: str, rate: int):
        self.chunks.append((np.frombuffer(base64.b64decode(b64), dtype=np.int16), int(rate)))

    def open(self, browser):
        """Начать открытие страницы, не дожидаясь загрузки (дальше — в poll_ready)."""
        if self.page is not None:
            try:
                self.page.close()
            except Exception:  # noqa: BLE001
                pass
        self.chunks = []
        self.state = "loading"
        self.t_open = time.time()
        # у любительских приёмников часто «кривые» сертификаты HTTPS — не мешаем этим
        self.page = browser.new_page(viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
        self.page.expose_function("__issPcm", self._on_pcm)
        self.page.add_init_script(TAP_JS)
        url = self.cfg["url"]
        if self.cfg["kind"] == "owrx":
            url = url.rstrip("/") + f"/#freq={F0_HZ},mod=nfm,sql=-150"
        self.page.goto(url, wait_until="commit", timeout=25_000)
        self.tuned_hz = None
        self.opened_at = self.last_sound = time.time()

    def poll_ready(self):
        """Довести открытие до конца; True — приёмник готов. Ошибка — исключение."""
        el = time.time() - self.t_open
        if self.state == "loading":
            try:
                rs = self.page.evaluate("document.readyState")
            except Exception:  # noqa: BLE001
                rs = None
            if rs == "complete" or (rs and el > 20):
                if self.cfg["kind"] == "owrx":
                    src, a = OWRX_SETUP_JS, [F0_HZ, self.may_switch(), bool(getattr(self.args, "test", False))]
                else:
                    src, a = WEBSDR_SETUP_JS, F0_HZ
                self.page.evaluate(RUN_SETUP_JS, [src.strip(), a])
                self.state = "setup"
            elif el > 40:
                raise RuntimeError("страница не загрузилась")
            return False
        if self.state == "setup":
            res = self.page.evaluate("window.__issSetup")
            if res is None:
                if el > 60:
                    raise RuntimeError("страница не настроилась")
                return False
            if not res.get("ok"):
                raise RuntimeError(res.get("note") or "не удалось настроить приёмник")
            if "переключён" in (res.get("note") or ""):
                self.switched_at = time.time()
            if res.get("note"):
                log(f"{self.short}: {res['note']}")
            self.state = "running"
            self.opened_at = self.last_sound = time.time()
            return True
        return True

    def tune(self, hz: float):
        if self.cfg["kind"] == "websdr":
            self.page.evaluate(
                """(f) => { const e = document.querySelector('input[name=frequency]');
                            e.value = f; e.dispatchEvent(new Event('change', {bubbles: true})); }""",
                f"{hz / 1000:.2f}")
        else:
            if not self.page.evaluate(OWRX_TUNE_JS, int(round(hz))):
                if getattr(self.args, "test", False):
                    self.tuned_hz = hz      # в тесте диапазон не трогаем, просто слушаем
                    return
                # Профиль сменил другой слушатель. Не перетягиваем тюнер:
                # уступаем и пробуем вернуть 70 см не раньше чем через 3 минуты.
                if not self.yielded:
                    self.yielded = True
                    self.yield_at = time.time()
                    log(f"{self.short}: кто-то сменил диапазон — верну 437.550 "
                        f"через {self.args.yield_min * 60:.0f} с.")
                    return
                if not self.may_switch():
                    return
                res = self.page.evaluate(OWRX_SETUP_JS, [int(round(hz)), True, False])
                self.yielded = False
                if res.get("ok"):
                    self.switched_at = time.time()
                    log(f"{self.short}: вернул 437.550 ({res.get('note', '')}).")
                return
        self.tuned_hz = hz

    # --- жизненный цикл в сеансе ---
    def start(self, browser, outdir: Path):
        self.reset()
        self.starts += 1
        self.outdir = outdir / self.key
        self.outdir.mkdir(parents=True, exist_ok=True)
        log(f"{self.short}: открываю {self.cfg['name']} …")
        self.open(browser)
        self.active = True

    def step(self, now: datetime, test: bool):
        if self.state != "running":
            self.el = self.orbit.look(self.topo, now)[0]
            if not self.poll_ready():
                return None
        el, az, dop = self.orbit.look(self.topo, now)
        if test:
            dop = 0.0
        self.el, self.az, self.dop = el, az, dop
        want = F0_HZ + dop
        if self.cfg["kind"] == "websdr":
            # у WebSDR перестройка даёт провал звука: посреди картинки — только при большом уходе
            in_img = self.dec is not None and self.dec.progress is not None
            limit = 3000.0 if in_img else self.args.retune_khz * 1000
        else:
            limit = 150.0   # OpenWebRX двигает демодулятор плавно, без провалов
        if self.tuned_hz is None or abs(want - self.tuned_hz) >= limit:
            self.tune(want)

        for a, rate in self.take():
            if self.dec is None:
                wname = "audio.wav" if self.starts <= 1 else f"audio_{self.starts}.wav"
                # пишем уже пересчитанный в 12 кГц звук: выше 6 кГц у приёмников ничего нет,
                # а файлы вчетверо меньше
                self.wav = wave.open(str(self.outdir / wname), "wb")
                self.wav.setnchannels(1)
                self.wav.setsampwidth(2)
                self.wav.setframerate(DEC_FS)
                self._wn = 0
                try:
                    (self.outdir / (Path(wname).stem + ".json")).write_text(json.dumps(
                        dict(start=time.time(), url=self.cfg["url"], name=self.cfg.get("name", "")),
                        ensure_ascii=False), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                self.dec = LiveDecoder(rate, self.outdir,
                                       prefix="iss" if self.starts <= 1 else f"iss{self.starts}_",
                                       log=lambda m, s=self.short: log(f"{s}: {m}"),
                                       on_image=self._image_saved)
                log(f"{self.short}: звук идёт")
            f = a.astype(np.float32) / 32768
            self.dec.feed(f)
            new = self.dec.buf[self._wn:self.dec.n]
            self._wn = self.dec.n
            self.wav.writeframes((np.clip(new, -1, 1) * 32767).astype(np.int16).tobytes())
            if self.on_audio is not None and len(new):
                try:
                    self.on_audio(self, new)
                except Exception:  # noqa: BLE001
                    pass
            self.level = 20 * np.log10(max(1e-6, float(np.sqrt(np.mean(f * f)))))
            if np.any(a):
                self.last_sound = time.time()
            self.got_sound = self.got_sound or bool(np.any(a))
        if self.dec is not None:
            self.dec.poll()
            if time.time() - self._str_t > 1.0:
                self._str_t = time.time()
                try:
                    self.strength = self.dec.strength()
                except Exception:  # noqa: BLE001
                    self.strength = 0.0

        silent = time.time() - self.last_sound
        if self.yielded:
            self.last_sound = time.time()
        elif silent > 8 and time.time() - self.opened_at > 15:
            self.reloads += 1
            log(f"{self.short}: звук пропал на {silent:.0f} с — переоткрываю страницу (раз {self.reloads}).")
            return "reload"
        if self.wav is None and time.time() - self.opened_at > 25 and not self.warned:
            self.warned = True
            log(f"{self.short}: звука нет. Если окно браузера видно, нажми на странице запуск звука.")
        return None

    def _image_saved(self, p):
        self.images.append(p)
        if self.on_image:
            try:
                self.on_image(self, p)
            except Exception:  # noqa: BLE001
                pass

    def take(self):
        c, self.chunks = self.chunks, []
        return c

    def stop(self):
        if self.dec is not None:
            try:
                self.dec.finish()
            except Exception as e:  # noqa: BLE001
                log(f"{self.short}: ошибка при дорисовке: {e}")
            self.session_records += [dict(r, rx=self.short) for r in self.dec.records]
            self.session_images += self.images
        if self.wav is not None:
            self.wav.close()
        if self.page is not None:
            try:
                self.page.close()
            except Exception:  # noqa: BLE001
                pass
        self.page = None
        self.active = False
        self.dec = None
        log(f"{self.short}: закончил, картинок {len(self.images)}")

    def badge(self):
        if not self.active:
            return f"{self.short} -- к{len(self.session_images)}"
        if self.state != "running":
            return f"{self.short} открываю"
        prog = self.dec.progress if self.dec else None
        st = f"~{prog}" if prog is not None else ("жду" if self.dec else "нет звука")
        if self.yielded:
            st = "уступил"
        return f"{self.short} {self.el:3.0f}° {st} к{len(self.session_images) + len(self.images)}"




# ---------------- расписание ----------------
class Visibility:
    """Углы места МКС над всеми приёмниками на сетке времени (быстро, разом для всех)."""

    STEP = 20.0

    def __init__(self, orbit: Orbit, rxs: list[Rx], start: datetime, end: datetime):
        from skyfield.framelib import itrs
        n = max(2, int((end - start).total_seconds() / self.STEP) + 1)
        self.t0 = start
        self.times = [start + timedelta(seconds=i * self.STEP) for i in range(n)]
        ts = orbit.ts.from_datetimes(self.times)
        sat = orbit.sat.at(ts).frame_xyz(itrs).km.T              # (n, 3)
        lat = np.radians([r.cfg["lat"] for r in rxs])
        lon = np.radians([r.cfg["lon"] for r in rxs])
        R = 6371.0
        st = np.stack([R * np.cos(lat) * np.cos(lon), R * np.cos(lat) * np.sin(lon), R * np.sin(lat)], 1)
        up = st / R
        rho = sat[:, None, :] - st[None, :, :]                   # (n, m, 3)
        self.el = np.degrees(np.arcsin((rho * up[None]).sum(-1) / np.linalg.norm(rho, axis=-1)))
        self.rxs = rxs

    def idx(self, dt: datetime) -> int:
        return int(np.clip(round((dt - self.t0).total_seconds() / self.STEP), 0, len(self.times) - 1))

    def remaining_above(self, j: int, dt: datetime, min_el: float) -> float:
        """Сколько секунд ещё МКС будет выше min_el над приёмником j."""
        i = self.idx(dt)
        col = self.el[i:, j] >= min_el
        if not col.size or not col[0]:
            return 0.0
        k = np.argmin(col) if not col.all() else len(col)
        return k * self.STEP


def build_sessions(vis: Visibility, args):
    """Сеанс — время, когда МКС хотя бы над одним приёмником выше min_el."""
    any_vis = (vis.el >= args.min_el).any(1)
    sessions, i = [], 0
    n = len(any_vis)
    while i < n:
        if not any_vis[i]:
            i += 1
            continue
        j = i
        while j < n and any_vis[j]:
            j += 1
        a, b = vis.times[i], vis.times[min(j, n - 1)]
        if sessions and a - sessions[-1]["b"] < timedelta(minutes=2):
            sessions[-1]["b"] = b
            sessions[-1]["j"] = j
        else:
            sessions.append(dict(a=a, b=b, i=i, j=j))
        i = j
    for s in sessions:
        sub = vis.el[s["i"]:s["j"]]
        best = sub.max(0)
        order = np.argsort(-best)
        s["n_rx"] = int((best >= args.min_el).sum())
        s["best"] = [(vis.rxs[k].short, float(best[k])) for k in order[:4] if best[k] >= args.min_el]
        s["a"] -= timedelta(seconds=args.lead)
        s["b"] += timedelta(seconds=args.tail)
    return sessions


def describe(s):
    top = ", ".join(f"{n} {e:.0f}°" for n, e in s["best"])
    return f"приёмников {s['n_rx']}; лучшие: {top}"


def pick(vis: Visibility, now: datetime, active: list[Rx], free: int, args) -> list[Rx]:
    """Какие приёмники открыть: дольше всех видят МКС, не рядом с уже открытыми."""
    cands = []
    for j, rx in enumerate(vis.rxs):
        if rx.active or free <= 0:
            continue
        if rx.fail_count >= 3:
            continue
        if rx.cfg.get("bad") and not rx.cfg.get("builtin"):
            continue
        if rx.failed_at and time.time() - rx.failed_at < 120 * rx.fail_count:
            continue
        el = float(vis.el[vis.idx(now), j])
        if el < args.min_el - 2:
            continue
        rem = vis.remaining_above(j, now, args.min_el)
        if rem < 60:
            continue
        # дольше видит и выше — лучше; встроенные проверенные — небольшой бонус
        score = rem + 4 * el + (60 if rx.cfg.get("builtin") else 0)
        # история: доля прошлых сеансов, где приёмник дал картинку (0.5 — неизвестно)
        if getattr(args, "use_history", False):
            score *= 0.6 + 0.8 * getattr(rx, "rating", 0.5)
        cands.append((score, rx))
    cands.sort(key=lambda x: -x[0])
    chosen = []
    for _, rx in cands:
        if len(chosen) >= free:
            break
        near = [o for o in active + chosen if scan.dist_km(o.cfg, rx.cfg) < args.min_sep_km]
        if near:
            continue
        chosen.append(rx)
    return chosen


def run_session(sess: dict, vis: Visibility, args, outroot: Path, test=False, ctl=None):
    from playwright.sync_api import sync_playwright
    name = sess["a"].astimezone().strftime("%Y%m%d_%H%M")
    outdir = outroot / ("test" if test else "") / name
    outdir.mkdir(parents=True, exist_ok=True)
    rxs = vis.rxs
    if ctl is not None:
        ctl.session_started(sess, outdir, test)
    for rx in rxs:
        rx.session_records, rx.session_images = [], []
        rx.active, rx.fail_count, rx.failed_at, rx.starts = False, 0, None, 0
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=args.headless,
                                 args=["--autoplay-policy=no-user-gesture-required"])
    used: list[Rx] = []
    last_pick = 0.0
    try:
        while True:
            now = datetime.now(timezone.utc)
            if now > sess["b"]:
                break
            if ctl is not None:
                ctl.tick(rxs)
                if ctl.stop_session:
                    log("Сеанс остановлен из интерфейса.")
                    break
            active = [r for r in rxs if r.active]
            # закрыть тех, над кем МКС ушла за горизонт (если не идёт картинка)
            for rx in active:
                j = rxs.index(rx)
                el_now = float(vis.el[vis.idx(now), j]) if not test else 90.0
                busy = rx.dec is not None and rx.dec.progress is not None
                if el_now < 0 or (el_now < args.min_el - 3 and not busy and
                                  vis.remaining_above(j, now, args.min_el) == 0 and
                                  float(vis.el[min(vis.idx(now) + 3, len(vis.times) - 1), j]) < el_now):
                    rx.stop()
            active = [r for r in rxs if r.active]
            # добрать лучших на свободные места
            if time.time() - last_pick > 5:
                last_pick = time.time()
                free = args.max_rx - len(active)
                if ctl is not None:
                    # приёмники, выключенные вручную в интерфейсе, закрываем
                    for rx in active:
                        if rx.cfg.get("disabled"):
                            rx.stop()
                    active = [r for r in rxs if r.active]
                    free = args.max_rx - len(active)
                    rxs_pick = [r for r in rxs if not r.cfg.get("disabled")]
                else:
                    rxs_pick = rxs
                chosen = ([r for r in rxs_pick if not r.active and r.fail_count < 1][:free] if test
                          else [r for r in pick(vis, now, active, free + 50, args)
                                if r in rxs_pick][:max(0, free)])
                for rx in chosen:
                    try:
                        rx.start(browser, outdir)
                        if rx not in used:
                            used.append(rx)
                        rx.fail_count = 0
                    except Exception as e:  # noqa: BLE001
                        rx.fail_count += 1
                        rx.failed_at = time.time()
                        rx.active = False
                        try:
                            if rx.page:
                                rx.page.close()
                        except Exception:  # noqa: BLE001
                            pass
                        rx.page = None
                        log(f"{rx.short}: не открылся ({str(e).splitlines()[0][:100]})")
                active = [r for r in rxs if r.active]
            if active:
                try:
                    active[0].page.wait_for_timeout(500)
                except Exception:  # noqa: BLE001
                    time.sleep(0.5)
            else:
                time.sleep(0.5)
            for rx in active:
                try:
                    if rx.step(now, test) == "reload":
                        if rx.reloads > 3:
                            log(f"{rx.short}: звук всё время пропадает, закрываю.")
                            rx.stop()
                            rx.fail_count, rx.failed_at = 3, time.time()
                        else:
                            rx.open(browser)
                except Exception as e:  # noqa: BLE001
                    msg = str(e).splitlines()[0][:100]
                    log(f"{rx.short}: ошибка ({msg}), закрываю.")
                    if "профиль не переключился" in msg or "нет профиля" in msg:
                        mark_bad(rx, msg)
                    try:
                        rx.stop()
                    except Exception:  # noqa: BLE001
                        rx.active = False
                    rx.fail_count += 1
                    rx.failed_at = time.time()
            left = (sess["b"] - now).total_seconds()
            total = sum(len(r.session_images) + len(r.images) for r in rxs)
            badges = " | ".join(r.badge() for r in rxs if r.active)
            status(f"[{len([r for r in rxs if r.active])} пр., карт {total}, конец через {fmt_dur(left)}] {badges}")
    except KeyboardInterrupt:
        log("Остановлено.")
        raise
    finally:
        for rx in rxs:
            if rx.active:
                rx.stop()
        for f in (browser.close, pw.stop):
            try:
                f()
            except Exception:  # noqa: BLE001
                pass
        sys.stdout.write("\n")
        records = [r for rx in rxs for r in rx.session_records]
        try:
            comb = combine_session(records, outdir, log=log)
        except Exception as e:  # noqa: BLE001
            comb = []
            log(f"Склейка не удалась: {e}")
        log(f"Сеанс закончен: приёмников слушали {len(used)}, картинок {len(records)}, "
            f"склеек {len(comb)}. Всё в {outdir}")
        if ctl is not None:
            ctl.session_finished(sess, outdir, records, comb, test)


CHECK_PATH: Path | None = None


def mark_bad(rx, why: str):
    """Запомнить приёмник как нерабочий (в check.json), чтобы не тратить на него время."""
    rx.cfg["bad"] = why
    rx.fail_count = 3
    if CHECK_PATH is None:
        return
    try:
        d = json.loads(CHECK_PATH.read_text(encoding="utf-8")) if CHECK_PATH.exists() else {}
        d[rx.cfg["url"]] = dict(ok=False, why=why, when=datetime.now().isoformat(timespec="minutes"))
        CHECK_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"{rx.short}: помечен как нерабочий, больше открывать не буду.")
    except Exception:  # noqa: BLE001
        pass


# ---------------- main ----------------
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":
        import os
        os.system("")  # включить ANSI-коды в консоли Windows

    from paths import DATA as here
    ap = argparse.ArgumentParser(description="SSTV с МКС через онлайн-приёмники")
    ap.add_argument("--scan", action="store_true",
                    help="найти приёмники на rx-tx.info, проверить их и сохранить receivers.json")
    ap.add_argument("--receivers", default=str(here / "receivers.json"), help="файл со списком приёмников")
    ap.add_argument("--only", help="только приёмники, в названии/адресе которых есть эти слова (через запятую)")
    ap.add_argument("--max-rx", type=int, default=8, help="сколько приёмников слушать одновременно")
    ap.add_argument("--min-sep-km", type=float, default=40, help="не брать одновременно приёмники ближе, км")
    ap.add_argument("--min-el", type=float, default=10, help="минимальный угол МКС над приёмником, °")
    ap.add_argument("--until", default=DEFAULT_UNTIL, help="до какого момента работать (ISO, например 2026-11-17T00:00); по умолчанию неделя")
    ap.add_argument("--once", action="store_true", help="только ближайший сеанс")
    ap.add_argument("--list", action="store_true", help="показать сеансы и выйти")
    ap.add_argument("--headless", action="store_true", help="не показывать окна браузера")
    ap.add_argument("--lead", type=int, default=60, help="за сколько секунд до сеанса начинать")
    ap.add_argument("--tail", type=int, default=20, help="сколько секунд слушать после")
    ap.add_argument("--retune-khz", type=float, default=1.0, help="шаг перестройки для Goonhilly, кГц")
    ap.add_argument("--tle", help="файл с TLE вместо скачивания")
    ap.add_argument("--out", default="sstv_out", help="папка для записей и картинок")
    ap.add_argument("--decode", metavar="WAV", help="декодировать готовый WAV и выйти")
    ap.add_argument("--redecode", action="store_true",
                    help="пересобрать все записи в sstv_out нынешним декодером и склеить по сеансам")
    ap.add_argument("--test", action="store_true", help="открыть приёмники на минуту прямо сейчас")
    ap.add_argument("--check", action="store_true",
                    help="проверить все приёмники из списка (звук, профиль 437.550) и запомнить нерабочие")
    ap.add_argument("--yield-min", type=float, default=1 / 3,
                    help="через сколько минут возвращать 437.550, если кто-то сменил диапазон (по умолчанию 20 с)")
    ap.add_argument("--no-switch", action="store_true",
                    help="никогда не переключать диапазон на OpenWebRX (только если он уже на 70 см)")
    args = ap.parse_args()

    global LOGFILE, CHECK_PATH
    Path(args.out).mkdir(parents=True, exist_ok=True)
    LOGFILE = Path(args.out) / "log.txt"
    if args.redecode:
        import redecode
        redecode.redecode_tree(Path(args.out), log=log)
        return
    if args.decode:
        saved = decode_wav(args.decode, log=log)
        log(f"Готово, картинок: {len(saved)}")
        return
    rpath = Path(args.receivers)
    if args.scan:
        scan.scan(rpath, log=log)
        return

    cfgs = scan.load(rpath)
    builtin_hosts = {b["url"] for b in scan.BUILTIN}
    for c in cfgs:
        c["builtin"] = c["url"] in builtin_hosts
    if not rpath.exists():
        log("Списка receivers.json нет — работаю с 5 проверенными приёмниками. "
            "Запусти с --scan, чтобы найти остальные.")
    cpath = rpath.with_name("check.json")
    CHECK_PATH = cpath
    if cpath.exists() and not args.check:
        try:
            chk = json.loads(cpath.read_text(encoding="utf-8"))
            for c in cfgs:
                r = chk.get(c["url"])
                if r and not r.get("ok"):
                    c["bad"] = r.get("why") or "не прошёл проверку"
        except Exception:  # noqa: BLE001
            pass
    if args.only:
        words = [w.strip().lower() for w in args.only.split(",") if w.strip()]
        cfgs = [c for c in cfgs if any(w in (c["url"] + " " + c.get("name", "") + " " +
                                             c.get("short", "")).lower() for w in words)]
    if not cfgs:
        log("Нет ни одного приёмника.")
        return
    orbit = Orbit(args.tle)
    rxs = [Rx(c, orbit, args) for c in cfgs]
    outroot = Path(args.out)

    if args.test:
        now = datetime.now(timezone.utc)
        log(f"Тест: открываю до {args.max_rx} приёмников на 60 секунд, 437.550 без доплера.")
        vis = Visibility(orbit, rxs, now, now + timedelta(seconds=90))
        sess = dict(a=now, b=now + timedelta(seconds=60))
        run_session(sess, vis, args, outroot, test=True)
        return

    if args.check:
        run_check(rxs, args, outroot, cpath)
        return

    until = (datetime.fromisoformat(args.until) if args.until
             else datetime.now(timezone.utc) + timedelta(days=7))
    if until.tzinfo is None:
        until = until.astimezone()

    def plan():
        start = datetime.now(timezone.utc)
        vis = Visibility(orbit, rxs, start, until)
        return vis, [x for x in build_sessions(vis, args) if x["b"] > start]

    vis, sessions = plan()
    if not sessions:
        log("Подходящих пролётов до конца эксперимента нет.")
        return
    nbad = sum(1 for c in cfgs if c.get("bad"))
    log(f"Приёмников в списке: {len(rxs)}" + (f" (не прошли проверку: {nbad})" if nbad else "")
        + f". Сеансов до конца эксперимента: {len(sessions)}")
    for x in sessions[: (len(sessions) if args.list else 6)]:
        log(f"  {local(x['a'], '%d.%m %H:%M')}–{local(x['b'], '%H:%M')}  {describe(x)}")
    if args.list:
        return

    while sessions:
        s = sessions[0]
        while True:
            left = (s["a"] - datetime.now(timezone.utc)).total_seconds()
            if left <= 0:
                break
            status(f"Следующий сеанс {local(s['a'], '%d.%m %H:%M')}–{local(s['b'], '%H:%M')} "
                   f"({describe(s)}); старт через {fmt_dur(left)}   (Ctrl+C — выход)")
            time.sleep(min(1.0, left))
        sys.stdout.write("\n")
        log(f"Сеанс {local(s['a'], '%H:%M')}–{local(s['b'], '%H:%M')}: {describe(s)}")
        run_session(s, vis, args, outroot)
        if args.once:
            break
        # раз в 12 часов — свежий TLE и пересчёт расписания
        if not args.tle and time.time() - orbit.loaded > 12 * 3600:
            log("Обновляю орбиту МКС (TLE) и расписание …")
            orbit = Orbit(None)
            for rx in rxs:
                rx.orbit = orbit
            vis, sessions = plan()
        else:
            now = datetime.now(timezone.utc)
            sessions = [x for x in sessions if x["b"] > now and x is not s]
    log("Расписание закончилось.")


def run_check(rxs: list[Rx], args, outroot: Path, cpath: Path, ctl=None):
    """Открыть все приёмники пачками и проверить, что настраиваются и дают звук.

    Диапазон при этом не переключается: проверяется только, что звук идёт
    и что у приёмника есть профиль с 437.550.
    """
    from playwright.sync_api import sync_playwright
    args.test = True                     # тестовый режим: ничего не переключаем
    outdir = outroot / "check" / datetime.now().strftime("%Y%m%d_%H%M")
    results = {}
    n = args.max_rx
    log(f"Проверяю {len(rxs)} приёмников пачками по {n} (примерно {len(rxs) / n * 0.6:.0f} мин) …")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    try:
        for i in range(0, len(rxs), n):
            batch = rxs[i:i + n]
            for rx in batch:
                rx.session_records, rx.session_images = [], []
                try:
                    rx.start(browser, outdir)
                except Exception as e:  # noqa: BLE001
                    results[rx.cfg["url"]] = dict(ok=False, why=str(e).splitlines()[0][:120])
                    rx.active = False
            t0 = time.time()
            if ctl is not None and ctl.stop_session:
                break
            while time.time() - t0 < 45:
                if ctl is not None:
                    ctl.tick(batch)
                    if ctl.stop_session:
                        break
                live = [r for r in batch if r.active and r.cfg["url"] not in results]
                if not live:
                    break
                try:
                    live[0].page.wait_for_timeout(500)
                except Exception:  # noqa: BLE001
                    time.sleep(0.5)
                for rx in live:
                    try:
                        rx.step(datetime.now(timezone.utc), True)
                        if rx.state == "running" and rx.got_sound and time.time() - rx.opened_at > 3:
                            results[rx.cfg["url"]] = dict(ok=True, why="")
                    except Exception as e:  # noqa: BLE001
                        results[rx.cfg["url"]] = dict(ok=False, why=str(e).splitlines()[0][:120])
                done = sum(1 for r in rxs[:i + n] if r.cfg["url"] in results)
                status(f"Проверено {done}/{len(rxs)}")
            for rx in batch:
                if rx.cfg["url"] not in results and not (ctl is not None and ctl.stop_session):
                    results[rx.cfg["url"]] = dict(
                        ok=False, why="звук не пошёл" if rx.state == "running" else "не настроился за 45 с")
                try:
                    if rx.active:
                        rx.page.close()
                except Exception:  # noqa: BLE001
                    pass
                rx.active, rx.page = False, None
                if rx.wav is not None:
                    rx.wav.close()
                rx.dec = rx.wav = None
    finally:
        for f in (browser.close, pw.stop):
            try:
                f()
            except Exception:  # noqa: BLE001
                pass
        sys.stdout.write("\n")
    stamp = datetime.now().isoformat(timespec="minutes")
    data = {}
    if ctl is not None and cpath.exists():      # из интерфейса проверяют выборочно — не теряем старое
        try:
            data = json.loads(cpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    data.update({u: dict(r, when=stamp) for u, r in results.items()})
    cpath.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    good = [u for u, r in results.items() if r["ok"]]
    log(f"Готово: работают {len(good)} из {len(results)}. Результат в {cpath}")
    why = {}
    for u, r in results.items():
        if not r["ok"]:
            why.setdefault(r["why"], []).append(u)
    for w, us in sorted(why.items(), key=lambda x: -len(x[1])):
        log(f"  {len(us):3d} × {w}")
    log("Не прошедшие проверку приёмники в сеансах пропускаются (кроме пяти встроенных).")
    return results


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write("\nВыход.\n")
