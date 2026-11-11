"""Стопки: одна и та же картинка, принятая в разных пролётах, складывается в одну.

За акцию МКС много раз передаёт один и тот же набор картинок по кругу, и каждую
программа ловит десятки раз — разными приёмниками в разных пролётах. Шум во всех
этих приёмах независимый, поэтому средневзвешенная картинка из N приёмов чище
любой отдельной примерно в √N раз.

Как:
  1. берутся все картинки, у которых сохранено качество строк (файл .json рядом;
     для старых записей — после «Пересобрать записи»). Из сеанса, который
     пересобран, берутся пересобранные, иначе принятые в эфире; склейки не берутся,
     чтобы один и тот же звук не попал в стопку дважды;
  2. картинки сравниваются по яркости строк при всех вертикальных сдвигах
     (у кусков без начала положение строк неизвестно); совпадение — если строки
     похожи на большом перекрытии и выбранный сдвиг явно лучше остальных;
  3. совпавшие картинки складываются тем же способом, что и склейка (combine.merge).

Результат: sstv_out/stack/stack_NN.png и stack_NN.json (кто вошёл в стопку).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from combine import merge
from robot36 import LINES, load_meta, save_png

GOOD_Q = 0.3          # строки для сравнения
MIN_OVERLAP = 40      # сколько общих хороших строк нужно для решения «та же картинка»
MIN_CORR = 0.62
MIN_MARGIN = 0.12
COLS = 80             # ширина «отпечатка» строки
D = np.subtract.outer(np.arange(LINES), np.arange(LINES)) + LINES - 1   # индекс сдвига i-j


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _features(rgb, q):
    y = rgb.astype(np.float32).mean(2)
    y = y.reshape(LINES, COLS, -1).mean(2)
    y -= y.mean(1, keepdims=True)
    nrm = np.sqrt((y * y).sum(1))
    ok = (np.asarray(q) > GOOD_Q) & (nrm > 3.0 * np.sqrt(COLS))   # однотонные строки не сравниваем
    return y / np.maximum(nrm, 1e-6)[:, None], ok


def _match(fa, va, fb, vb):
    """Лучший сдвиг s (строка b + s = строка a), корреляция, отрыв от далёких сдвигов."""
    V = (va[:, None] & vb[None, :]).ravel()
    if V.sum() < MIN_OVERLAP:
        return None, -1.0, 0.0
    M = (fa @ fb.T).ravel()
    sums = np.bincount(D.ravel(), weights=M * V, minlength=2 * LINES - 1)
    cnt = np.bincount(D.ravel(), weights=V, minlength=2 * LINES - 1)
    sc = np.where(cnt >= MIN_OVERLAP, sums / np.maximum(cnt, 1), -1.0)
    i = int(sc.argmax())
    if sc[i] <= -1:
        return None, -1.0, 0.0
    far = np.abs(np.arange(len(sc)) - i) > 3
    margin = sc[i] - (sc[far].max() if far.any() else -1.0)
    return i - (LINES - 1), float(sc[i]), float(margin)


def load_records(root: Path, log=print):
    recs = []
    for sdir in sorted(d for d in Path(root).iterdir() if d.is_dir() and d.name[:8].isdigit()):
        red = sdir / "redecoded"
        paths = sorted(red.glob("*.png")) if red.is_dir() else sorted(sdir.glob("*/iss*.png"))
        for p in paths:
            if p.name.startswith("combined"):
                continue
            m = load_meta(p)
            if m is None:
                continue
            try:
                rgb = np.asarray(Image.open(p).convert("RGB"))
            except Exception:  # noqa: BLE001
                continue
            if rgb.shape[0] == 2 * LINES:          # картинки сохраняются увеличенными вдвое
                rgb = rgb[::2, ::2]
            if rgb.shape[0] != LINES:
                continue
            rx = p.stem if red.is_dir() else p.parent.name
            recs.append(dict(rgb=rgb, q=m["q"], start=m.get("start", False), session=sdir.name,
                             rx=rx, path=str(p.relative_to(root))))
    return recs


def build(recs, log=print):
    """Разложить записи по стопкам. Возвращает список стопок: dict(members=[(rec, сдвиг, корр)], ...)."""
    # сначала самые полные и привязанные к началу — они задают положение строк в стопке
    order = sorted(recs, key=lambda r: (not r["start"], -float(np.sum(np.asarray(r["q"]) > GOOD_Q))))
    stacks = []
    for r in order:
        fr, vr = _features(r["rgb"], r["q"])
        if vr.sum() < MIN_OVERLAP:
            continue
        best = None
        for st in stacks:
            s, c, mg = _match(st["f"], st["v"], fr, vr)
            if s is None or c < MIN_CORR or mg < MIN_MARGIN:
                continue
            if best is None or c > best[2]:
                best = (st, s, c)
        if best is None:
            stacks.append(dict(members=[(r, 0, 1.0)], rgb=r["rgb"], q=np.asarray(r["q"], np.float32),
                               f=fr, v=vr))
            continue
        st, s, c = best
        st["members"].append((r, s, c))
        rgb, _, _, q = merge([(m, sh) for m, sh, _ in st["members"]], fill=st["members"][0][0]["rgb"])
        st["rgb"], st["q"] = rgb, q
        st["f"], st["v"] = _features(rgb, q)
    return stacks


def stack_tree(root: Path, log=print, min_members: int = 2):
    root = Path(root)
    recs = load_records(root, log)
    out = root / "stack"
    if out.exists():
        shutil.rmtree(out)
    if not recs:
        log("Стопки: нет картинок с качеством строк. Для старых записей нажми «Пересобрать записи».")
        return []
    stacks = [s for s in build(recs, log) if len(s["members"]) >= min_members]
    stacks.sort(key=lambda s: -len(s["members"]))
    if not stacks:
        log(f"Стопки: из {len(recs)} картинок повторов пока нет.")
        return []
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, st in enumerate(stacks, 1):
        p = out / f"stack_{i:02d}.png"
        save_png(st["rgb"], p)
        mem = [dict(path=m["path"], session=m["session"], rx=m["rx"], shift=int(sh), corr=round(c, 3))
               for m, sh, c in st["members"]]
        (out / f"stack_{i:02d}.json").write_text(json.dumps(dict(
            count=len(mem), sessions=len({m["session"] for m in mem}), members=mem,
            q=[round(float(v), 3) for v in st["q"]], lines=int((st["q"] > 0).sum())),
            ensure_ascii=False, indent=1), encoding="utf-8")
        saved.append(p)
    log(f"Стопки: из {len(recs)} картинок сложено {len(saved)}: "
        + ", ".join(f"№{i} — {len(s['members'])} {plural(len(s['members']), 'приём', 'приёма', 'приёмов')}"
                   for i, s in enumerate(stacks, 1)))
    return saved


if __name__ == "__main__":
    import sys
    stack_tree(Path(sys.argv[1] if len(sys.argv) > 1 else "sstv_out"))
