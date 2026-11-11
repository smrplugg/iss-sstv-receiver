"""Пересборка всех записей нынешним декодером и склейка внутри каждого сеанса.

Для каждого сеанса sstv_out/<дата_время>/<приёмник>/audio*.wav:
  * декодирует WAV целиком (не «на лету», а по всей записи сразу);
  * сохраняет картинки в sstv_out/<сеанс>/redecoded/<приёмник>_NN.png;
  * склеивает куски одной передачи с разных приёмников → redecoded/combined_NN.png;
  * лучшие картинки всех сеансов копирует в sstv_out/best/ (в имени — качество).
Оригиналы не трогаются.

Время начала записи берётся из audio*.json (пишется с версии 11.11), а для
старых записей — из времени изменения файла минус длительность.
"""
from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

import numpy as np

import robot36 as r
from combine import combine_session


def _read_wav(p: Path):
    with wave.open(str(p)) as w:
        fs = w.getframerate()
        ch = w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    return fs, a


def _start_time(p: Path, dur: float, mtimes: dict | None) -> float:
    meta = p.with_suffix(".json")
    if meta.exists():
        try:
            return float(json.loads(meta.read_text(encoding="utf-8"))["start"])
        except Exception:  # noqa: BLE001
            pass
    end = (mtimes or {}).get(str(p)) or p.stat().st_mtime
    return end - dur


def redecode_session(sdir: Path, log=print, mtimes: dict | None = None, min_quality=0.17):
    out = sdir / "redecoded"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    records, made = [], []
    wavs = sorted(sdir.glob("audio*.wav")) + sorted(sdir.glob("*/audio*.wav"))
    for wav in wavs:
        # самые первые сеансы писались прямо в папку сеанса (только Goonhilly)
        rx = wav.parent.name if wav.parent != sdir else "goonhilly"
        try:
            fs, a = _read_wav(wav)
        except Exception as e:  # noqa: BLE001
            log(f"  {rx}/{wav.name}: не читается ({e})")
            continue
        if len(a) < fs * 5:
            continue
        dur = len(a) / fs
        t_start = _start_time(wav, dur, mtimes)
        x = r.to_fs(a, fs)
        f, ok = r.inst_freq(x)
        k = 0
        for ch in r.find_chains(f, ok):
            anc = r.anchor_of(f, ok, ch, 0, len(x))
            rgb, ng = r.render(f, ok, ch, anchor=anc)
            if ch["quality"] < min_quality or ng < 15:
                continue
            k += 1
            tag = "" if wav.stem == "audio" else wav.stem.replace("audio", "")
            p = out / f"{rx}{tag}_{k:02d}.png"
            r.save_png(rgb, p)
            made.append((ch["quality"], ng, p))
            w0 = t_start + ch["starts"][0] / r.FS
            records.append(dict(rgb=rgb, q=ch["line_q"], w0=w0, start=bool(ch.get("lead")),
                                anchor=anc, lines=ng, rx=rx[:24], path=p))
            log(f"  {rx}{tag}: картинка {k}: {ng} строк, качество {ch['quality']:.2f}"
                f"{'' if ch.get('lead') else ', начало пропущено'}")
    comb = []
    if records:
        try:
            comb = combine_session(records, out, log=lambda m: log("  " + m))
        except Exception as e:  # noqa: BLE001
            log(f"  склейка не удалась: {e}")
    return made, comb


def redecode_tree(root: Path, log=print, mtimes: dict | None = None, top: int = 40, skip=None):
    root = Path(root)
    sessions = sorted(d for d in root.iterdir() if d.is_dir() and d.name[:8].isdigit()
                      and d.name not in (skip or ()))
    all_made, all_comb = [], []
    for sdir in sessions:
        if not (list(sdir.glob("*/audio*.wav")) or list(sdir.glob("audio*.wav"))):
            continue
        log(f"Сеанс {sdir.name}:")
        made, comb = redecode_session(sdir, log=log, mtimes=mtimes)
        all_made += [(q, n, p, sdir.name) for q, n, p in made]
        all_comb += [(sdir.name, p) for p in comb]
    best = root / "best"
    if best.exists():
        shutil.rmtree(best)
    best.mkdir()
    # склейки — всегда; одиночные — лучшие по «качество × полнота»
    for sname, p in all_comb:
        shutil.copy(p, best / f"{sname}_{p.name}")
    ranked = sorted(all_made, key=lambda x: -(x[0] * min(1.0, x[1] / 200)))
    for q, n, p, sname in ranked[:top]:
        shutil.copy(p, best / f"{sname}_q{int(q * 100):02d}_{n}стр_{p.name}")
    log(f"Готово: картинок {len(all_made)}, склеек {len(all_comb)}. Лучшие — в {best}")
    return all_made, all_comb
