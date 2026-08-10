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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import settings
from .prompts import PROMPTS

log = logging.getLogger("enhance.codex")


class CodexError(Exception):
    pass


def _build_prompt(in_path: Path, out_path: Path) -> str:
    """Инструкция агенту: одна задача, фиксированный путь результата, короткий ответ."""
    preset = PROMPTS.get(settings.codex_preset) or PROMPTS["texture"]
    return (
        f"Улучши изображение {in_path} с помощью skill imagegen.\n"
        f"Промпт для улучшения: «{preset}»\n"
        f"Сохрани результат СТРОГО в файл {out_path} (скопируй туда сгенерированный файл).\n"
        "Не выполняй никаких других задач, не меняй другие файлы, не задавай вопросов.\n"
        "В финальном ответе напиши только OK или FAIL."
    )


def _run_codex(in_path: Path, out_path: Path, workdir: Path) -> None:
    cmd = [
        settings.codex_bin,
        "exec",
        "-C",
        str(workdir),
        "-s",
        "workspace-write",
        "--skip-git-repo-check",
        _build_prompt(in_path, out_path),
    ]
    log.info("codex exec: старт (таймаут %sс)", settings.codex_timeout_s)
    try:
        proc = subprocess.run(  # noqa: S603 — аргументы массивом, shell не используется
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.codex_timeout_s,
            cwd=str(workdir),
            # Без этого codex ждёт инструкции из stdin и висит до таймаута:
            # промпт передан аргументом, читать ему нечего.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise CodexError(f"codex exec не уложился в {settings.codex_timeout_s}с") from e
    except FileNotFoundError as e:
        raise CodexError(f"codex не найден: {settings.codex_bin}") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise CodexError(f"codex exec вернул {proc.returncode}: {tail}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        # Агент мог «поговорить» вместо работы — показываем хвост, чтобы это было видно в логе.
        tail = (proc.stdout or "").strip()[-300:]
        raise CodexError(f"codex не создал файл результата. Хвост вывода: {tail}")


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

        # EXIF уже применён при чтении — на диск кладём нормализованный кадр.
        cv2.imwrite(str(in_path), img)

        if progress_cb:
            progress_cb(15)
        _run_codex(in_path, out_path, workdir)
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

    if progress_cb:
        progress_cb(95)
    w, h = engine._write_jpeg_capped(out, Path(output_path))
    log.info("codex done: %dx%d -> %dx%d", orig_w, orig_h, w, h)
    return w, h
