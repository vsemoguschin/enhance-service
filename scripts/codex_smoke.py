#!/usr/bin/env python3
"""Прогон одного фото через провайдер codex — целиком, как в проде.

Отличие от finish_preview.py: тот подбирает резкость офлайн, а этот дёргает саму модель,
то есть тратит лимит подписки и ~3.5 минуты. Пользоваться, когда нужно проверить весь
путь (промпт → imagegen → правка пропорции → финиш детализации), а не одну его ступень.

    python3 scripts/codex_smoke.py photo.jpg --preset preserve

Результат и метрики — в work/codex/.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT_DIR = Path(__file__).resolve().parent.parent / "work" / "codex"


def sharpness(img) -> float:
    return cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


def _preprocess(img: np.ndarray, mode: str) -> np.ndarray:
    """Деликатно проявляет уже записанные детали перед imagegen, не меняя цвет."""
    if mode == "none":
        return img

    from app import finish  # импорт после настройки env в main

    if mode == "soft":
        enabled = finish.settings.finish_detail
        finish.settings.finish_detail = True
        try:
            return finish.detail_finish(
                img.copy(), amount=0.45, micro_contrast=0.0, sharpen=0.30
            )
        finally:
            finish.settings.finish_detail = enabled

    # Мягкая Wiener-деконволюция только по яркости. Используется как лабораторный
    # вход для заведомо размытого кадра; a/b остаются исходными.
    from deconv_test import gaussian_psf, wiener

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l3 = cv2.cvtColor(l, cv2.COLOR_GRAY2BGR)
    restored = wiener(l3, gaussian_psf(1.0), noise=0.03)
    l_out = cv2.cvtColor(restored, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(cv2.merge((l_out, a, b)), cv2.COLOR_LAB2BGR)


def main() -> int:
    p = argparse.ArgumentParser(description="Полный прогон кадра через codex")
    p.add_argument("image", type=Path)
    p.add_argument("--preset", default="preserve", help="ключ из app/prompts.py")
    p.add_argument("--model", default="", help="переопределить CODEX_MODEL агента")
    p.add_argument("--reasoning-effort", default="", help="переопределить CODEX_REASONING_EFFORT")
    p.add_argument("--no-finish", action="store_true", help="без локальной детализации")
    p.add_argument(
        "--preprocess", choices=("none", "soft", "deblur"), default="none",
        help="предварительная безопасная коррекция перед Codex",
    )
    p.add_argument("--timeout", type=int, default=600,
                   help="прод держит 150с, но для эксперимента ограничение снимаем")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    os.environ["CODEX_PRESET"] = args.preset
    os.environ["CODEX_TIMEOUT_S"] = str(args.timeout)
    if args.model:
        os.environ["CODEX_MODEL"] = args.model
    if args.reasoning_effort:
        os.environ["CODEX_REASONING_EFFORT"] = args.reasoning_effort
    if args.no_finish:
        os.environ["FINISH_DETAIL"] = "false"

    from app import codex_provider  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (
        f"{args.preset}_pre-{args.preprocess}"
        f"{'_nofinish' if args.no_finish else ''}"
        f"{'_' + args.model if args.model else ''}"
    )
    dst = OUT_DIR / f"{args.image.stem}_{suffix}.jpg"

    from app import engine

    src = engine._read_image_oriented(args.image)
    prepared = _preprocess(src, args.preprocess)
    run_input = args.image
    if args.preprocess != "none":
        run_input = OUT_DIR / f"{args.image.stem}_pre-{args.preprocess}.png"
        cv2.imwrite(str(run_input), prepared)

    # Сырой результат модели нужен отдельно: один вызов потом можно сравнить и без
    # финиша, и с несколькими локальными финишами, не расходуя генерацию повторно.
    if args.no_finish:
        settings.finish_detail = False

    started = time.time()
    w, h = codex_provider.enhance(str(run_input), str(dst))
    took = time.time() - started

    out = cv2.imread(str(dst), cv2.IMREAD_COLOR)
    print(f"вход:  {src.shape[1]}x{src.shape[0]} = {src.shape[1] * src.shape[0] / 1e6:.2f} Мп, "
          f"резкость {sharpness(src):.0f}")
    if args.preprocess != "none":
        print(f"пред:  {prepared.shape[1]}x{prepared.shape[0]}, резкость {sharpness(prepared):.0f} "
              f"({args.preprocess})")
    print(f"выход: {w}x{h} = {w * h / 1e6:.2f} Мп, резкость {sharpness(out):.0f}")
    print(
        f"время: {took:.0f}с (таймаут запуска {settings.codex_timeout_s}с — "
        f"{'НЕ уложились' if took > settings.codex_timeout_s else 'уложились'})"
    )
    print(f"файл:  {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
