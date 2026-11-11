"""Декодер SSTV Robot 36, устойчивый к шуму и «плавающей» скорости звука.

Звук со страницы WebSDR идёт с переменной скоростью: клиент сайта подгоняет
воспроизведение под свой буфер, и период строки уходит на ~1% и больше.
Поэтому декодер не полагается на точный тайминг:
  1. считает мгновенную частоту (полоса 1000–2500 Гц, аналитический сигнал);
  2. ищет синхроимпульсы 1200 Гц как пики «похожести на синхро» за 9 мс;
  3. связывает их в цепочки с шагом ≈ строке (допуск ±3%, период
     подстраивается по ходу);
  4. рисует каждую строку по её собственным соседним синхро.
Провалы звука (нули при перестройке частоты) не ломают цепочку: пропавшие
строки повторяют предыдущие.

decode_audio(x, fs)  — разобрать массив целиком (для готовых WAV);
LiveDecoder          — то же самое на лету, по мере поступления звука.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.signal import butter, find_peaks, firwin, hilbert, lfilter, medfilt, sosfiltfilt

FS = 12000                      # рабочая частота: исходник у WebSDR всё равно 8 кГц
LINE = 0.150                    # номинальная длина строки Robot 36, с
SYNC, PORCH, Y_LEN, SEP, PORCH2, C_LEN = 0.009, 0.003, 0.088, 0.0045, 0.0015, 0.044
LINES, WIDTH = 240, 320
P_MIN, P_MAX = LINE * 0.97, LINE * 1.03


# ---------------- низкий уровень ----------------
PRE_EMPH = 0.8


class FreqTrack(np.ndarray):
    """Массив мгновенной частоты, который несёт с собой «фазовые приращения».

    prod[n] = z[n]·conj(z[n-1]) для аналитического сигнала z. Сумма prod по
    окну и угол суммы — оценка частоты, взвешенная амплитудой: щелчки FM
    (они случаются при провалах амплитуды) почти ничего не весят.
    """
    prod: np.ndarray | None = None


def inst_freq(x: np.ndarray, fs: int = FS) -> tuple[np.ndarray, np.ndarray]:
    """Мгновенная частота (Гц) и маска «звук есть»."""
    x = np.asarray(x, np.float64)
    if len(x) < 256:
        return np.zeros(len(x), np.float32), np.zeros(len(x), bool)
    # Приёмники заваливают верхние частоты звука (де-эмфазис), из-за этого шум
    # тянет оценку частоты вниз и шумные места окрашиваются в зелёный.
    # Лёгкий подъём верхов выравнивает шум; на чистый тон он не влияет.
    x = np.concatenate([[x[0]], x[1:] - PRE_EMPH * x[:-1]])
    sos = butter(4, [1000, 2500], btype="bandpass", fs=fs, output="sos")
    bp = sosfiltfilt(sos, x)
    an = hilbert(bp)
    prod = np.empty(len(an), np.complex64)
    prod[1:] = an[1:] * np.conj(an[:-1])
    prod[0] = prod[1] if len(an) > 1 else 0
    ph = np.unwrap(np.angle(an))
    f = np.diff(ph, prepend=ph[0]) * fs / (2 * math.pi)
    f = medfilt(f, 5)
    k = max(1, int(0.0004 * fs))
    f = np.convolve(f, np.ones(k) / k, mode="same")
    # провалы: участки, где звук практически нулевой
    env = np.convolve(np.abs(x), np.ones(int(0.004 * fs)) / int(0.004 * fs), mode="same")
    ok = env > 1e-4
    ft = np.clip(f, 900, 2600).astype(np.float32).view(FreqTrack)
    ft.prod = prod
    return ft, ok


def sync_score(f: np.ndarray, ok: np.ndarray, fs: int = FS) -> np.ndarray:
    """Насколько окно 9 мс, начинающееся в точке, похоже на синхро 1200 Гц (≈1 — похоже)."""
    c = np.clip((1500.0 - f) / 300.0, -1.0, 1.0)
    c[~ok] = -1.0
    n = int(SYNC * fs)
    s = np.convolve(c, np.ones(n) / n, mode="full")[n - 1:]
    return s[: len(f)].astype(np.float32)


def comb_score(s: np.ndarray, fs: int = FS, k: int = 3, steps: int = 13) -> np.ndarray:
    """«Похожесть на синхро», накопленная по 2k+1 соседним строкам.

    Синхро повторяется каждую строку, шум — нет. Сумма по соседним строкам
    поднимает настоящие синхро над шумом. Период перебирается в пределах
    ±3% (скорость звука у приёмников «плывёт»), берётся лучший.
    """
    n = len(s)
    best = np.full(n, -1.0, np.float32)
    sp = np.clip(s, -0.5, 1.0)
    for P in np.linspace(P_MIN * fs, P_MAX * fs, steps):
        acc = np.zeros(n, np.float32)
        cnt = np.zeros(n, np.float32)
        for j in range(-k, k + 1):
            sh = int(round(j * P))
            if sh >= 0:
                acc[:n - sh] += sp[sh:]
                cnt[:n - sh] += 1
            else:
                acc[-sh:] += sp[:n + sh]
                cnt[-sh:] += 1
        np.maximum(best, acc / np.maximum(cnt, 1), out=best)
    return best


def find_chains(f, ok, fs: int = FS, min_lines: int = 15):
    """Синхроимпульсы → цепочки {номер строки: время отсчёта}."""
    s = sync_score(f, ok, fs)
    pk, pr = find_peaks(s, height=0.45, distance=int(0.09 * fs))
    # Несколько гипотез сразу: каждый пик, не подошедший ни к одной открытой
    # цепочке, начинает свою. Так ложный пик (бит VIS, щелчок) не может
    # «увести» настоящую цепочку — он просто останется короткой гипотезой.
    done, open_ = [], []
    for t in pk:
        still = []
        for c in open_:
            (still if t - c["last_t"] <= 8.5 * c["P"] else done).append(c)
        open_ = still
        attached = False
        for c in open_:
            d = t - c["last_t"]
            m = round(d / c["P"])
            tol = (0.0025 + 0.0007 * m) * fs
            if 1 <= m <= 8 and abs(d - m * c["P"]) < tol:
                n = c["last_n"] + m
                c["lines"][n] = int(t)
                if m == 1:
                    c["P"] = min(max(0.85 * c["P"] + 0.15 * d, P_MIN * fs), P_MAX * fs)
                c["last_t"], c["last_n"] = t, n
                attached = True
        if not attached:
            open_.append({"lines": {0: int(t)}, "P": LINE * fs, "last_t": t, "last_n": 0})
    done += open_
    # из пересекающихся по времени гипотез оставляем самые длинные
    done.sort(key=lambda c: -len(c["lines"]))
    taken, chains = [], []
    for c in done:
        if len(c["lines"]) < 4:
            continue
        a, b = min(c["lines"].values()), max(c["lines"].values())
        if any(a < tb - 0.5 * fs and b > ta + 0.5 * fs for ta, tb in taken):
            continue
        taken.append((a, b))
        chains.append(c)
    chains.sort(key=lambda c: min(c["lines"].values()))

    # склейка кусков одной картинки: провал звука при перестройке частоты
    # выкидывает часть отсчётов и рвёт цепочку, но картинка продолжается
    chains = [c for c in chains if len(c["lines"]) >= 4]
    merged = []
    for c in chains:
        if merged:
            prev = merged[-1]
            pend_n = max(prev["lines"])
            pend_t = prev["lines"][pend_n]
            c0_n = min(c["lines"])
            c0_t = c["lines"][c0_n]
            gap = c0_t - pend_t
            span = (pend_n - min(prev["lines"])) + (max(c["lines"]) - c0_n)
            if 0 < gap < 10 * fs and span < LINES + 5:
                shift = pend_n + max(1, round(gap / prev["P"])) - c0_n
                for n, t in c["lines"].items():
                    prev["lines"][n + shift] = t
                prev["P"] = c["P"]
                prev.setdefault("breaks", []).append(pend_n + 1)
                continue
        merged.append(c)
    chains = merged

    out = []
    for c in chains:
        if len(c["lines"]) < min_lines:
            continue
        _extend(c, s, fs)
        # уточняем стартовый период: медиана соседних интервалов
        n = np.array(sorted(c["lines"]))
        t = np.array([c["lines"][k] for k in n], float)
        steps = np.diff(t) / np.diff(n)
        good = (steps > P_MIN * fs) & (steps < P_MAX * fs)
        if good.sum() < min_lines // 2:
            continue
        out.append({"lines": dict(zip(n.tolist(), t.tolist())),
                    "P": float(np.median(steps[good]))})
    return out


def _extend(c, s, fs, max_miss: int = 15):
    """Продлить цепочку вперёд и назад через слабый участок.

    Идём по известному периоду и в узком окне вокруг ожидаемого места
    ищем синхро с пониженным порогом. Если долго ничего нет, останавливаемся.
    """
    for direction in (1, -1):
        n = max(c["lines"]) if direction > 0 else min(c["lines"])
        t = c["lines"][n]
        P = c["P"]
        miss = 0
        while miss < max_miss:
            span = max(c["lines"]) - min(c["lines"]) + 1
            if span >= LINES:
                break
            n += direction
            t_pred = t + direction * P * (miss + 1)
            w = int((0.002 + 0.0004 * miss) * fs)
            a, b = int(t_pred) - w, int(t_pred) + w + 1
            if a < 0 or b >= len(s):
                break
            j = a + int(np.argmax(s[a:b]))
            if s[j] > 0.3:
                c["lines"][n] = j
                if miss == 0:
                    P = 0.95 * P + 0.05 * abs(j - t)
                t, miss = j, 0
            else:
                miss += 1


def quality(rgb, first, nmax) -> float:
    """Похожесть соседних строк по яркости: у картинки ~0.3–0.9, у шума ~0.1."""
    Y = rgb[first:nmax].astype(np.float32).mean(2)
    if len(Y) < 3:
        return 0.0
    a, b = Y[:-1] - Y[:-1].mean(1, keepdims=True), Y[1:] - Y[1:].mean(1, keepdims=True)
    num = (a * b).sum(1)
    den = np.sqrt((a * a).sum(1) * (b * b).sum(1)) + 1e-9
    return float(np.mean(num / den))


def anchor_of(f, ok, ch, lo: int, data_end: int, fs: int = FS) -> str:
    """Куда прижать кусок: 'start' — это начало картинки, 'end' — её конец.

    Есть ведущий тон перед первой строкой → начало. Иначе, если после
    последней строки сигнал кончился, а запись идёт дальше → это конец
    картинки (передача Robot 36 обрывается ровно на 240-й строке).
    """
    t_first = min(ch["lines"].values())
    t_last = max(ch["lines"].values())
    le = leader_end(f, ok, int(t_first - lo), fs)
    if le is None:
        # первая точка цепочки могла оказаться старт-битом VIS — ищем тон и перед второй
        n_sorted = sorted(ch["lines"])
        if len(n_sorted) > 2:
            le = leader_end(f, ok, int(ch["lines"][n_sorted[1]] - lo), fs)
    if le is not None:
        line0 = le + lo + 0.300 * fs                 # после тона идёт VIS 300 мс
        pts = {n: t for n, t in ch["lines"].items() if t >= line0 - 0.004 * fs}
        if len(pts) >= 2:
            n_first = min(pts)
            k0 = max(0, round((pts[n_first] - line0) / ch["P"]))
            ch["lines"] = {n - n_first + k0: t for n, t in pts.items()}
            ch["lead"] = True
            return "start"
    nlines = max(ch["lines"]) - min(ch["lines"]) + 1
    if nlines < LINES - 2 and data_end - t_last > 1.5 * fs:
        return "end"
    return "start"


def leader_end(f, ok, t0: int, fs: int = FS):
    """Если перед t0 есть ведущий тон 1900 Гц, вернуть отсчёт его конца, иначе None.

    Тон ищется по доле отсчётов около 1900 Гц в окне 0.2 с — так шумовые
    щелчки не рвут его, как при поиске непрерывного участка.
    """
    a = max(0, t0 - int(1.3 * fs))
    seg = f[a:t0]
    if len(seg) < int(0.3 * fs):
        return None
    inb = ((np.abs(seg - 1900) < 150) & ok[a:t0]).astype(np.float32)
    k = int(0.2 * fs)
    fr = np.convolve(inb, np.ones(k) / k, mode="valid")
    if fr.max() < 0.6:
        return None
    good = np.flatnonzero(fr >= 0.6)
    return a + int(good[-1]) + k        # конец последнего окна с тоном


def has_leader(f, ok, t0: int, fs: int = FS) -> bool:
    return leader_end(f, ok, t0, fs) is not None


def render(f, ok, chain, fs: int = FS, anchor: str = "start"):
    """Нарисовать картинку по цепочке синхро. Возвращает (RGB, число строк с данными)."""
    lines = chain["lines"]
    if anchor == "end":
        n0 = max(lines) - (LINES - 1)
    elif chain.get("lead"):
        n0 = 0
    else:
        n0 = min(lines)
    idx = {n - n0: t for n, t in lines.items() if 0 <= n - n0 < LINES}
    first = min(idx)
    nmax = min(LINES, max(idx) + 1)
    known = np.array(sorted(idx))
    kt = np.array([idx[k] for k in known], float)
    # время начала каждой строки: интерполяция/экстраполяция по известным синхро
    allk = np.arange(nmax)
    if len(known) >= 2:
        starts = np.interp(allk, known, kt)
        P = chain["P"]
        starts[allk < known[0]] = kt[0] - (known[0] - allk[allk < known[0]]) * P
        starts[allk > known[-1]] = kt[-1] + (allk[allk > known[-1]] - known[-1]) * P
    else:
        starts = kt[0] + allk * chain["P"]
    nxt = np.append(starts[1:], starts[-1] + chain["P"])
    scale = np.clip((nxt - starts) / (LINE * fs), 0.97, 1.03)

    prod = getattr(f, "prod", None)
    if prod is not None and len(prod) == len(f):
        cp = np.concatenate([[0], np.cumsum(prod.astype(np.complex128))])
        ca = np.concatenate([[0.0], np.cumsum(np.abs(prod).astype(np.float64))])
    else:
        cp = ca = None
    coh_out = {}
    cs = np.concatenate([[0.0], np.cumsum(np.asarray(f), dtype=np.float64)])
    co = np.concatenate([[0], np.cumsum(ok, dtype=np.int64)])

    def seg(start, length, n):
        e = start + np.arange(n + 1) * (length * fs / n)
        e = np.clip(e.round().astype(int), 0, len(f))
        a, b = e[:-1], np.maximum(e[1:], e[:-1] + 1)
        b = np.minimum(b, len(f))
        a = np.minimum(a, b - 1)
        if cp is not None:
            # частота по сумме фазовых приращений (устойчиво к щелчкам)
            z = cp[b] - cp[a]
            mean = np.angle(z) * fs / (2 * math.pi)
            mean = np.clip(mean, 900, 2600)
            # «чистота тона»: 1 — ровный тон, около 0 — шум
            coh_out["last"] = np.abs(z) / np.maximum(ca[b] - ca[a], 1e-12)
        else:
            mean = (cs[b] - cs[a]) / (b - a)
        valid = (co[b] - co[a]) >= 0.6 * (b - a)
        return mean, valid

    def val(fr):
        return np.clip((fr - 1500.0) / 800.0 * 255.0, 0, 255)

    Y = np.zeros((LINES, WIDTH), np.float32)
    CH = np.full((LINES, WIDTH), 128.0, np.float32)
    sep = np.zeros(LINES)
    good = np.zeros(LINES, bool)
    line_q = np.zeros(LINES, np.float32)
    for n in range(first, nmax):
        k = scale[n]
        y0 = starts[n] + (SYNC + PORCH) * fs * k
        y, vy = seg(y0, Y_LEN * k, WIDTH)
        sp, _ = seg(y0 + Y_LEN * fs * k, SEP * k, 1)
        c, vc = seg(y0 + (Y_LEN + SEP + PORCH2) * fs * k, C_LEN * k, WIDTH // 2)
        if "last" in coh_out:
            # шумный цвет не выдумываем: тянем к серому пропорционально «грязности»
            w = np.clip((coh_out["last"] - 0.35) / 0.4, 0.0, 1.0)
            c = 1900.0 + w * (c - 1900.0)
        sep[n] = sp[0]
        good[n] = vy.mean() > 0.7
        a0 = int(starts[n])
        sw = f[max(0, a0):max(0, a0) + int(SYNC * fs)]
        lq = float(np.clip((1500.0 - sw) / 300.0, -1, 1).mean()) if len(sw) else 0.0
        line_q[n] = max(0.0, lq) * (1.0 if n in idx else 0.5) * (1.0 if good[n] else 0.0)
        Y[n] = val(y)
        CH[n] = np.repeat(val(c), 2)
        if not good[n] and n > first:      # провал звука — повторяем строку выше
            Y[n], CH[n] = Y[n - 1], CH[n - 2] if n > 1 else CH[n - 1]

    # чётность: у строк B-Y разделитель 2300 Гц, у R-Y — 1500 Гц
    ev, od = sep[first:nmax][(np.arange(first, nmax) % 2) == 0], sep[first:nmax][(np.arange(first, nmax) % 2) == 1]
    by_odd = (np.median(od) if len(od) else 0) >= (np.median(ev) if len(ev) else 0)
    RY = np.full_like(Y, 128.0)
    BY = np.full_like(Y, 128.0)
    for n in range(first - first % 2, nmax, 2):
        pair = [m for m in (n, n + 1) if first <= m < nmax]
        for m in pair:
            is_by = (m % 2 == 1) == by_odd
            for q in pair:
                (BY if is_by else RY)[q] = CH[m]
    chain["raw"] = _to_rgb(Y, RY, BY, first, nmax)
    if DENOISE:
        Y, RY, BY = denoise(Y, RY, BY, first, nmax)
    rgb = _to_rgb(Y, RY, BY, first, nmax)
    chain["quality"] = quality(rgb, first, nmax)
    chain["line_q"] = line_q
    chain["starts"] = np.asarray(starts, float)
    return rgb, int(good[first:nmax].sum())


DENOISE = True


def _to_rgb(Y, RY, BY, first, nmax):
    cr, cb = RY - 128.0, BY - 128.0
    rgb = np.stack([Y + 1.402 * cr, Y - 0.344136 * cb - 0.714136 * cr, Y + 1.772 * cb], -1)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    rgb[nmax:] = 0
    rgb[:first] = 0
    return rgb


def denoise(Y, RY, BY, first, nmax):
    """Шумоподавление под шум FM.

    Цвет: в Robot 36 он и так вдвое грубее яркости, поэтому его можно
    сглаживать сильно (медиана 3x5, затем мягкое размытие) — на глаз
    это почти не теряет деталей, зато убирает цветную рябь.
    Яркость: выбиваем только одиночные «уколы» — пиксели, которые сильно
    отличаются от медианы соседей; края и мелкие детали остаются.
    """
    from scipy.ndimage import gaussian_filter, median_filter
    sl = slice(first, nmax)
    Y, RY, BY = Y.copy(), RY.copy(), BY.copy()
    for C in (RY, BY):
        c = median_filter(C[sl], size=(3, 5), mode="nearest")
        C[sl] = gaussian_filter(c, sigma=(0.8, 1.5), mode="nearest")
    y = Y[sl]
    med = median_filter(y, size=3, mode="nearest")
    spread = median_filter(np.abs(y - med), size=5, mode="nearest")
    spike = np.abs(y - med) > np.maximum(25.0, 3.0 * spread)
    y = np.where(spike, med, y)
    Y[sl] = 0.75 * y + 0.25 * gaussian_filter(y, sigma=0.7, mode="nearest")
    return Y, RY, BY


def save_png(rgb, path: Path):
    Image.fromarray(rgb).resize((WIDTH * 2, LINES * 2), Image.NEAREST).save(path)


def save_meta(path: Path, q, start: bool, w0, lines: int, quality: float | None = None):
    """Рядом с картинкой — качество каждой строки: по нему склейка и стопки решают,
    какой строке сколько доверять."""
    import json
    try:
        Path(path).with_suffix(".json").write_text(json.dumps(dict(
            q=[round(float(v), 3) for v in q], start=bool(start), w0=w0, lines=int(lines),
            quality=None if quality is None else round(float(quality), 3))), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def load_meta(path: Path):
    import json
    try:
        d = json.loads(Path(path).with_suffix(".json").read_text(encoding="utf-8"))
        d["q"] = np.asarray(d["q"], np.float32)
        return d if len(d["q"]) == LINES else None
    except Exception:  # noqa: BLE001
        return None


def to_fs(x: np.ndarray, fs_in: int) -> np.ndarray:
    from scipy.signal import resample_poly
    if fs_in == FS:
        return np.asarray(x, np.float32)
    g = math.gcd(FS, fs_in)
    return resample_poly(np.asarray(x, np.float64), FS // g, fs_in // g).astype(np.float32)


def decode_audio(x: np.ndarray, fs_in: int, min_quality: float = 0.17):
    """Найти и нарисовать все картинки в записи. Возвращает список словарей."""
    x = to_fs(x, fs_in)
    f, ok = inst_freq(x)
    out = []
    for ch in find_chains(f, ok):
        anc = anchor_of(f, ok, ch, 0, len(x))
        rgb, ng = render(f, ok, ch, anchor=anc)
        if ch["quality"] < min_quality:
            continue
        t_first = min(ch["lines"].values())
        out.append(dict(rgb=rgb, lines=ng, t=t_first / FS, P=ch["P"] / FS, q=ch["quality"],
                        start=bool(ch.get("lead")), anchor=anc))
    return out


def decode_wav(path, outdir=None, log=print):
    from scipy.io import wavfile
    fs, data = wavfile.read(str(path))
    data = data.astype(np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data /= max(1.0, float(np.abs(data).max()))
    outdir = Path(outdir or Path(path).with_suffix(""))
    outdir.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, r in enumerate(decode_audio(data, fs), 1):
        p = outdir / f"{Path(path).stem}_{i:02d}.png"
        save_png(r["rgb"], p)
        saved.append(p)
        log(f"{p.name}: {r['lines']} строк, с {r['t']:.0f} с, качество {r['q']:.2f}, "
            f"скорость {(r['P'] / LINE - 1) * 100:+.2f}%"
            f"{'' if r['start'] else ', начало пропущено (кусок прижат к ' + ('низу' if r['anchor'] == 'end' else 'верху') + ')'}")
    return saved


# ---------------- потоковый режим ----------------
class LiveDecoder:
    """Копит звук и каждые пару секунд ищет картинки в последних ~60 с."""

    WINDOW = 60.0

    def __init__(self, fs_in: int, outdir: Path, prefix: str = "iss", log=print, on_image=None):
        self.fs_in = fs_in
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.prefix, self.log, self.on_image = prefix, log, on_image
        self.buf = np.zeros(1 << 20, np.float32)
        self.n = 0
        self.ratio = FS / fs_in
        self._lp = firwin(63, 4500, fs=fs_in) if fs_in != FS else None
        self._zi = np.zeros(62) if self._lp is not None else None
        self._phase = 0.0
        self.emitted_until = -1        # абсолютный отсчёт конца последней сохранённой картинки
        self.count = 0
        self.saved: list[Path] = []
        self.records: list[dict] = []     # для склейки с другими приёмниками
        self._wall_n: list[int] = []
        self._wall_t: list[float] = []
        self.progress: int | None = None
        self.last_poll = 0
        self.partial = None            # картинка, которая принимается сейчас (RGB 240×320)
        self.partial_ver = 0
        self.partial_rec = None
        self.last_rgb = None           # последняя готовая картинка

    def _append(self, y):
        need = self.n + len(y)
        if need > len(self.buf):
            nb = np.zeros(max(need, 2 * len(self.buf)), np.float32)
            nb[: self.n] = self.buf[: self.n]
            self.buf = nb
        self.buf[self.n:need] = y
        self.n = need

    def wall(self, n):
        """Время на часах (time.time()) для отсчёта n рабочей частоты."""
        if not self._wall_n:
            return None
        return float(np.interp(n, self._wall_n, self._wall_t))

    def feed(self, samples, wall_time: float | None = None):
        import time as _t
        self._mark_wall = wall_time if wall_time is not None else _t.time()
        x = np.asarray(samples, np.float64)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if self._lp is None:
            self._append(x.astype(np.float32))
            self._wall_n.append(self.n)
            self._wall_t.append(self._mark_wall)
            return
        y, self._zi = lfilter(self._lp, 1.0, x, zi=self._zi)
        step = 1.0 / self.ratio
        pos = np.arange(self._phase, len(y), step)
        self._phase = pos[-1] + step - len(y) if len(pos) else self._phase - len(y)
        self._append(np.interp(pos, np.arange(len(y)), y).astype(np.float32))
        self._wall_n.append(self.n)
        self._wall_t.append(self._mark_wall)

    def poll(self, final: bool = False):
        if not final and self.n - self.last_poll < 2 * FS:
            return
        self.last_poll = self.n
        lo = max(0, self.n - int(self.WINDOW * FS))
        seg = self.buf[lo:self.n]
        f, ok = inst_freq(seg)
        self.progress = None
        for ch in find_chains(f, ok):
            t_first = min(ch["lines"].values()) + lo
            t_last = max(ch["lines"].values()) + lo
            if t_first < self.emitted_until - 1.0 * FS:
                continue    # эта картинка (или её кусок) уже сохранена
            nlines = max(ch["lines"]) - min(ch["lines"]) + 1
            ended = (self.n - t_last > 2.0 * FS) or nlines >= LINES or final
            if not ended:
                self.progress = min(LINES, nlines)
                try:
                    anc = anchor_of(f, ok, ch, 0, len(seg))
                    self.partial, ng = render(f, ok, ch, anchor=anc)
                    self.partial_ver += 1
                    st = ch["starts"]
                    # кусок, который принимается сейчас, — для склейки прямо во время пролёта
                    self.partial_rec = dict(rgb=self.partial, q=ch["line_q"].copy(),
                                            w0=self.wall(st[0] + lo) if len(st) else None,
                                            start=bool(ch.get("lead")), anchor=anc, lines=ng)
                except Exception:  # noqa: BLE001
                    pass
                continue
            anc = anchor_of(f, ok, ch, 0, len(seg))
            rgb, ng = render(f, ok, ch, anchor=anc)
            self.emitted_until = t_last
            if ng < 15 or ch["quality"] < 0.17:
                continue
            self.count += 1
            self.last_rgb, self.partial, self.partial_rec = rgb, None, None
            self.partial_ver += 1
            self.last_meta = dict(lines=ng, q=float(ch["quality"]), start=bool(ch.get("lead")),
                                  w0=self.wall(ch["starts"][0] + lo) if len(ch["starts"]) else None)
            p = self.outdir / f"{self.prefix}_{self.count:02d}.png"
            save_png(rgb, p)
            self.saved.append(p)
            st = ch["starts"]
            save_meta(p, ch["line_q"], bool(ch.get("lead")),
                      self.wall(st[0] + lo) if len(st) else None, ng, ch["quality"])
            self.records.append(dict(path=p, rgb=rgb, q=ch["line_q"].copy(),
                                     w0=self.wall(st[0] + lo) if len(st) else None,
                                     start=bool(ch.get("lead")), anchor=anc, lines=ng))
            start = bool(ch.get("lead"))
            self.log(f"Картинка сохранена: {p.name} ({ng} строк, скорость звука "
                     f"{(ch['P'] / FS / LINE - 1) * 100:+.2f}%{'' if start else ', начало пропущено'})")
            if self.on_image:
                self.on_image(p)

    def strength(self) -> float:
        """Качество сигнала SSTV по последним 3 с, 0…100 %."""
        n = int(3.5 * FS)
        if self.n < n:
            return 0.0
        f, ok = inst_freq(self.buf[self.n - n:self.n])
        v = sync_strength(f, ok)
        return float(np.clip((v + 0.1) / 0.95, 0, 1) * 100)

    def finish(self):
        self.poll(final=True)


# ---------------- индикатор качества сигнала ----------------
def sync_strength(f, ok, fs: int = FS, seconds: float = 3.0) -> float:
    """Насколько уверенно видны синхроимпульсы Robot 36 в последних секундах (0…1).

    Ищет лучшую «гребёнку» с шагом строки (±3%) по максимумам «похожести на
    синхро» в окнах ±2.5 мс; среднее по ~20 строкам. Шум даёт ≈0.2–0.35,
    чистая картинка — ≈0.9.
    """
    from scipy.ndimage import maximum_filter1d
    n = int(seconds * fs)
    if len(f) < n:
        return 0.0
    s = sync_score(f[-n:], ok[-n:], fs)
    d = max(1, fs // 1000)                       # шаг 1 мс
    sm = maximum_filter1d(s, size=int(0.005 * fs))[::d]
    L = len(sm)
    k = np.arange(int(seconds / P_MAX) - 1)
    best = -1.0
    for P in np.linspace(P_MIN * 1000, P_MAX * 1000, 15):
        ph = np.arange(int(P))
        pos = (ph[:, None] + np.round(k[None, :] * P)).astype(int)
        pos = pos[pos[:, -1] < L] if pos.size else pos
        if not len(pos):
            continue
        v = float(sm[pos].mean(1).max())
        best = max(best, v)
    return best
