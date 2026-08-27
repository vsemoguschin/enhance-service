#!/usr/bin/env python3
"""Матрица вариантов финиша по одному кадру — каждый файл в свою папку.

Раскладывает результаты в work/tests/<имя файла>/, чтобы варианты можно было
пролистывать подряд и сравнивать глазами. Метрики пишутся туда же в metrics.txt.

    python3 scripts/finish_matrix.py work/c1_result.jpg

Резкость меряется дисперсией лапласиана — сравнима только внутри одного кадра.
Цветность считается в LAB (a/b), она не зависит от яркости: насыщенность в HSV
на осветлённом кадре падает сама по себе и вводит в заблуждение.
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"

# Наборы подобраны 21.08: дефолт — прод-значения, «без CLAHE» владелец выбрал как лучший
# на групповом кадре, «сильный» — для заведомо мягких снимков (резкость ниже ~100).
VARIANTS = [
    ("01_finish_default", dict(amount=0.6, micro_contrast=0.6, sharpen=0.45)),
    ("02_finish_no_clahe", dict(amount=0.6, micro_contrast=0.0, sharpen=0.45)),
    ("03_finish_soft", dict(amount=0.45, micro_contrast=0.0, sharpen=0.30)),
    ("04_finish_strong", dict(amount=0.9, micro_contrast=0.0, sharpen=0.70)),
]


def measure(img, faces):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    a, b = lab[:, :, 1] - 128, lab[:, :, 2] - 128
    face_sharp = [
        cv2.Laplacian(cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        for (x, y, w, h) in faces
    ]
    return {
        "резкость": cv2.Laplacian(gray, cv2.CV_64F).var(),
        "лица": float(np.mean(face_sharp)) if face_sharp else 0.0,
        "яркость": lab[:, :, 0].mean(),
        "контраст": lab[:, :, 0].std(),
        "цветность": float(np.sqrt(a * a + b * b).mean()),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Матрица вариантов финиша, по папке на кадр")
    p.add_argument("image", type=Path)
    p.add_argument("--out", type=Path, default=None, help="куда класть (по умолчанию work/tests/<имя>)")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    out_dir = args.out or (ROOT / "work" / "tests" / args.image.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    faces = cv2.CascadeClassifier(CASCADE).detectMultiScale(
        cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(40, 40)
    )
    cv2.imwrite(str(out_dir / "00_input.jpg"), src, [cv2.IMWRITE_JPEG_QUALITY, 96])

    from app import finish  # noqa: PLC0415

    finish.settings.finish_luma_only = True

    lines = [f"кадр: {args.image.name} — {src.shape[1]}x{src.shape[0]} = "
             f"{src.shape[1] * src.shape[0] / 1e6:.2f} Мп",
             f"лиц найдено: {len(faces)}"
             + (f", размеры: {sorted((int(w) for _, _, w, _ in faces), reverse=True)}" if len(faces) else ""),
             ""]
    header = f"{'вариант':22} {'резкость':>9} {'лица':>7} {'яркость':>8} {'контраст':>9} {'цветность':>10}"
    lines.append(header)

    for name, values in [("00_input", None), *VARIANTS]:
        img = src.copy() if values is None else finish.detail_finish(src.copy(), **values)
        if values is not None:
            cv2.imwrite(str(out_dir / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 96])
        m = measure(img, faces)
        lines.append(f"{name:22} {m['резкость']:9.0f} {m['лица']:7.0f} "
                     f"{m['яркость']:8.1f} {m['контраст']:9.1f} {m['цветность']:10.2f}")

    (out_dir / "metrics.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nрезультаты: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
