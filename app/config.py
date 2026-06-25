"""Runtime config from env (see .env.example)."""
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent

        # Auth: empty = disabled (dev only). In prod set a real secret.
        self.api_key = os.getenv("ENHANCE_API_KEY", "")

        # Engine
        self.ncnn_bin = os.getenv("ENHANCE_NCNN_BIN", str(base / "bin" / "realesrgan-ncnn-vulkan"))
        self.model_dir = Path(os.getenv("ENHANCE_MODEL_DIR", str(base / "models")))
        self.model_name = os.getenv("ENHANCE_MODEL_NAME", "realesrgan-x4plus")
        self.ncnn_tile = _int("ENHANCE_NCNN_TILE", 0)  # 0 = auto

        # Work dir
        self.work_dir = Path(os.getenv("ENHANCE_WORK_DIR", "/tmp/enhance"))

        # Limits
        self.max_output_px = _int("ENHANCE_MAX_OUTPUT_PX", 7200)
        self.max_output_bytes = _int("ENHANCE_MAX_OUTPUT_MB", 20) * 1024 * 1024
        self.max_input_px = _int("ENHANCE_MAX_INPUT_MP", 80) * 1_000_000
        self.max_input_bytes = _int("ENHANCE_MAX_INPUT_MB", 60) * 1024 * 1024
        self.queue_max = _int("ENHANCE_QUEUE_MAX", 20)
        self.job_timeout_s = _int("ENHANCE_JOB_TIMEOUT_S", 660)
        self.jpeg_quality = _int("ENHANCE_JPEG_QUALITY", 95)
        self.result_ttl_s = _int("ENHANCE_RESULT_TTL_S", 3600)

        self.work_dir.mkdir(parents=True, exist_ok=True)

    def model_param_path(self) -> Path:
        return self.model_dir / f"{self.model_name}.param"


settings = Settings()
