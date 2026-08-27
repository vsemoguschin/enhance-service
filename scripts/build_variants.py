#!/usr/bin/env python3
"""Раскладывает по папке на кадр все варианты обработки — для выбора глазами.

Один кадр = одна папка = оригинал плюс пронумерованные варианты отдельными файлами.
Склеенных сравнений не делаем: их неудобно листать и нельзя открыть на весь экран.

    python3 scripts/build_variants.py work/image.png --name 1-застолье

Методы делятся на две группы по тому, что они физически могут:
  * усиливающие (финиш, detailEnhance) — делают читаемее то, что уже есть в пикселях;
  * восстанавливающие (деконволюция) — обращают размытие, возвращая утраченное.
Плюс Retinex — он не про детали, а про свет: вытягивает лица из тени.

Ни один из них не обращается к сети и не тратит лимиты.
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deconv_test import gaussian_psf, richardson_lucy, wiener  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def retinex(img: np.ndarray, sigmas=(15, 80, 250)) -> np.ndarray:
    """Multi-Scale Retinex: выравнивает освещение, вытягивает тени, не выбивая света.

    Работает по яркости, чтобы не поехал цвет. Полезен на контровом свете и вечерних
    кадрах, где лица уходят в тень, — детализации не добавляет.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lf = l.astype(np.float32) + 1.0
    acc = np.zeros_like(lf)
    for s in sigmas:
        acc += np.log(lf) - np.log(cv2.GaussianBlur(lf, (0, 0), s) + 1.0)
    acc /= len(sigmas)
    acc = cv2.normalize(acc, None, 0, 255, cv2.NORM_MINMAX)
    # Смешиваем с исходной яркостью: чистый Retinex выглядит неестественно плоским.
    mixed = cv2.addWeighted(l, 0.6, acc.astype(np.uint8), 0.4, 0)
    return cv2.cvtColor(cv2.merge((mixed, a, b)), cv2.COLOR_LAB2BGR)


def measure(img, faces):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    a, b = lab[:, :, 1] - 128, lab[:, :, 2] - 128
    fs = [cv2.Laplacian(gray[y:y + h, x:x + w], cv2.CV_64F).var() for (x, y, w, h) in faces]
    return (cv2.Laplacian(gray, cv2.CV_64F).var(),
            float(np.mean(fs)) if fs else 0.0,
            lab[:, :, 0].mean(),
            float(np.sqrt(a * a + b * b).mean()))


def main() -> int:
    p = argparse.ArgumentParser(description="Все варианты обработки одного кадра")
    p.add_argument("image", type=Path)
    p.add_argument("--name", required=True, help="имя папки в work/")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    out = ROOT / "work" / args.name
    out.mkdir(parents=True, exist_ok=True)

    src = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    faces = cv2.CascadeClassifier(CASCADE).detectMultiScale(
        cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(40, 40)
    )
    cv2.imwrite(str(out / f"00-оригинал{args.image.suffix}"), src,
                [cv2.IMWRITE_JPEG_QUALITY, 98] if args.image.suffix != ".png" else [])

    from app import finish  # noqa: PLC0415
    finish.settings.finish_luma_only = True

    F = lambda im, **kw: finish.detail_finish(im.copy(), **kw)  # noqa: E731
    psf10, psf16, psf24 = gaussian_psf(1.0), gaussian_psf(1.6), gaussian_psf(2.4)

    variants = [
        ("01-финиш-мягко", lambda: F(src, amount=0.45, micro_contrast=0.0, sharpen=0.30)),
        ("02-финиш-средне", lambda: F(src, amount=0.60, micro_contrast=0.0, sharpen=0.45)),
        ("03-финиш-сильно", lambda: F(src, amount=0.90, micro_contrast=0.0, sharpen=0.70)),
        ("04-финиш-с-контрастом", lambda: F(src, amount=0.60, micro_contrast=0.6, sharpen=0.45)),
        ("05-деконв-мягкая", lambda: wiener(src, psf10, 0.03)),
        ("06-деконв-средняя", lambda: wiener(src, psf16, 0.03)),
        ("07-деконв-сильная", lambda: wiener(src, psf16, 0.01)),
        ("08-деконв-итеративная", lambda: richardson_lucy(src, psf16, 20)),
        ("09-деконв-плюс-финиш", lambda: F(wiener(src, psf16, 0.03), amount=0.5, micro_contrast=0.0, sharpen=0.35)),
        ("10-деконв-денойз-финиш", lambda: F(
            cv2.fastNlMeansDenoisingColored(wiener(src, psf16, 0.03), None, 4, 4, 7, 21),
            amount=0.5, micro_contrast=0.0, sharpen=0.35)),
        ("11-денойз-плюс-финиш", lambda: F(
            cv2.fastNlMeansDenoisingColored(src, None, 4, 4, 7, 21),
            amount=0.6, micro_contrast=0.0, sharpen=0.45)),
        ("12-свет-Retinex", lambda: retinex(src)),
        ("13-свет-плюс-финиш", lambda: F(retinex(src), amount=0.5, micro_contrast=0.0, sharpen=0.35)),
        ("14-детали-OpenCV", lambda: cv2.detailEnhance(src, sigma_s=10, sigma_r=0.15)),
        ("15-деконв-сильная-плюс-денойз", lambda: cv2.fastNlMeansDenoisingColored(
            wiener(src, psf24, 0.01), None, 5, 5, 7, 21)),
    ]

    w0, f0, y0, c0 = measure(src, faces)
    lines = [f"кадр: {args.image.name} — {src.shape[1]}x{src.shape[0]} = "
             f"{src.shape[1] * src.shape[0] / 1e6:.2f} Мп, лиц {len(faces)}", "",
             f"{'вариант':32} {'резкость':>9} {'лица':>8} {'яркость':>8} {'цвет':>7} {'время':>7}",
             f"{'00-оригинал':32} {w0:9.0f} {f0:8.0f} {y0:8.1f} {c0:7.2f} {'—':>7}"]

    for name, fn in variants:
        t = time.time()
        img = fn()
        dt = time.time() - t
        cv2.imwrite(str(out / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 96])
        w, f, y, c = measure(img, faces)
        lines.append(f"{name:32} {w:9.0f} {f:8.0f} {y:8.1f} {c:7.2f} {dt:6.1f}с")
        print(lines[-1])

    (out / "metrics.txt").write_text("\n".join(lines) + "\n")
    print(f"\nготово: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
