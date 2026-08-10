"""enhance-service HTTP API (contract C1).

POST /enhance/start  (multipart) -> {job_id}
GET  /enhance/status/{job_id}    -> status
GET  /enhance/result/{job_id}    -> image/jpeg
GET  /health, GET /metrics
Auth: header X-Enhance-Api-Key (skipped if ENHANCE_API_KEY unset — dev only).
"""
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

import cv2
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .config import settings
from .queue import Job, manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("enhance")

app = FastAPI(title="enhance-service", version="0.1.0")

_CHUNK = 1 << 20  # 1 MiB


def auth(x_enhance_api_key: str = Header(default="")) -> None:
    if settings.api_key and x_enhance_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="bad api key")


@app.get("/health")
def health() -> dict:
    if settings.provider == "codex":
        ready = bool(shutil.which(settings.codex_bin))
        engine_info = {"cli": ready, "preset": settings.codex_preset,
                       "timeout_s": settings.codex_timeout_s}
    elif settings.provider == "magnific":
        # Ключ не печатаем — только факт наличия.
        ready = bool(settings.magnific_api_key)
        engine_info = {"key": ready, "preset": settings.magnific_preset,
                       "upscale": settings.magnific_upscale}
    else:
        bin_ok = Path(settings.ncnn_bin).exists()
        model_ok = settings.model_param_path().exists()
        ready = bin_ok and model_ok
        engine_info = {"bin": bin_ok, "model": model_ok}
    return {
        "status": "ok" if ready else "degraded",
        "version": app.version,
        "provider": settings.provider,
        "busy": manager.busy,
        "queue_len": manager.queue_len(),
        "gfpgan": False,
        "engine": engine_info,
    }


@app.get("/metrics")
def metrics(_: None = Depends(auth)) -> dict:
    return manager.metrics()


@app.post("/enhance/start")
async def enhance_start(
    _: None = Depends(auth),
    file: UploadFile = File(...),
    target_w: Optional[int] = Form(None),
    target_h: Optional[int] = Form(None),
    scale_cap: float = Form(4.0),
    face_restore: bool = Form(False),
) -> dict:
    job_id = "ej_" + uuid.uuid4().hex
    in_path = settings.work_dir / f"{job_id}_in"
    out_path = settings.work_dir / f"{job_id}_out.jpg"

    # Stream upload to disk with a hard size cap.
    size = 0
    try:
        with open(in_path, "wb") as f:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > settings.max_input_bytes:
                    raise HTTPException(status_code=413, detail="input too large")
                f.write(chunk)
    except HTTPException:
        in_path.unlink(missing_ok=True)
        raise

    # Validate it is a decodable image and within the pixel cap.
    img = cv2.imread(str(in_path), cv2.IMREAD_COLOR)
    if img is None:
        in_path.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="not a decodable image")
    h, w = img.shape[:2]
    if w * h > settings.max_input_px:
        in_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="too many pixels")

    job = Job(
        id=job_id, input_path=str(in_path), output_path=str(out_path),
        target_w=target_w, target_h=target_h, scale_cap=scale_cap, face_restore=face_restore,
    )
    if not manager.submit(job):
        in_path.unlink(missing_ok=True)
        raise HTTPException(status_code=429, detail="queue full, retry later")

    log.info("job %s queued (%dx%d, target=%sx%s)", job_id, w, h, target_w, target_h)
    return {"job_id": job_id}


@app.get("/enhance/status/{job_id}")
def enhance_status(job_id: str, _: None = Depends(auth)) -> dict:
    j = manager.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": j.id,
        "status": j.status,
        "progress": j.progress,
        "message": j.message,
        "width": j.width,
        "height": j.height,
        "error": j.error,
    }


@app.get("/enhance/result/{job_id}")
def enhance_result(job_id: str, _: None = Depends(auth)) -> FileResponse:
    j = manager.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status != "done":
        raise HTTPException(status_code=409, detail=f"not done: {j.status}")
    if not Path(j.output_path).exists():
        raise HTTPException(status_code=410, detail="result expired")
    return FileResponse(j.output_path, media_type="image/jpeg")


@app.on_event("startup")
def _startup() -> None:
    log.info("enhance-service starting: provider=%s work=%s", settings.provider, settings.work_dir)
    if settings.provider == "codex":
        found = shutil.which(settings.codex_bin)
        log.info("codex: bin=%s preset=%s workers=%d timeout=%ss",
                 found or "НЕ НАЙДЕН", settings.codex_preset, settings.workers,
                 settings.codex_timeout_s)
        if not found:
            log.warning("codex CLI не найден — задайте CODEX_BIN (полный путь)")
        return
    if settings.provider == "magnific":
        if not settings.magnific_api_key:
            log.warning("MAGNIFIC_API_KEY missing — jobs will fail")
        log.info("magnific: preset=%s upscale=%s workers=%d",
                 settings.magnific_preset, settings.magnific_upscale, settings.workers)
        # Опрос статуса ест тот же лимит, что и постановка задач. Если конфиг выводит нас
        # за 50 запросов/мин, Magnific начнёт отвечать 429 — лучше узнать это на старте.
        per_job = 1 + (30 / max(1, settings.magnific_poll_s))  # ~30с на генерацию
        per_min = settings.workers * 2 * per_job
        log.info("magnific: оценка нагрузки на API ~%d запросов/мин (лимит 50)", round(per_min))
        if per_min > 45:
            log.warning("magnific: конфиг близок к лимиту API — увеличьте MAGNIFIC_POLL_S "
                        "или уменьшите ENHANCE_WORKERS")
        return
    log.info("engine: bin=%s model=%s", settings.ncnn_bin, settings.model_param_path())
    if not Path(settings.ncnn_bin).exists():
        log.warning("ncnn binary missing — run scripts/download_models.sh")
    if not settings.model_param_path().exists():
        log.warning("model %s missing — run scripts/download_models.sh", settings.model_name)
