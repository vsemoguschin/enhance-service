#!/usr/bin/env python3
"""Подбор параметров финиша детализации на готовом кадре, без обращения к движку.

Полный прогон codex занимает 3.5 минуты и жжёт лимит подписки, поэтому силу резкости
подбираем офлайн: берём уже полученный результат модели и гоняем по нему finish
с разными параметрами.

    python3 scripts/finish_preview.py work/codex/out.jpg --amount 0.4 0.6 0.8

Метрика резкости — дисперсия лапласиана: сравнима только между кадрами одного размера,
поэтому в отчёте всегда печатается и исходное значение.
"""
import argparse

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT_DIR = Path(__file__).resolve().parent.parent / "work" / "finish"


def sharpness(img) -> float:
    return cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


def main() -> int:
    p = argparse.ArgumentParser(description="Подбор силы детализации без вызова движка")
    p.add_argument("image", type=Path)
    p.add_argument("--amount", type=float, nargs="+", default=[0.4, 0.6, 0.8],
                   help="сила усиления слоя деталей")
    p.add_argument("--sharpen", type=float, default=None, help="сила финального unsharp")
    p.add_argument("--micro", type=float, default=None, help="clipLimit для CLAHE, 0 = выкл")
    p.add_argument("--reference", type=Path, default=None,
                   help="оригинал для сравнения: покажет, насколько ушли от него")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    src = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    print(f"вход: {args.image.name} — {src.shape[1]}x{src.shape[0]}, резкость {sharpness(src):.0f}")

    if args.reference and args.reference.is_file():
        ref = cv2.imread(str(args.reference), cv2.IMREAD_COLOR)
        print(f"оригинал: {args.reference.name} — {ref.shape[1]}x{ref.shape[0]}, "
              f"резкость {sharpness(ref):.0f}")

    from app import finish  # noqa: PLC0415

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for amount in args.amount:
        out = finish.detail_finish(
            src.copy(), amount=amount, micro_contrast=args.micro, sharpen=args.sharpen
        )
        dst = OUT_DIR / f"{args.image.stem}_a{amount}.jpg"
        cv2.imwrite(str(dst), out, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f"  amount={amount}: резкость {sharpness(out):.0f} -> {dst}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
