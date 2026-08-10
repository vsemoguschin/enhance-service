"""Runtime config from env (see .env.example)."""
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() not in ("false", "0", "no", "")


class Settings:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent

        # Auth: empty = disabled (dev only). In prod set a real secret.
        self.api_key = os.getenv("ENHANCE_API_KEY", "")

        # Провайдер движка: local (ncnn Real-ESRGAN на своём железе) | magnific (внешний API).
        # На боксе без GPU рабочий вариант — magnific: компьют уходит наружу.
        self.provider = os.getenv("ENHANCE_PROVIDER", "local").strip().lower()

        # Magnific (значения — только из окружения, в git не попадают)
        self.magnific_api_key = os.getenv("MAGNIFIC_API_KEY", "").strip()
        self.magnific_base_url = os.getenv("MAGNIFIC_BASE_URL", "https://api.magnific.com").strip()
        self.magnific_preset = os.getenv("MAGNIFIC_PRESET", "texture").strip()
        self.magnific_preset_fallback = "texture"
        # Меньше, чем ENHANCE_MAX_WAIT_MS у book-editor (180с): сервис должен сдаваться раньше
        # клиента, иначе кредиты тратятся на результат, который уже некому забрать.
        self.magnific_timeout_s = _int("MAGNIFIC_TIMEOUT_S", 150)
        # Опрос статуса тоже расходует rate limit Magnific (50 запросов/мин на ключ),
        # поэтому интервал влияет на предельное число воркеров — см. self.workers.
        # 8с: 4 воркера × 2 задачи/мин × (1 POST + ~4 опроса) ≈ 40 запросов/мин, запас к лимиту 20%.
        self.magnific_poll_s = _int("MAGNIFIC_POLL_S", 8)
        # Апскейл тарифицируется по площади результата — по умолчанию выключен.
        self.magnific_upscale = _bool("MAGNIFIC_UPSCALE", False)
        self.magnific_autocontrast = _bool("MAGNIFIC_AUTOCONTRAST", True)
        # Порог расхождения пропорции с ближайшим пресетом Seedream, выше которого генерация
        # запрещена. 0.20 подобран по фактам: селфи 1.17 (расхождение 14%) отработало отлично,
        # а сломалась панорама 2.22 — её отсекает отдельная проверка на экстремальный формат,
        # см. _generative_is_safe. То есть решает не расхождение, а сама ширина кадра.
        self.magnific_max_aspect_drift = float(os.getenv("MAGNIFIC_MAX_ASPECT_DRIFT", "0.20"))

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

        # Сколько джобов считается одновременно.
        # local: 1 — ncnn упирается в CPU бокса, параллель только замедлит и задушит соседей.
        # magnific: 4 — воркер почти всё время ждёт сеть, но упирается в rate limit Magnific
        # (50 запросов/мин на ключ). Одна задача ≈ 1 POST + ~5 опросов за ~30с, то есть
        # 4 воркера ≈ 48 запросов/мин. Больше — и API начнёт отвечать нам 429.
        self.workers = _int("ENHANCE_WORKERS", 4 if self.provider == "magnific" else 1)
        self.job_timeout_s = _int("ENHANCE_JOB_TIMEOUT_S", 660)
        self.jpeg_quality = _int("ENHANCE_JPEG_QUALITY", 95)
        self.result_ttl_s = _int("ENHANCE_RESULT_TTL_S", 3600)

        self.work_dir.mkdir(parents=True, exist_ok=True)

    def model_param_path(self) -> Path:
        return self.model_dir / f"{self.model_name}.param"


settings = Settings()
