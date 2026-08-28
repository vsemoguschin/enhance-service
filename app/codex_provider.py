"""Провайдер codex — улучшение через встроенный image-generation инструмент Codex CLI.

Зачем: на реальных фото он дал лучший результат из проверенных (см.
docs/audits/2026-08-10-magnific-api-test.md). Seedream пересобирает композицию на панорамах
и дрейфует по лицам, nano-banana дорисовывает предметы — codex сохранил и людей, и кадр,
и пропорцию входа. Платится подпиской, а не за вызов, поэтому «дольше, но без галлюцинаций
и без кредитов» — сознательный размен.

Механика: `codex exec` запускается неинтерактивно в одноразовом рабочем каталоге, кладёт
результат в заранее заданный файл, мы его валидируем. Агент запускается с sandbox
workspace-write и видит только свой каталог задачи.

Ограничения на боксе (2 ядра, 1 ГБ RAM): один-два воркера, не больше — каждый прогон это
отдельный node-процесс рядом с ai-assistant.
"""
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence, Tuple

import cv2

from . import finish
from .config import settings
from .prompts import (
    CODEX_IDENTITY_REFERENCE_PROMPT,
    CODEX_IDENTITY_SHADOW_REFERENCE_PROMPT,
    CODEX_MULTI_REFERENCE_PROMPT,
    PROMPTS,
)

log = logging.getLogger("enhance.codex")


class CodexError(Exception):
    pass


def _kill_process_group(proc: "subprocess.Popen") -> None:
    """Снимает всю группу процессов агента, а не только родителя."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError) as e:
        log.warning("codex: не удалось снять группу процессов (%s), убиваем родителя", e)
        try:
            proc.kill()
        except OSError:
            pass


def _build_prompt(
    in_path: Path,
    out_path: Path,
    shadow_path: Optional[Path] = None,
    detail_path: Optional[Path] = None,
    identity_path: Optional[Path] = None,
    identity_shadow_path: Optional[Path] = None,
) -> str:
    """Инструкция агенту: одна задача, фиксированный путь результата, короткий ответ."""
    if shadow_path is not None and detail_path is not None:
        reference_lines = [
            f"1. {in_path} — authoritative original/edit target and sole source of composition;",
            f"2. {shadow_path} — shadow-detail reference only;",
            f"3. {detail_path} — edge-and-texture reference only;",
        ]
        enhancement_prompt = CODEX_MULTI_REFERENCE_PROMPT
        if identity_path is not None:
            reference_lines.append(
                f"4. {identity_path} — original-resolution identity-context crop only;"
            )
            enhancement_prompt += CODEX_IDENTITY_REFERENCE_PROMPT
        if identity_shadow_path is not None:
            if identity_path is None:
                raise ValueError("identity-shadow reference requires identity reference")
            reference_lines.append(
                f"5. {identity_shadow_path} — shadow-detail version of the same identity crop only;"
            )
            enhancement_prompt += CODEX_IDENTITY_SHADOW_REFERENCE_PROMPT
        references = (
            "Передай imagegen эти exact referenced_image_paths строго в таком порядке:\n"
            + "\n".join(reference_lines)
            + "\n"
        )
    else:
        references = f"Передай imagegen один edit target: {in_path}.\n"
        enhancement_prompt = PROMPTS.get(settings.codex_preset) or PROMPTS["texture"]
    return (
        "Улучши фотографию с помощью skill imagegen. Вызови imagegen ровно один раз.\n"
        f"{references}"
        f"Промпт для imagegen: «{enhancement_prompt}»\n"
        f"Сохрани результат СТРОГО в файл {out_path} (скопируй туда сгенерированный файл).\n"
        "Не выполняй никаких других задач, не меняй другие файлы, не задавай вопросов.\n"
        "В финальном ответе напиши только OK или FAIL."
    )


def _run_codex(
    in_path: Path,
    out_path: Path,
    workdir: Path,
    shadow_path: Optional[Path] = None,
    detail_path: Optional[Path] = None,
    identity_path: Optional[Path] = None,
    identity_shadow_path: Optional[Path] = None,
) -> None:
    cmd = [
        settings.codex_bin,
        "exec",
        "-C",
        str(workdir),
        "-s",
        "workspace-write",
        "--skip-git-repo-check",
        _build_prompt(
            in_path,
            out_path,
            shadow_path,
            detail_path,
            identity_path,
            identity_shadow_path,
        ),
    ]
    log.info("codex exec: старт (таймаут %sс)", settings.codex_timeout_s)
    # Своя группа процессов: при таймауте subprocess убивает только родителя, а codex порождает
    # дочерние. Уцелевшие держали бы pipe открытым, и ожидание вывода не завершалось —
    # так воркеры зависали при обрыве сети (инцидент 2026-08-12).
    try:
        proc = subprocess.Popen(  # noqa: S603 — аргументы массивом, shell не используется
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(workdir),
            # Без этого codex ждёт инструкции из stdin: промпт передан аргументом, читать нечего.
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        raise CodexError(f"codex не найден: {settings.codex_bin}") from e

    try:
        stdout, stderr = proc.communicate(timeout=settings.codex_timeout_s)
    except subprocess.TimeoutExpired as e:
        _kill_process_group(proc)
        # Второй раз ждём коротко: группа уже убита, но pipe нужно закрыть.
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("codex: группа процессов не закрыла вывод после kill")
        raise CodexError(f"codex exec не уложился в {settings.codex_timeout_s}с") from e

    if proc.returncode != 0:
        tail = (stderr or stdout or "").strip()[-300:]
        raise CodexError(f"codex exec вернул {proc.returncode}: {tail}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        # Агент мог «поговорить» вместо работы — показываем хвост, чтобы это было видно в логе.
        tail = (stdout or "").strip()[-300:]
        raise CodexError(f"codex не создал файл результата. Хвост вывода: {tail}")


FaceBox = tuple[int, int, int, int]
FloatFaceBox = tuple[float, float, float, float]


def _resize_down_to_long_side(img, max_px: int):
    """Уменьшает до long side, но никогда не апскейлит."""
    longest = max(img.shape[1], img.shape[0])
    if max_px <= 0 or longest <= max_px:
        return img
    scale = max_px / longest
    return cv2.resize(
        img,
        (max(1, round(img.shape[1] * scale)), max(1, round(img.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _primary_input(img):
    """Возвращает рабочую копию: небольшие оригиналы остаются без resize."""
    if not settings.codex_adaptive_input:
        return img
    longest = max(img.shape[1], img.shape[0])
    if longest <= settings.codex_input_downscale_threshold_px:
        return img
    return _resize_down_to_long_side(img, settings.codex_input_max_px)


def _keep_primary_faces(candidates: Sequence[FloatFaceBox]) -> list[FloatFaceBox]:
    """Убирает мелкие фоновые лица, чтобы они не растягивали identity-crop всей сцены."""
    if not candidates:
        return []
    largest = max(min(w, h) for x, y, w, h in candidates)
    primary = [
        box
        for box in candidates
        if min(box[2], box[3]) >= largest * settings.codex_face_relative_min_scale
    ]
    return primary if len(primary) <= settings.codex_face_max_count else []


def _detect_faces(img) -> list[FaceBox]:
    """Находит лица через YuNet и возвращает координаты полноразмерного оригинала."""
    if not settings.codex_face_model.is_file():
        log.warning(
            "codex: модель лиц %s отсутствует, face-reference пропущен",
            settings.codex_face_model,
        )
        return []
    detect = _resize_down_to_long_side(img, settings.codex_face_detect_max_px)
    input_size = (detect.shape[1], detect.shape[0])
    try:
        detector = cv2.FaceDetectorYN.create(
            str(settings.codex_face_model),
            "",
            input_size,
            settings.codex_face_score_threshold,
            0.3,
            5000,
        )
        _, detected = detector.detect(detect)
    except (AttributeError, cv2.error) as e:
        log.warning("codex: YuNet недоступен, face-reference пропущен: %s", e)
        return []
    if detected is None:
        return []
    candidates = [
        (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        for row in detected
        if min(float(row[2]), float(row[3])) >= settings.codex_face_min_px
    ]
    if not candidates:
        return []
    found = _keep_primary_faces(candidates)
    if not found:
        log.warning(
            "codex: после фильтра нет допустимой группы лиц (кандидатов=%d, лимит=%d)",
            len(candidates),
            settings.codex_face_max_count,
        )
        return []
    scale_x = img.shape[1] / detect.shape[1]
    scale_y = img.shape[0] / detect.shape[0]
    boxes = [
        (
            round(x * scale_x),
            round(y * scale_y),
            max(1, round(w * scale_x)),
            max(1, round(h * scale_y)),
        )
        for x, y, w, h in found
    ]
    return sorted(boxes, key=lambda box: (box[0], box[1]))


def _identity_context_crop(img, faces: Sequence[FaceBox]):
    """Один непрерывный crop лиц/верхней части тела, без сетки и смены порядка людей."""
    if not faces:
        return None
    image_h, image_w = img.shape[:2]
    if len(faces) == 1:
        # Для маленького одиночного человека оставляем голову и верх тела — именно такой
        # контекст стабилизировал identity в локальной матрице.
        left = min(x - 2.0 * w for x, y, w, h in faces)
        top = min(y - 1.0 * h for x, y, w, h in faces)
        right = max(x + 3.0 * w for x, y, w, h in faces)
        bottom = max(y + 5.0 * h for x, y, w, h in faces)
    else:
        left = min(x - 1.2 * w for x, y, w, h in faces)
        top = min(y - 1.2 * h for x, y, w, h in faces)
        right = max(x + 2.8 * w for x, y, w, h in faces)
        bottom = max(y + 2.5 * h for x, y, w, h in faces)
    x1 = max(0, int(round(left)))
    y1 = max(0, int(round(top)))
    x2 = min(image_w, int(round(right)))
    y2 = min(image_h, int(round(bottom)))
    if x2 - x1 < 16 or y2 - y1 < 16:
        return None
    crop = img[y1:y2, x1:x2]
    return _resize_down_to_long_side(crop, settings.codex_face_reference_max_px)


def _single_face_is_dark(img, faces: Sequence[FaceBox]) -> bool:
    if len(faces) != 1:
        return False
    x, y, w, h = faces[0]
    face = img[max(0, y):min(img.shape[0], y + h), max(0, x):min(img.shape[1], x + w)]
    if face.size == 0:
        return False
    luma = cv2.cvtColor(face, cv2.COLOR_BGR2LAB)[:, :, 0]
    return float(luma.mean()) < settings.codex_face_shadow_max_luma


def enhance(
    input_path: str,
    output_path: str,
    target_w: Optional[int] = None,
    target_h: Optional[int] = None,
    scale_cap: float = 4.0,
    face_restore: bool = False,
    progress_cb=None,
) -> Tuple[int, int]:
    """Сигнатура совпадает с engine.enhance и magnific.enhance — очередь их не различает."""
    from . import engine  # локальный импорт: EXIF-чтение и запись с капами

    img = engine._read_image_oriented(Path(input_path))
    orig_h, orig_w = img.shape[:2]
    orig_ratio = orig_w / orig_h

    if progress_cb:
        progress_cb(5)

    # Одноразовый каталог: агент работает только внутри него и не видит остальной диск.
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(settings.work_dir), prefix="codex_") as td:
        workdir = Path(td)
        in_path = workdir / "in.png"
        out_path = workdir / "out.png"

        # EXIF уже применён. Большой оригинал остаётся источником crop лиц, а в imagegen
        # уходит рабочая копия; близкие к размеру выхода кадры не трогаем.
        primary = _primary_input(img)
        if not cv2.imwrite(str(in_path), primary):
            raise CodexError("не удалось записать основной input")

        shadow_path = None
        detail_path = None
        identity_path = None
        identity_shadow_path = None
        if settings.codex_multi_reference:
            reference_source = _resize_down_to_long_side(
                primary, settings.codex_reference_max_px
            )
            shadow, detail = finish.codex_reference_variants(reference_source)
            shadow_path = workdir / "in-shadow.png"
            detail_path = workdir / "in-detail.png"
            if not cv2.imwrite(str(shadow_path), shadow):
                raise CodexError("не удалось записать shadow-reference")
            if not cv2.imwrite(str(detail_path), detail):
                raise CodexError("не удалось записать detail-reference")

            if settings.codex_face_reference:
                faces = _detect_faces(img)
                identity = _identity_context_crop(img, faces)
                if identity is not None:
                    identity_path = workdir / "in-identity.png"
                    if not cv2.imwrite(str(identity_path), identity):
                        raise CodexError("не удалось записать identity-reference")
                    if _single_face_is_dark(img, faces):
                        identity_shadow, _ = finish.codex_reference_variants(identity)
                        identity_shadow_path = workdir / "in-identity-shadow.png"
                        if not cv2.imwrite(str(identity_shadow_path), identity_shadow):
                            raise CodexError("не удалось записать identity-shadow-reference")
                log.info(
                    "codex: face-reference faces=%d identity=%s shadow=%s",
                    len(faces),
                    identity_path is not None,
                    identity_shadow_path is not None,
                )

        if progress_cb:
            progress_cb(15)
        _run_codex(
            in_path,
            out_path,
            workdir,
            shadow_path,
            detail_path,
            identity_path,
            identity_shadow_path,
        )
        if progress_cb:
            progress_cb(85)

        out = cv2.imread(str(out_path), cv2.IMREAD_COLOR)
        if out is None:
            raise CodexError("результат не читается как изображение")

    # Модель иногда отдаёт кадр с чуть иной пропорцией — приводим к исходной,
    # иначе в редакторе поедет кроп (инвариант сервиса: аспект не меняется).
    out_ratio = out.shape[1] / out.shape[0]
    if abs(out_ratio - orig_ratio) / orig_ratio > 0.02:
        log.info("codex: правим пропорцию %.3f -> %.3f", out_ratio, orig_ratio)
        h, w = out.shape[:2]
        if out_ratio > orig_ratio:
            new_w = int(round(h * orig_ratio))
            off = (w - new_w) // 2
            out = out[:, off : off + new_w]
        else:
            new_h = int(round(w / orig_ratio))
            off = (h - new_h) // 2
            out = out[off : off + new_h, :]

    longest = max(out.shape[1], out.shape[0])
    if longest > settings.max_output_px:
        s = settings.max_output_px / longest
        out = cv2.resize(
            out, (round(out.shape[1] * s), round(out.shape[0] * s)), interpolation=cv2.INTER_AREA
        )

    # Чёткость модель не добавляет — под запретами она возвращает копию входа, поэтому
    # детализацию делаем сами, по уже готовому кадру. См. app/finish.py.
    out = finish.detail_finish(out)

    if progress_cb:
        progress_cb(95)
    w, h = engine._write_jpeg_capped(out, Path(output_path))
    log.info(
        "codex done: %dx%d -> %dx%d (input=%dx%d, multi_ref=%s, identity_ref=%s, finish=%s)",
        orig_w,
        orig_h,
        w,
        h,
        primary.shape[1],
        primary.shape[0],
        settings.codex_multi_reference,
        identity_path is not None,
        settings.finish_detail,
    )
    return w, h
