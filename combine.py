"""Склейка одной и той же картинки, принятой несколькими приёмниками.

МКС передаёт одну картинку, а приёмники слышат её кусками и с разным
качеством. Каждая строка — среднее по всем приёмникам, где она принята
не сильно хуже лучшего, с весом по качеству (q²): шум у разных приёмников
независимый, и при N сопоставимых приёмах он падает примерно в √N раз.
Строки, которые явно не похожи на остальные (сбой, щелчок), в среднее не идут.

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

Q_MIN = 0.15          # строки хуже этого вообще не используются
KEEP = 0.6            # в среднее идут строки не хуже KEEP × лучшей по этой строке
OUTLIER = 2.5         # строка отбрасывается, если отличается от среднего сильнее OUTLIER × медианы

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


def merge(members, fill=None):
    """Средневзвешенная картинка из совмещённых кусков.

    members: [(запись, сдвиг)], строка n записи = строка n+сдвиг результата.
    fill: картинка, из которой берутся строки, не принятые никем (повторы при провалах).
    Возвращает (rgb, лучший источник каждой строки, число усреднённых, качество строки).
    """
    K = len(members)
    A = np.zeros((K, LINES) + members[0][0]["rgb"].shape[1:], np.float32)
    Q = np.zeros((K, LINES), np.float32)
    n = np.arange(LINES)
    for k, (r, s) in enumerate(members):
        m = n + s
        sel = (m >= 0) & (m < LINES) & (np.asarray(r["q"]) > Q_MIN)
        A[k, m[sel]] = r["rgb"][n[sel]]
        Q[k, m[sel]] = np.asarray(r["q"])[n[sel]]
    qmax = Q.max(0)
    W = np.where((Q > 0) & (Q >= KEEP * qmax[None]), Q * Q, 0.0)

    def wmean(W):
        return (A * W[:, :, None, None]).sum(0) / np.maximum(W.sum(0), 1e-9)[:, None, None]

    mean = wmean(W)
    cnt = (W > 0).sum(0)
    if K >= 3:
        d = np.abs(A - mean[None]).mean((2, 3))
        dm = np.where(W > 0, d, np.nan)
        with np.errstate(all="ignore"):
            med = np.nanmedian(dm, axis=0)
        bad = (W > 0) & (cnt[None] >= 3) & (d > np.maximum(10.0, OUTLIER * np.nan_to_num(med))[None])
        if bad.any():
            W = np.where(bad, 0.0, W)
            mean = wmean(W)
            cnt = (W > 0).sum(0)
    out = np.clip(mean + 0.5, 0, 255).astype(np.uint8)
    if fill is not None:
        out[cnt == 0] = fill[cnt == 0]
    src = np.where(qmax > 0, Q.argmax(0), -1)
    # качество усреднённой строки: шум падает как √(Σq²)/max — оцениваем «эквивалентное» q
    q_out = np.minimum(1.0, np.sqrt(W.sum(0)))
    return out, src, cnt, q_out


def combine(group_recs: list[dict]):
    """Вернуть (rgb, откуда какая строка, описание) или None, если склеивать нечего."""
    if len(group_recs) < 2:
        return None
    # база: картинка с найденным началом, иначе самая полная
    base = max(group_recs, key=lambda r: (r["start"], float((r["q"] > 0.15).sum())))
    members = [(base, 0)]
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
        used.append(r)
        members.append((r, s))
        notes.append(f"{r['rx']}: сдвиг {s:+d} строк, корр. {c:.2f}")
    if len(used) < 2:
        return None, None, used, notes
    out, src, cnt, _ = merge(members, fill=base["rgb"])
    avg = int((cnt >= 2).sum())
    if avg:
        notes.append(f"усреднено строк: {avg}")
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
