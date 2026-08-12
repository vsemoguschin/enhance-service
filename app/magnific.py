"""Провайдер Magnific API — генеративное восстановление вместо локального Real-ESRGAN.

Зачем: на codex нет GPU, ncnn считает фото минутами (см. docs/operations.md). Magnific уносит
компьют наружу — здесь остаётся HTTP-вызов и лёгкая обработка, так что бокс без GPU подходит.

Интерфейс совпадает с engine.enhance, поэтому очередь и контракт C1 не меняются.

Пайплайн (обоснование замерами — docs/audits/2026-08-10-magnific-api-test.md):
  EXIF + автоуровни -> pad до ближайшей пропорции Seedream -> seedream-v4-5-edit ->
  crop обратно -> (опц.) precision-апскейл -> JPEG под лимиты.

Pad/crop обязательны: пропорция выхода у Seedream выбирается из фиксированного списка, и без
подгонки кадр растягивается. Инвариант сервиса — аспект не меняется, enhanced подставляется
вместо оригинала и не должен ломать кроп в редакторе.
"""
import base64
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import settings
from .prompts import PROMPTS

log = logging.getLogger("enhance.magnific")

SEEDREAM_PATH = "/v1/ai/text-to-image/seedream-v4-5-edit"
PRECISION_PATH = "/v1/ai/image-upscaler-precision-v2"

# Пропорции, которые Seedream принимает. Произвольную задать нельзя — выбираем ближайшую.
ASPECT_PRESETS = {
    "square_1_1": 1.0,
    "classic_4_3": 4 / 3,
    "traditional_3_4": 3 / 4,
    "standard_3_2": 3 / 2,
    "portrait_2_3": 2 / 3,
    "widescreen_16_9": 16 / 9,
    "social_story_9_16": 9 / 16,
    "cinematic_21_9": 21 / 9,
}

# Seedream отдаёт максимум ~4 Мп, поэтому вход крупнее 2048 по длинной стороне бесполезен:
# он только раздувает base64 и время загрузки.
MAX_SEND_SIDE = 2048
# Документированный потолок площади результата у апскейлера.
MAX_UPSCALE_OUT_MP = 25.3


class MagnificError(Exception):
    pass


class Deadline:
    """Общий срок на всю задачу, а не на отдельный вызов.

    Без него таймауты складывались: три попытки по 120с в `_call` плюс паузы давали до шести
    минут на один запрос, а цикл опроса проверял срок только между итерациями. При обрыве сети
    (инцидент 2026-08-12) задача висела 10–15 минут вместо заявленных 150с, воркер не отпускал
    слот, и очередь вставала до ручного перезапуска.
    """

    def __init__(self, seconds: float) -> None:
        self.at = time.monotonic() + max(1.0, seconds)

    def remaining(self) -> float:
        return max(0.0, self.at - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def budget(self, cap: float) -> float:
        """Сколько ждать конкретный вызов: не дольше остатка и не дольше разумного потолка."""
        return max(1.0, min(cap, self.remaining()))


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # noqa: PLC0415 — опционально: на macOS системных корней у python.org нет

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _call(
    url: str,
    deadline: Deadline,
    payload: Optional[dict] = None,
    attempts: int = 3,
    cap: float = 60.0,
) -> dict:
    """POST (payload) или GET, но не дольше общего срока задачи.

    Ретраим сетевые сбои, 5xx и отдельно 429: при нескольких воркерах всплеск опросов может
    упереться в лимит Magnific (50 запросов/мин на ключ), и это лечится ожиданием, а не отказом.
    Остальные 4xx — ошибка запроса, повтор бесполезен.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    last_error = ""
    for attempt in range(attempts):
        if deadline.expired():
            raise MagnificError(f"{url.rsplit('/', 1)[-1]} -> дедлайн задачи истёк ({last_error})")

        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("x-magnific-api-key", settings.magnific_api_key)
        if data:
            req.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(
                req, timeout=deadline.budget(cap), context=_ssl_context()
            ) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            last_error = f"HTTP {e.code}: {body}"
            if e.code == 429:
                # Retry-After в секундах, если сервер его прислал; иначе окно лимита — минута.
                delay = int(e.headers.get("Retry-After") or 0) or 20 * (attempt + 1)
                pause = min(delay, 60, deadline.remaining())
                if pause <= 0:
                    break
                log.warning("rate limited by magnific, sleeping %ss", round(pause))
                time.sleep(pause)
                continue
            if e.code < 500:  # прочие 4xx не лечится повтором
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = f"network: {e}"
        if attempt + 1 < attempts:
            time.sleep(min(2 * (attempt + 1), deadline.remaining()))
    raise MagnificError(f"{url.rsplit('/', 1)[-1]} -> {last_error}")


def _run_task(
    path: str,
    payload: dict,
    progress_cb,
    lo: int,
    hi: int,
    deadline: Optional[Deadline] = None,
) -> bytes:
    """Ставит задачу, ждёт COMPLETED, возвращает байты результата — в пределах общего срока."""
    base = settings.magnific_base_url.rstrip("/")
    total = float(settings.magnific_timeout_s)
    deadline = deadline or Deadline(total)

    # Отправка картинки тяжелее опроса статуса, поэтому потолок на неё выше.
    body = _call(base + path, deadline, payload, cap=90.0)
    task_id = (body.get("data") or {}).get("task_id")
    if not task_id:
        raise MagnificError(f"no task_id in response: {json.dumps(body)[:200]}")

    while not deadline.expired():
        time.sleep(min(settings.magnific_poll_s, deadline.remaining()))
        if deadline.expired():
            break
        data = (_call(f"{base}{path}/{task_id}", deadline, cap=30.0).get("data") or {})
        status = data.get("status", "")
        if progress_cb:
            done_share = 1.0 - (deadline.remaining() / total)
            progress_cb(lo + (hi - lo) * min(0.95, max(0.0, done_share)))
        if status == "COMPLETED":
            urls = data.get("generated") or []
            if not urls:
                raise MagnificError("COMPLETED with empty generated[]")
            # Скачивание тоже внутри срока: иначе зависший CDN держал бы воркер как раньше.
            with urllib.request.urlopen(
                urls[0], timeout=deadline.budget(90.0), context=_ssl_context()
            ) as resp:
                return resp.read()
        if status == "FAILED":
            raise MagnificError(f"task failed: {json.dumps(data)[:200]}")
    raise MagnificError(f"timeout after {settings.magnific_timeout_s}s")


def _nearest_aspect(ratio: float) -> Tuple[str, float]:
    name = min(ASPECT_PRESETS, key=lambda k: abs(ASPECT_PRESETS[k] - ratio))
    return name, ASPECT_PRESETS[name]


def _generative_is_safe(width: int, height: int) -> Tuple[bool, str]:
    """Можно ли пускать кадр через генеративную модель.

    Seedream выбирает пропорцию выхода из фиксированного списка. Когда кадр далёк от любого
    пресета, модель не дополняет края, а пересобирает композицию: на панораме 2.22:1 она
    приблизила крайних людей и дорисовала два новых лица. Для таких кадров генерация
    запрещена — отдаём их апскейлеру, который ничего не выдумывает.
    """
    ratio = width / height
    _, preset_ratio = _nearest_aspect(ratio)
    drift = abs(preset_ratio - ratio) / ratio

    if drift > settings.magnific_max_aspect_drift:
        return False, f"пропорция {ratio:.2f} далека от пресетов (расхождение {drift * 100:.0f}%)"
    if ratio > 2.0 or ratio < 0.5:
        return False, f"панорамный кадр (пропорция {ratio:.2f})"
    return True, ""


def _pad_to_ratio(img: np.ndarray, target: float) -> np.ndarray:
    """Дополняет кадр зеркальными полями — модель читает их как продолжение сцены."""
    h, w = img.shape[:2]
    if abs(w / h - target) < 0.005:
        return img
    if w / h < target:  # нужно шире
        pad = int(round(h * target)) - w
        left = pad // 2
        return cv2.copyMakeBorder(img, 0, 0, left, pad - left, cv2.BORDER_REFLECT_101)
    pad = int(round(w / target)) - h
    top = pad // 2
    return cv2.copyMakeBorder(img, top, pad - top, 0, 0, cv2.BORDER_REFLECT_101)


def _crop_to_ratio(img: np.ndarray, target: float) -> np.ndarray:
    """Срезает поля обратно — восстанавливает исходную пропорцию кадра."""
    h, w = img.shape[:2]
    if abs(w / h - target) < 0.005:
        return img
    if w / h > target:
        new_w = int(round(h * target))
        off = (w - new_w) // 2
        return img[:, off : off + new_w]
    new_h = int(round(w / target))
    off = (h - new_h) // 2
    return img[off : off + new_h, :]


def _decode(raw: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise MagnificError("result is not a decodable image")
    return img


def _encode_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise MagnificError("failed to encode input")
    return base64.b64encode(buf.tobytes()).decode()


def _prepare(img: np.ndarray) -> np.ndarray:
    """Бесплатная база: автоуровни и разумный размер для отправки.

    Баланс белого сюда не входит намеренно: он убивает намеренный цвет сцены (закат, лампы),
    а цвет всё равно правит генеративный шаг.
    """
    if settings.magnific_autocontrast:
        # CLAHE по каналу яркости: мягче глобального autocontrast, не выбивает света.
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(lab[:, :, 0])
        img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    h, w = img.shape[:2]
    longest = max(w, h)
    if longest > MAX_SEND_SIDE:
        s = MAX_SEND_SIDE / longest
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return img


def _upscale_only(
    img: np.ndarray,
    orig_ratio: float,
    output_path: str,
    target_w: Optional[int],
    target_h: Optional[int],
    progress_cb,
) -> Tuple[int, int]:
    """Путь без генерации: Precision-апскейлер увеличивает, но ничего не дорисовывает.

    Пропорция у него берётся от входа, поэтому pad/crop не нужны.
    """
    from . import engine  # локальный импорт: переиспользуем запись с капами

    need = 2
    if target_w and target_h:
        need = max(2, min(4, round(max(target_w / img.shape[1], target_h / img.shape[0]))))
    if img.shape[1] * img.shape[0] * need**2 / 1e6 > MAX_UPSCALE_OUT_MP:
        need = 2

    raw = _run_task(
        PRECISION_PATH,
        {"image": _encode_b64(img), "scale_factor": need, "flavor": "photo"},
        progress_cb,
        lo=10,
        hi=90,
    )
    out = _crop_to_ratio(_decode(raw), orig_ratio)

    longest = max(out.shape[1], out.shape[0])
    if longest > settings.max_output_px:
        s = settings.max_output_px / longest
        out = cv2.resize(
            out, (round(out.shape[1] * s), round(out.shape[0] * s)), interpolation=cv2.INTER_AREA
        )

    w, h = engine._write_jpeg_capped(out, Path(output_path))
    log.info("magnific(upscale-only) done: -> %dx%d", w, h)
    return w, h


def enhance(
    input_path: str,
    output_path: str,
    target_w: Optional[int] = None,
    target_h: Optional[int] = None,
    scale_cap: float = 4.0,
    face_restore: bool = False,
    progress_cb=None,
) -> Tuple[int, int]:
    """Восстанавливает фото через Magnific. Сигнатура совпадает с engine.enhance."""
    from . import engine  # локальный импорт: переиспользуем EXIF-чтение и запись с капами

    if not settings.magnific_api_key:
        raise MagnificError("MAGNIFIC_API_KEY is not set")
    if face_restore:
        log.info("face_restore не нужен для magnific — восстановление лиц входит в модель")

    img = engine._read_image_oriented(Path(input_path))
    orig_h, orig_w = img.shape[:2]
    orig_ratio = orig_w / orig_h

    if progress_cb:
        progress_cb(5)
    prepared = _prepare(img)

    safe, reason = _generative_is_safe(orig_w, orig_h)
    if not safe:
        log.warning("magnific: генерация пропущена — %s; апскейлим без выдумывания", reason)
        return _upscale_only(prepared, orig_ratio, output_path, target_w, target_h, progress_cb)

    aspect_name, aspect_ratio = _nearest_aspect(orig_ratio)
    padded = _pad_to_ratio(prepared, aspect_ratio)

    preset = settings.magnific_preset
    prompt = PROMPTS.get(preset) or PROMPTS[settings.magnific_preset_fallback]
    log.info(
        "magnific: %dx%d -> preset=%s aspect=%s", orig_w, orig_h, preset, aspect_name
    )

    raw = _run_task(
        SEEDREAM_PATH,
        {
            "prompt": prompt,
            "reference_images": [_encode_b64(padded)],
            "aspect_ratio": aspect_name,
        },
        progress_cb,
        lo=10,
        hi=70,
    )
    out = _crop_to_ratio(_decode(raw), orig_ratio)

    # Апскейл — отдельные деньги (тариф по площади результата), поэтому только по флагу и только
    # когда слот реально крупнее того, что отдал генеративный шаг.
    if settings.magnific_upscale and target_w and target_h:
        need = max(target_w / out.shape[1], target_h / out.shape[0])
        out_mp = out.shape[1] * out.shape[0] * 4 / 1e6
        if need > 1.3 and out_mp <= MAX_UPSCALE_OUT_MP:
            try:
                raw2 = _run_task(
                    PRECISION_PATH,
                    {"image": _encode_b64(out), "scale_factor": 2, "flavor": "photo"},
                    progress_cb,
                    lo=70,
                    hi=90,
                )
                out = _crop_to_ratio(_decode(raw2), orig_ratio)
                log.info("magnific: upscaled to %dx%d", out.shape[1], out.shape[0])
            except MagnificError as e:
                # Не валим job: восстановленный кадр уже лучше оригинала.
                log.warning("upscale skipped (%s)", e)

    # Общий кап сервиса на длинную сторону.
    longest = max(out.shape[1], out.shape[0])
    if longest > settings.max_output_px:
        s = settings.max_output_px / longest
        out = cv2.resize(
            out, (round(out.shape[1] * s), round(out.shape[0] * s)), interpolation=cv2.INTER_AREA
        )

    if progress_cb:
        progress_cb(95)
    w, h = engine._write_jpeg_capped(out, Path(output_path))
    log.info("magnific done: %dx%d -> %dx%d", orig_w, orig_h, w, h)
    return w, h
