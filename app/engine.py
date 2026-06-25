"""Real-ESRGAN engine wrapper (realesrgan-ncnn-vulkan binary + OpenCV post-processing).

Why ncnn (not torch): validated in eval-stage0 — lighter (~30MB, no torch), faster on a weak
CPU box, fewer dependency landmines. The binary always upscales x4 with the photo model
(realesrgan-x4plus); target-aware output is achieved by resizing the x4 result to the desired
size (super-resolve -> downscale gives clean x2 etc.).
"""
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

from .config import settings

log = logging.getLogger("enhance.engine")

ProgressCb = Optional[Callable[[float], None]]


class EngineError(Exception):
    pass


def _read_image_oriented(path: Path) -> np.ndarray:
    """Read image as BGR applying EXIF orientation (cv2.imread ignores EXIF).

    Fast path (no EXIF rotation needed): cv2.imread directly — avoids full PIL decode,
    np.array copy, and cvtColor overhead (saves ~5× on large JPEGs).

    Slow path (EXIF orientation present and ≠1): PIL exif_transpose to physically rotate pixels,
    then convert to BGR.  This keeps the browser-visible orientation baked into the output.
    """
    try:
        # Cheap peek: open without decoding pixels, just read the EXIF tag.
        pim_meta = Image.open(str(path))
        orientation = pim_meta.getexif().get(0x0112)  # 0x0112 = Orientation
    except Exception as e:
        raise EngineError(f"cannot read input image: {e}")

    if not orientation or orientation == 1:
        # Fast path: cv2 reads JPEG/PNG efficiently without extra copies.
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise EngineError(f"cv2.imread returned None for: {path}")
        return img

    # Slow path: EXIF rotation required — apply via PIL then hand off to cv2 colour space.
    log.info("applied EXIF orientation %s", orientation)
    try:
        pim_meta.seek(0)  # rewind for full decode (same file handle, already opened)
        pim = ImageOps.exif_transpose(pim_meta).convert("RGB")
    except Exception as e:
        raise EngineError(f"cannot apply EXIF orientation: {e}")
    return cv2.cvtColor(np.array(pim), cv2.COLOR_RGB2BGR)


def _run_ncnn(input_path: Path, output_path: Path, progress_cb: ProgressCb = None) -> None:
    """Run one realesrgan-ncnn-vulkan x4 pass. Parses stderr for progress percentages."""
    if not Path(settings.ncnn_bin).exists():
        raise EngineError(f"ncnn binary not found: {settings.ncnn_bin}")
    if not settings.model_param_path().exists():
        raise EngineError(f"model not found: {settings.model_param_path()}")

    cmd = [
        settings.ncnn_bin,
        "-i", str(input_path),
        "-o", str(output_path),
        "-n", settings.model_name,
        "-m", str(settings.model_dir),
    ]
    if settings.ncnn_tile > 0:
        cmd += ["-t", str(settings.ncnn_tile)]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    err_tail = []
    assert proc.stderr is not None
    for line in proc.stderr:
        s = line.strip()
        err_tail.append(s)
        if len(err_tail) > 10:
            err_tail.pop(0)
        # ncnn prints progress like "25.00%"
        if s.endswith("%"):
            try:
                if progress_cb:
                    progress_cb(float(s[:-1]))
            except ValueError:
                pass
    rc = proc.wait()
    if rc != 0 or not output_path.exists():
        raise EngineError(f"ncnn failed (rc={rc}): {' | '.join(err_tail)[:300]}")


def _unsharp(img: np.ndarray, sigma: float = 0.7, strength: float = 0.4) -> np.ndarray:
    """Gentle unsharp mask for print crispness (no halos)."""
    try:
        blur = cv2.GaussianBlur(img, (0, 0), sigma)
        return cv2.addWeighted(img, 1.0 + strength, blur, -strength, 0)
    except Exception:
        return img


def _write_jpeg_capped(img: np.ndarray, output_path: Path) -> Tuple[int, int]:
    """Encode JPEG under max_output_bytes: drop quality, then downscale if still too big."""
    q = settings.jpeg_quality
    while True:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            raise EngineError("jpeg encode failed")
        if len(buf) <= settings.max_output_bytes or q <= 60:
            output_path.write_bytes(buf.tobytes())
            h, w = img.shape[:2]
            return w, h
        q -= 8
        if q < 60:  # last resort: shrink 15% and retry
            h, w = img.shape[:2]
            img = cv2.resize(img, (int(w * 0.85), int(h * 0.85)), interpolation=cv2.INTER_AREA)
            q = settings.jpeg_quality


def enhance(
    input_path: str,
    output_path: str,
    target_w: Optional[int] = None,
    target_h: Optional[int] = None,
    scale_cap: float = 4.0,
    face_restore: bool = False,
    progress_cb: ProgressCb = None,
) -> Tuple[int, int]:
    """Upscale input to target (or default x2), capped, sharpened, saved as JPEG.

    Returns (width, height). face_restore is accepted for contract C1 but is a no-op in MVP
    (face restoration ships in a later step; see README).
    """
    in_path, out_path = Path(input_path), Path(output_path)
    img = _read_image_oriented(in_path)  # EXIF baked in (cv2.imread would ignore it)
    orig_h, orig_w = img.shape[:2]

    if face_restore:
        log.info("face_restore requested but not implemented in MVP — ignoring")

    # Upscale factor needed to cover the target slot. ASPECT IS PRESERVED (no crop): the
    # enhanced image is a drop-in replacement, the editor's placement keeps its own crop.
    if target_w and target_h:
        needed = max(target_w / orig_w, target_h / orig_h)
    else:
        needed = 2.0  # default x2
    needed = min(needed, float(scale_cap))
    max_scale = settings.max_output_px / max(orig_w, orig_h)
    needed = min(needed, max_scale)

    if needed <= 1.05:
        # Already enough resolution for the slot — don't upscale, and never downscale/crop
        # (that degrades quality and changes aspect). EXIF-normalized passthrough.
        out = img
    else:
        if needed > 1.1:
            settings.work_dir.mkdir(parents=True, exist_ok=True)
            # Bound ncnn work: x4 output must stay <= MAX_OUTPUT_PX, so cap ncnn input side to
            # MAX_OUTPUT_PX/4 (large originals needing a small upscale would otherwise OOM).
            max_ncnn_side = max(64, settings.max_output_px // 4)
            with tempfile.TemporaryDirectory(dir=str(settings.work_dir)) as td:
                longest = max(orig_w, orig_h)
                if longest > max_ncnn_side:
                    s = max_ncnn_side / longest
                    work = cv2.resize(
                        img,
                        (max(1, round(orig_w * s)), max(1, round(orig_h * s))),
                        interpolation=cv2.INTER_AREA,
                    )
                else:
                    work = img
                ncnn_input = Path(td) / "in.jpg"
                # JPEG is 10× faster to write than PNG; ncnn accepts jpg/png/webp equally.
                # Use quality=96 to keep ringing artefacts below ncnn's own noise floor.
                cv2.imwrite(str(ncnn_input), work, [cv2.IMWRITE_JPEG_QUALITY, 96])
                x4_path = Path(td) / "x4.png"
                _run_ncnn(ncnn_input, x4_path, progress_cb)  # always x4
                up = cv2.imread(str(x4_path), cv2.IMREAD_COLOR)
            if up is None:
                raise EngineError("ncnn output unreadable")
        else:
            up = img  # small upscale (1.05–1.1): plain Lanczos, no AI pass

        out_w = max(1, round(orig_w * needed))
        out_h = max(1, round(orig_h * needed))
        interp = cv2.INTER_AREA if out_w < up.shape[1] else cv2.INTER_LANCZOS4
        out = cv2.resize(up, (out_w, out_h), interpolation=interp)  # aspect preserved
        out = _unsharp(out)

    w, h = _write_jpeg_capped(out, out_path)
    log.info("enhanced %dx%d -> %dx%d (needed=%.2f)", orig_w, orig_h, w, h, needed)
    return w, h
