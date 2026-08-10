#!/usr/bin/env python3
"""Подгонка пропорции кадра: pad до отправки в модель, crop после.

Зачем: у Seedream пропорция выхода выбирается из фиксированного списка (`classic_4_3` и др.).
Если у кадра своя пропорция, модель растягивает изображение под пресет — лица становятся шире.
Лечится так: перед отправкой дополняем кадр зеркальными полями до нужной пропорции (pad),
после генерации срезаем их обратно (crop). Оба шага локальные и бесплатные.

    python3 scripts/fit_aspect.py in.jpg --mode pad  --to 4:3      # 758x647 -> 863x647
    python3 scripts/fit_aspect.py out.jpg --mode crop --to 758:647 # 2364x1774 -> 2077x1774
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

OUT_DIR = Path(__file__).resolve().parent.parent / "work" / "magnific"


def parse_ratio(text: str) -> float:
    w, _, h = text.partition(":")
    return float(w) / float(h)


def pad_reflect(im: Image.Image, target: float) -> Image.Image:
    """Дополняет кадр зеркальным отражением краёв — модель читает это как продолжение сцены."""
    current = im.width / im.height
    if abs(current - target) < 0.001:
        return im

    if current < target:  # нужно шире
        new_w, new_h = round(im.height * target), im.height
        pad = (new_w - im.width) // 2
        canvas = Image.new("RGB", (new_w, new_h))
        canvas.paste(im, (pad, 0))
        left = im.crop((0, 0, pad, im.height)).transpose(Image.FLIP_LEFT_RIGHT)
        right = im.crop((im.width - (new_w - im.width - pad), 0, im.width, im.height)).transpose(
            Image.FLIP_LEFT_RIGHT
        )
        canvas.paste(left, (0, 0))
        canvas.paste(right, (pad + im.width, 0))
        return canvas

    new_w, new_h = im.width, round(im.width / target)
    pad = (new_h - im.height) // 2
    canvas = Image.new("RGB", (new_w, new_h))
    canvas.paste(im, (0, pad))
    top = im.crop((0, 0, im.width, pad)).transpose(Image.FLIP_TOP_BOTTOM)
    bottom = im.crop((0, im.height - (new_h - im.height - pad), im.width, im.height)).transpose(
        Image.FLIP_TOP_BOTTOM
    )
    canvas.paste(top, (0, 0))
    canvas.paste(bottom, (0, pad + im.height))
    return canvas


def crop_center(im: Image.Image, target: float) -> Image.Image:
    current = im.width / im.height
    if abs(current - target) < 0.001:
        return im
    if current > target:  # слишком широкий — режем бока
        new_w = round(im.height * target)
        off = (im.width - new_w) // 2
        return im.crop((off, 0, off + new_w, im.height))
    new_h = round(im.width / target)
    off = (im.height - new_h) // 2
    return im.crop((0, off, im.width, off + new_h))


def main() -> int:
    p = argparse.ArgumentParser(description="pad/crop до заданной пропорции")
    p.add_argument("image", type=Path)
    p.add_argument("--mode", choices=["pad", "crop"], required=True)
    p.add_argument("--to", required=True, help="целевая пропорция, напр. 4:3 или 758:647")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    target = parse_ratio(args.to)
    with Image.open(args.image) as src:
        im = src.convert("RGB")
        before = f"{im.width}x{im.height}"
        im = pad_reflect(im, target) if args.mode == "pad" else crop_center(im, target)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.image.stem}_{args.mode}.jpg"
    im.save(out_path, quality=95)
    print(f"{args.mode}: {before} -> {im.width}x{im.height} ({im.width / im.height:.3f}) — {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
