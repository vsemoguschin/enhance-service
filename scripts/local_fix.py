#!/usr/bin/env python3
"""Локальная база под скан старого фото: поворот + автоуровни + баланс белого + резкость.

Зачем: у пересъёмок напечатанных фотографий проблема не в числе пикселей, а в свете и цвете.
Всё это чинится офлайн и бесплатно — платная генеративная модель нужна только для того,
что осталось после этого шага (блик от вспышки, реальная детализация).

    python3 scripts/local_fix.py photo.jpg --rotate -90

Результат — в work/magnific/ рядом с остальными вариантами сравнения.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

OUT_DIR = Path(__file__).resolve().parent.parent / "work" / "magnific"


def gray_world_white_balance(im: Image.Image) -> Image.Image:
    """Убирает цветной оттенок выцветшей бумаги: выравнивает средние по каналам."""
    r, g, b = im.split()
    means = [ch.resize((1, 1), Image.BOX).getpixel((0, 0)) for ch in (r, g, b)]
    target = sum(means) / 3
    return Image.merge(
        "RGB",
        [ch.point(lambda v, m=m: min(255, int(v * target / m)) if m else v) for ch, m in zip((r, g, b), means)],
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Локальная коррекция скана без обращения к API")
    p.add_argument("image", type=Path)
    p.add_argument(
        "--rotate",
        type=int,
        default=0,
        help="принудительный доворот; обычно НЕ нужен — EXIF-ориентация применяется сама",
    )
    p.add_argument("--cutoff", type=float, default=0.5, help="процент отсечки для автоуровней")
    p.add_argument("--saturation", type=float, default=1.15)
    p.add_argument("--sharpen", type=float, default=1.3, help="сила unsharp mask, 1.0 = выкл")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    with Image.open(args.image) as src:
        im = ImageOps.exif_transpose(src).convert("RGB")

    if args.rotate:
        im = im.rotate(args.rotate, expand=True)

    im = ImageOps.autocontrast(im, cutoff=args.cutoff)
    im = gray_world_white_balance(im)
    if args.saturation != 1.0:
        im = ImageEnhance.Color(im).enhance(args.saturation)
    if args.sharpen != 1.0:
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=int(args.sharpen * 100), threshold=3))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.image.stem}_localfix.jpg"
    im.save(out_path, quality=95)
    print(f"готово: {out_path} — {im.width}x{im.height}, {out_path.stat().st_size / 1024 / 1024:.2f} МБ")
    print("стоимость: 0 кредитов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
