"""Склейка одной и той же картинки, принятой несколькими приёмниками.

МКС передаёт одну картинку, а приёмники слышат её кусками и с разным
качеством. Каждую строку берём оттуда, где она принята лучше.

Совмещение:
  1. по времени на часах: строка 0 каждой картинки известна с точностью
     до задержки сети (обычно 0.5–3 с, то есть до ~20 строк);
  2. затем уточняем сдвиг по содержимому: ищем сдвиг, при котором
     перекрывающиеся строки сильнее всего похожи.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from robot36 import LINE, LINES, save_png

GROUP_S = 25.0        # картинки, начавшиеся в пределах этого времени, — одна передача
SEARCH_LINES = 45     # насколько строк уточнять сдвиг по содержимому (задержки сети до ~6 с)
MIN_OVERLAP = 15      # сколько общих хороших строк нужно, чтобы доверять сдвигу
GOOD_Q = 0.3
MIN_CORR = 0.5
MIN_MARGIN = 0.08     # лучший сдвиг должен заметно выигрывать у далёких


def _rowwise_corr(a: np.ndarray, b: np.ndarray) -> float:
    """Средняя корреляция строка-к-строке (яркость). Чувствительна к сдвигу на 1–2 строки."""
    a = a.astype(np.float32).mean(-1)
    b = b.astype(np.float32).mean(-1)
    a = a - a.mean(1, keepdims=True)
    b = b - b.mean(1, keepdims=True)
    num = (a * b).sum(1)
    den = np.sqrt((a * a).sum(1) * (b * b).sum(1)) + 1e-6
    ok = den > 1e-3 * a.shape[1] * 100      # однотонные строки ничего не говорят о сдвиге
    if ok.sum() < MIN_OVERLAP // 2:
        return -1.0
    return float(np.mean(num[ok] / den[ok]))


def _best_shift(base, other, guess: int):
    """Сдвиг s: строка n картинки other = строка n+s базовой.

    Сравниваются только строки, хорошо принятые обоими приёмниками.
    Если таких меньше MIN_OVERLAP, сдвиг не определяется (None).
    """
    scores = {}
    n = np.arange(LINES)
    for s in sorted(set(range(guess - SEARCH_LINES, guess + SEARCH_LINES + 1)) | {0}):
        m = n + s
        sel = (m >= 0) & (m < LINES) & (other["q"] > GOOD_Q)
        sel[sel] &= base["q"][m[sel]] > GOOD_Q
        if sel.sum() < MIN_OVERLAP:
            continue
        scores[s] = _rowwise_corr(base["rgb"][m[sel]], other["rgb"][n[sel]])
    if not scores:
        return None, -1.0, 0.0
    best = max(scores, key=scores.get)
    far = [v for k, v in scores.items() if abs(k - best) > 3]
    margin = scores[best] - (max(far) if far else -1.0)
    return best, scores[best], margin


def _span(r):
    """Интервал времени на часах, где у картинки есть данные."""
    rows = np.flatnonzero(r["q"] > 0)
    if not len(rows):
        rows = np.array([0, LINES - 1])
    return r["w0"] + rows[0] * LINE, r["w0"] + rows[-1] * LINE


def group(records: list[dict]) -> list[list[dict]]:
    """Одна передача — куски, которые по времени пересекаются (картинки идут раз в ~2 мин)."""
    recs = sorted([r for r in records if r.get("w0") is not None], key=lambda r: _span(r)[0])
    groups: list[list[dict]] = []
    end = None
    for r in recs:
        a, b = _span(r)
        if groups and a < end + GROUP_S * 0.2:
            groups[-1].append(r)
            end = max(end, b)
        else:
            groups.append([r])
            end = b
    return groups


def combine(group_recs: list[dict]):
    """Вернуть (rgb, откуда какая строка, описание) или None, если склеивать нечего."""
    if len(group_recs) < 2:
        return None
    # база: картинка с найденным началом, иначе самая полная
    base = max(group_recs, key=lambda r: (r["start"], float((r["q"] > 0.15).sum())))
    out = base["rgb"].copy()
    qbest = base["q"].copy()
    src = np.full(LINES, -1, int)
    src[base["q"] > 0] = 0
    used = [base]
    notes = []
    for r in group_recs:
        if r is base:
            continue
        if r["start"] and base["start"]:
            s, c, mg = 0, 1.0, 1.0          # обе картинки привязаны к началу — сдвиг известен
        else:
            guess = round((r["w0"] - base["w0"]) / LINE)
            s, c, mg = _best_shift(base, r, guess)
        if s is None:
            notes.append(f"{r['rx']}: нет общих строк, не склеиваю")
            continue
        if c < MIN_CORR or mg < MIN_MARGIN:
            notes.append(f"{r['rx']}: не уверен в совмещении (корр. {c:.2f}), не склеиваю")
            continue
        k = len(used)
        used.append(r)
        notes.append(f"{r['rx']}: сдвиг {s:+d} строк, корр. {c:.2f}")
        for n in range(LINES):
            m = n + s
            if 0 <= m < LINES and r["q"][n] > qbest[m] + 0.05:
                out[m] = r["rgb"][n]
                qbest[m] = r["q"][n]
                src[m] = k
    if len(used) < 2:
        return None, None, used, notes
    return out, src, used, notes


def combine_session(records: list[dict], outdir: Path, log=print) -> list[Path]:
    saved = []
    for i, g in enumerate(group(records), 1):
        if len({r["rx"] for r in g}) < 2:
            continue
        res = combine(g)
        if res is None:
            continue
        rgb, src, used, notes = res
        if rgb is None:
            log(f"Картинка {i}: приёмники {', '.join(sorted({r['rx'] for r in g}))} — " + "; ".join(notes))
            continue
        p = Path(outdir) / f"combined_{i:02d}.png"
        save_png(rgb, p)
        saved.append(p)
        share = ", ".join(f"{u['rx']} {int((src == k).sum())}" for k, u in enumerate(used))
        log(f"Склейка {p.name}: строк из каждого приёмника — {share}. " + "; ".join(notes))
    return saved
