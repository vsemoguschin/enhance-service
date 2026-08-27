"""Локальная детализация результата — то, чего генеративный шаг не делает.

Зачем отдельным шагом: замер 2026-08-21 показал, что у генеративной модели нет рабочей
середины. Дай ей свободу («restore for print») — перерисует лица и пересоберёт кадр;
свяжи запретами — вернёт копию входа (резкость 637 против 644 у оригинала, то есть ноль).
Просить у неё «сделай детальнее» бессмысленно: она не правит пиксели, а рисует заново,
и любая добавленная «детальность» будет выдуманной.

Поэтому чёткость добавляем здесь, арифметикой по уже готовому кадру. Ничего не выдумывается:
усиливается только то, что в пикселях уже есть.

Метод — разделение на базовый слой и слой деталей (bilateral сохраняет крупные границы,
разница с ним = мелкая фактура), затем усиление слоя деталей. В отличие от простого
unsharp mask это не даёт светлых ореолов по контрастным контурам — а на групповом портрете
ореол вокруг голов виден сразу.
"""
import cv2
import numpy as np

from .config import settings


def _detail_layer_boost(img: np.ndarray, amount: float, sigma_color: float, sigma_space: float) -> np.ndarray:
    """Усиливает мелкую фактуру, не трогая крупные переходы."""
    base = cv2.bilateralFilter(img, d=0, sigmaColor=sigma_color, sigmaSpace=sigma_space)
    detail = img.astype(np.float32) - base.astype(np.float32)
    out = base.astype(np.float32) + detail * (1.0 + amount)
    return np.clip(out, 0, 255).astype(np.uint8)


def _micro_contrast(img: np.ndarray, clip: float) -> np.ndarray:
    """CLAHE по яркости: вытягивает локальный контраст, не сдвигая цвет."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def _retinex_luma(img: np.ndarray, sigmas=(15, 80, 250)) -> np.ndarray:
    """Проявляет тени по яркости, сохраняя цветовые каналы исходника."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lf = l.astype(np.float32) + 1.0
    acc = np.zeros_like(lf)
    for sigma in sigmas:
        acc += np.log(lf) - np.log(cv2.GaussianBlur(lf, (0, 0), sigma) + 1.0)
    acc /= len(sigmas)
    acc = cv2.normalize(acc, None, 0, 255, cv2.NORM_MINMAX)
    mixed = cv2.addWeighted(l, 0.6, acc.astype(np.uint8), 0.4, 0)
    return cv2.cvtColor(cv2.merge((mixed, a, b)), cv2.COLOR_LAB2BGR)


def _threshold_unsharp(img: np.ndarray, amount: float, radius: float, threshold: int) -> np.ndarray:
    """Финальная резкость только там, где есть перепад: порог держит шум в тенях и на коже."""
    blur = cv2.GaussianBlur(img, (0, 0), radius)
    diff = img.astype(np.float32) - blur.astype(np.float32)
    # Пиксели с перепадом ниже порога считаем шумом и не трогаем.
    mask = np.abs(diff).max(axis=2, keepdims=True) >= threshold
    out = img.astype(np.float32) + diff * amount * mask
    return np.clip(out, 0, 255).astype(np.uint8)


def _luma_only(img: np.ndarray, fn) -> np.ndarray:
    """Прогоняет операцию по каналу яркости, оставляя цвет исходным.

    Зачем: усиление фактуры по каналам BGR подкрашивает кадр — замер 21.08 дал +5%
    насыщенности, и владелец описал результат как «цвета выкручены». По яркости той же
    силы прибавка читается как детализация, а не как обработка.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l3 = cv2.cvtColor(l, cv2.COLOR_GRAY2BGR)
    l_out = cv2.cvtColor(fn(l3), cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(cv2.merge((l_out, a, b)), cv2.COLOR_LAB2BGR)


def _apply_finish(
    img: np.ndarray, *, amount: float, micro_contrast: float, sharpen: float
) -> np.ndarray:
    """Применяет уже разрешённые параметры без проверки runtime-флага."""
    luma_only = settings.finish_luma_only

    if amount > 0:
        boost = lambda x: _detail_layer_boost(  # noqa: E731
            x,
            amount=amount,
            sigma_color=settings.finish_detail_sigma_color,
            sigma_space=settings.finish_detail_sigma_space,
        )
        img = _luma_only(img, boost) if luma_only else boost(img)
    if micro_contrast > 0:
        img = _micro_contrast(img, clip=micro_contrast)
    if sharpen > 0:
        sharp = lambda x: _threshold_unsharp(  # noqa: E731
            x,
            amount=sharpen,
            radius=settings.finish_sharpen_radius,
            threshold=settings.finish_sharpen_threshold,
        )
        img = _luma_only(img, sharp) if luma_only else sharp(img)
    return img


def detail_finish(
    img: np.ndarray,
    *,
    amount: float | None = None,
    micro_contrast: float | None = None,
    sharpen: float | None = None,
) -> np.ndarray:
    """Полный финиш детализации. Порядок важен: сначала фактура, потом контраст, потом край.

    Именованные аргументы переопределяют настройки из env — так подбирают силу
    (scripts/finish_preview.py) без перезапуска процесса.
    """
    if not settings.finish_detail:
        return img

    return _apply_finish(
        img,
        amount=settings.finish_detail_amount if amount is None else amount,
        micro_contrast=(
            settings.finish_micro_contrast if micro_contrast is None else micro_contrast
        ),
        sharpen=settings.finish_sharpen_amount if sharpen is None else sharpen,
    )


def codex_reference_variants(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Строит две подсказки для multi-reference, не меняя authoritative-оригинал.

    Значения повторяют выбранные владельцем варианты из лабораторной матрицы:
    Retinex + умеренный финиш для теней и сильный финиш без CLAHE для фактуры.
    Они намеренно не зависят от FINISH_DETAIL: этот флаг управляет выходным финишем,
    а CODEX_MULTI_REFERENCE — входами генеративного шага.
    """
    shadow = _retinex_luma(img)
    shadow = _apply_finish(shadow, amount=0.5, micro_contrast=0.0, sharpen=0.35)
    detail = _apply_finish(img.copy(), amount=0.9, micro_contrast=0.0, sharpen=0.70)
    return shadow, detail
