#!/usr/bin/env python3
"""Деконволюция замыленного кадра — попытка вернуть детали, а не усилить существующие.

Зачем отдельно от финиша: unsharp усиливает то, что есть. Если кадр замылен (расфокус,
мягкая оптика, пережатие), усиливать нечего — замер на c1_result.jpg дал резкость 21 на
входе и 65 после самого сильного финиша. Деконволюция решает обратную задачу: оценивает,
каким размытием кадр испортили, и обращает его. Детали при этом не выдумываются —
восстанавливается то, что было до размытия.

Два метода, оба на numpy+cv2, без новых зависимостей:
  * Wiener — одна операция в частотной области, быстро, но даёт «звон» на контрастных краях;
  * Richardson-Lucy — итеративный, медленнее, зато краям мягче и без звона.

    python3 scripts/deconv_test.py work/c1_result.jpg

Результаты — в work/tests/<имя>_deconv/.
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def gaussian_psf(radius: float, size: int | None = None) -> np.ndarray:
    """Ядро размытия. Гауссово приближение подходит и для расфокуса, и для мягкой оптики."""
    size = size or max(3, int(radius * 6) | 1)
    k = cv2.getGaussianKernel(size, radius)
    psf = k @ k.T
    return psf / psf.sum()


def wiener(img: np.ndarray, psf: np.ndarray, noise: float) -> np.ndarray:
    """Классический винеровский фильтр. noise = отношение шум/сигнал, чем больше, тем мягче."""
    out = np.zeros_like(img, dtype=np.float32)
    h, w = img.shape[:2]
    psf_pad = np.zeros((h, w), dtype=np.float32)
    ph, pw = psf.shape
    psf_pad[:ph, :pw] = psf
    # Центр ядра нужно свести к началу координат, иначе результат уедет по диагонали.
    psf_pad = np.roll(psf_pad, (-(ph // 2), -(pw // 2)), axis=(0, 1))
    H = np.fft.rfft2(psf_pad)
    denom = (H.conj() * H).real + noise

    for c in range(img.shape[2]):
        G = np.fft.rfft2(img[:, :, c].astype(np.float32))
        out[:, :, c] = np.fft.irfft2(G * H.conj() / denom, s=(h, w))
    return np.clip(out, 0, 255).astype(np.uint8)


def richardson_lucy(img: np.ndarray, psf: np.ndarray, iters: int) -> np.ndarray:
    """Итеративное восстановление. Каждый шаг сверяет свёртку оценки с исходником."""
    psf_mirror = psf[::-1, ::-1]
    out = np.zeros_like(img, dtype=np.float32)
    eps = 1e-6

    for c in range(img.shape[2]):
        observed = img[:, :, c].astype(np.float32) + eps
        estimate = observed.copy()
        for _ in range(iters):
            conv = cv2.filter2D(estimate, -1, psf, borderType=cv2.BORDER_REFLECT) + eps
            estimate *= cv2.filter2D(observed / conv, -1, psf_mirror, borderType=cv2.BORDER_REFLECT)
            np.clip(estimate, 0, 255, out=estimate)
        out[:, :, c] = estimate
    return np.clip(out, 0, 255).astype(np.uint8)


def sharpness(img, faces) -> tuple[float, float]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    whole = cv2.Laplacian(gray, cv2.CV_64F).var()
    vals = [cv2.Laplacian(gray[y:y + h, x:x + w], cv2.CV_64F).var() for (x, y, w, h) in faces]
    return whole, (float(np.mean(vals)) if vals else 0.0)


def main() -> int:
    p = argparse.ArgumentParser(description="Матрица деконволюции по одному кадру")
    p.add_argument("image", type=Path)
    p.add_argument("--radius", type=float, nargs="+", default=[1.0, 1.6, 2.4],
                   help="предполагаемый радиус размытия в пикселях")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    out_dir = ROOT / "work" / "tests" / f"{args.image.stem}_deconv"
    out_dir.mkdir(parents=True, exist_ok=True)

    src = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    faces = cv2.CascadeClassifier(CASCADE).detectMultiScale(
        cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(40, 40)
    )
    cv2.imwrite(str(out_dir / "00_input.jpg"), src, [cv2.IMWRITE_JPEG_QUALITY, 96])

    w0, f0 = sharpness(src, faces)
    lines = [f"кадр: {args.image.name} — {src.shape[1]}x{src.shape[0]}, лиц {len(faces)}", ""]
    lines.append(f"{'вариант':28} {'резкость':>9} {'лица':>8} {'время':>7}")
    lines.append(f"{'00_input':28} {w0:9.0f} {f0:8.0f} {'—':>7}")

    for r in args.radius:
        psf = gaussian_psf(r)
        for noise in (0.01, 0.03):
            t = time.time()
            res = wiener(src, psf, noise)
            dt = time.time() - t
            name = f"wiener_r{r}_n{noise}"
            cv2.imwrite(str(out_dir / f"{name}.jpg"), res, [cv2.IMWRITE_JPEG_QUALITY, 96])
            wv, fv = sharpness(res, faces)
            lines.append(f"{name:28} {wv:9.0f} {fv:8.0f} {dt:6.1f}с")

        for iters in (8, 20):
            t = time.time()
            res = richardson_lucy(src, psf, iters)
            dt = time.time() - t
            name = f"rl_r{r}_i{iters}"
            cv2.imwrite(str(out_dir / f"{name}.jpg"), res, [cv2.IMWRITE_JPEG_QUALITY, 96])
            wv, fv = sharpness(res, faces)
            lines.append(f"{name:28} {wv:9.0f} {fv:8.0f} {dt:6.1f}с")

    (out_dir / "metrics.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nрезультаты: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
