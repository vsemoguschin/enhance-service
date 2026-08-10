#!/usr/bin/env python3
"""Smoke-тест Magnific API: апскейл одного фото + замер времени/размера/расхода кредитов.

Отдельный скрипт, а не часть сервиса: цель — проверить ключ и качество до того, как
Magnific станет вторым провайдером движка (см. docs/audits/2026-08-10-magnific-api-test.md).

Только stdlib — venv не нужен.

    python3 scripts/magnific_smoke.py photo.jpg --engine precision-v2 --scale 4
    python3 scripts/magnific_smoke.py photo.jpg --engine creative --scale 4x --creativity -10

Ключ берётся из окружения MAGNIFIC_API_KEY, иначе из .env рядом с проектом.
Ключ нигде не печатается. Результат кладётся в work/magnific/ (в .gitignore — там фото клиентов).
"""
import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "work" / "magnific"

# Документированный потолок Magnific на площадь результата.
MAX_OUTPUT_MP = 25.3


def ssl_context() -> ssl.SSLContext:
    """У python.org-сборок на macOS нет системных корневых сертификатов — берём certifi, если есть."""
    try:
        import certifi  # noqa: PLC0415 — опциональная зависимость

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

# path — куда слать POST; статус читается тем же path + /{task_id}
ENGINES = {
    "precision-v2": "/v1/ai/image-upscaler-precision-v2",
    "precision": "/v1/ai/image-upscaler-precision",
    "creative": "/v1/ai/image-upscaler",
    # Генеративное восстановление: не увеличивает, а перерисовывает кадр по промпту.
    # В вебе владелец пользуется Seedream 5 pro, в API доступен 4.5 — результат может отличаться.
    "seedream-edit": "/v1/ai/text-to-image/seedream-v4-5-edit",
    # Nano Banana (Gemini 2.5 Flash Image). Параметра пропорций нет — кадр берётся от входа,
    # поэтому здесь не бывает растяжения под пресет aspect_ratio.
    "nano-banana": "/v1/ai/gemini-2-5-flash-image-preview",
}

PROMPTS = {
    # Промпт из веб-версии — чтобы сравнение с ней было на равных.
    "web": (
        "подготовь это фото к профессиональной печати, улучши качество максимально без потери "
        "сходства, сделай цветокоррекцию, чтобы фото не было темным, разрешение 4к, сделай так, "
        "чтобы фото было четким и не смазанным. сделай фото четким"
    ),
    # Максимум микродеталей. Запреты на мимику стоят жёстко: агрессивная детализация
    # склонна «оживлять» лицо — в эталоне владельца модель дорисовала женщине улыбку с зубами.
    "detail": (
        "Восстанови это фото и усиль детализацию до уровня студийной съёмки. "
        "Проработай микродетали: текстуру и поры кожи, отдельные волоски и пряди, ресницы, "
        "плетение ткани рубашки, вязку головного убора, листву, траву и архитектуру на фоне. "
        "Высокая резкость, выразительный микроконтраст, глубокие естественные цвета. "
        "ОБЯЗАТЕЛЬНО сохрани узнаваемость: черты лиц, форму носа, глаз и губ, возраст, "
        "выражение и мимику ровно как в оригинале. "
        "НЕ меняй улыбку и положение губ, не открывай рот, не добавляй зубы, не наноси косметику, "
        "не меняй причёску, одежду, позу и композицию кадра."
    ),
    # Под сканы старых отпечатков: царапины, пятна, выцветание, вспышка.
    # Основа — промпт владельца; добавлены защита личности, запрет ретуши и запрет трогать фон
    # (исходная формулировка про «soft background color» разрешала модели переделать фон).
    "old-photo": (
        "Restore and enhance an old damaged photo. Remove scratches, stains, dust and noise. "
        "Reconstruct faded or torn areas while preserving original details. "
        "Slightly sharpen the image for better clarity, but keep it realistic. "
        "Apply natural and era-appropriate colors to skin, hair and clothing; keep background "
        "colors natural and muted. "
        "PRESERVE EXACTLY: facial features, expressions, mouth and eye position, age and identity "
        "of every person, glasses, jewellery, clothing patterns, pose, composition and crop. "
        "Do NOT replace, simplify or repaint the background. "
        "Do NOT retouch or smooth skin, do not remove wrinkles, moles or scars, "
        "do not apply beauty filters or plastic-looking surfaces. "
        "The final result should look like an old photo that has been realistically restored "
        "and colorized, while respecting its original appearance."
    ),
    # Против «пластика»: модели по умолчанию сглаживают кожу, и на печати это видно сразу.
    # Запреты работают заметно лучше просьб, поэтому здесь их больше, чем пожеланий.
    "texture": (
        "Восстанови это фото для печати, сохранив фотографическую фактуру. "
        "Сохрани и подчеркни микротекстуру: поры и рельеф кожи, отдельные волоски и пряди, "
        "ворс и плетение ткани, вязку головного убора, листву и траву. "
        "НЕ сглаживай кожу, НЕ применяй бьюти-ретушь, размытие и шумоподавление, "
        "НЕ создавай пластиковых и восковых поверхностей, не убирай морщины и родинки. "
        "Сохрани естественное зерно и микроконтраст, резкость как у съёмки на хороший объектив. "
        "Черты лиц, мимику, очки, причёску и одежду оставь без изменений."
    ),
    # Под фотокнигу: главное — сходство лиц, поэтому запреты сформулированы явно.
    "restore": (
        "Восстанови это фото для печати в фотокниге. Убери размытие, шум и артефакты сжатия. "
        "Верни естественную детализацию кожи, волос, ткани и фона. "
        "СОХРАНИ без изменений черты лиц, пропорции, выражения, возраст и все детали одежды "
        "и аксессуаров. Сохрани композицию кадра, естественный свет и цвета сцены. "
        "Резкость естественная, без пластиковой кожи, без перерисовки лиц, без добавления "
        "новых объектов и без косметической ретуши."
    ),
}


def load_env_key(name: str) -> str:
    """Значение из окружения, иначе из .env (простой KEY=VALUE парсер, без зависимостей)."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == name:
            return v.strip().strip('"').strip("'")
    return ""


def estimate_credits(engine: str, out_mp: float) -> str:
    """Прикидка расхода по замерам кабинета: апскейлеры тарифицируются по площади результата.

    Замеры: precision 3 Мп ≈ 90, 14.7 Мп ≈ 270; creative 7.8 Мп ≈ 200, 14.7 Мп ≈ 400;
    seedream и nano-banana — по 50 независимо от размера.
    """
    if engine in ("seedream-edit", "nano-banana"):
        return "~50 кредитов"
    per_mp = 26 if engine == "creative" else 18
    return f"~{round(out_mp * per_mp / 10) * 10} кредитов (оценка)"


def image_dims(path: Path) -> tuple[int, int] | None:
    """WxH через Pillow, если он есть."""
    try:
        from PIL import Image  # noqa: PLC0415 — опциональная зависимость
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None


def image_size(path: Path) -> str:
    dims = image_dims(path)
    if not dims:
        return "?"
    w, h = dims
    return f"{w}x{h} ({w * h / 1e6:.1f} Мп)"


def make_downscaled(path: Path, long_side: int) -> Path:
    """Уменьшенная копия — эмуляция типичного low-DPI кадра клиента.

    Оригинал при этом становится эталоном: видно, насколько апскейл к нему приблизился.
    """
    try:
        from PIL import Image  # noqa: PLC0415 — опциональная зависимость
    except ImportError:
        raise SystemExit("--downscale требует Pillow (pip install pillow)") from None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{path.stem}_src{long_side}.jpg"
    with Image.open(path) as im:
        im = im.convert("RGB")
        ratio = long_side / max(im.width, im.height)
        im.resize((round(im.width * ratio), round(im.height * ratio)), Image.LANCZOS).save(
            out_path, quality=90
        )
    return out_path


def api_call(url: str, api_key: str, payload: dict | None = None) -> tuple[dict, dict]:
    """POST (payload) или GET (payload=None). Возвращает (body, headers)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("x-magnific-api-key", api_key)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as resp:
            return json.loads(resp.read().decode()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise SystemExit(f"HTTP {e.code} от {url.split('?')[0]}\n{body}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"сеть недоступна: {e.reason}") from e


def show_quota_headers(headers: dict, label: str) -> None:
    """Печатает заголовки про кредиты/лимиты — по ним считаем реальную стоимость вызова."""
    interesting = {
        k: v
        for k, v in headers.items()
        if any(word in k.lower() for word in ("credit", "quota", "ratelimit", "remaining"))
    }
    if interesting:
        print(f"  {label}: " + ", ".join(f"{k}={v}" for k, v in sorted(interesting.items())))


def build_payload(args: argparse.Namespace, image_b64: str) -> dict:
    """Тела запросов у creative и precision разные — общий только image."""
    if args.engine == "nano-banana":
        return {
            "prompt": args.prompt or PROMPTS[args.preset],
            "reference_images": [image_b64],
        }

    if args.engine == "seedream-edit":
        payload = {
            "prompt": args.prompt or PROMPTS[args.preset],
            "reference_images": [image_b64],
            "aspect_ratio": args.aspect_ratio,
        }
        if args.seed is not None:
            payload["seed"] = args.seed
        return payload

    if args.engine == "creative":
        payload = {
            "image": image_b64,
            "scale_factor": args.scale if "x" in str(args.scale) else f"{args.scale}x",
            "optimized_for": args.optimized_for,
            "creativity": args.creativity,
            "hdr": args.hdr,
            "resemblance": args.resemblance,
            "fractality": args.fractality,
            "engine": args.magnific_engine,
        }
        if args.prompt:
            payload["prompt"] = args.prompt
        return payload

    return {
        "image": image_b64,
        "scale_factor": int(str(args.scale).rstrip("x")),
        "sharpen": args.sharpen,
        "smart_grain": args.smart_grain,
        "ultra_detail": args.ultra_detail,
        "flavor": args.flavor,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke-тест Magnific upscaler")
    p.add_argument("image", type=Path, help="путь к исходному фото")
    p.add_argument("--engine", choices=sorted(ENGINES), default=os.getenv("MAGNIFIC_ENGINE", "precision-v2"))
    p.add_argument("--scale", default="4", help="2/4/8/16 (creative принимает и '4x')")
    p.add_argument(
        "--downscale",
        type=int,
        default=0,
        help="сначала уменьшить фото до N px по длинной стороне и апскейлить уже копию; "
        "оригинал остаётся эталоном для сравнения (например: --downscale 1000 --scale 4)",
    )
    p.add_argument("--timeout", type=int, default=int(os.getenv("MAGNIFIC_TIMEOUT_S", "300")))
    p.add_argument("--poll", type=float, default=3.0, help="интервал опроса статуса, сек")
    p.add_argument("--verbose", action="store_true", help="печатать полные ответы API (без base64)")
    # precision
    p.add_argument("--flavor", choices=["sublime", "photo", "photo_denoiser"], default="photo")
    p.add_argument("--sharpen", type=int, default=7)
    p.add_argument("--smart-grain", type=int, default=7, dest="smart_grain")
    p.add_argument("--ultra-detail", type=int, default=30, dest="ultra_detail")
    # creative
    p.add_argument("--optimized-for", default="films_n_photography", dest="optimized_for")
    p.add_argument("--creativity", type=int, default=-10, help="-10 = минимум отсебятины")
    p.add_argument("--hdr", type=int, default=0)
    p.add_argument("--resemblance", type=int, default=5, help="+ = ближе к оригиналу")
    p.add_argument("--fractality", type=int, default=0)
    p.add_argument(
        "--magnific-engine",
        dest="magnific_engine",
        choices=["automatic", "magnific_illusio", "magnific_sharpy", "magnific_sparkle"],
        default="magnific_sharpy",
        help="движок creative: sharpy = резкость без выдумывания деталей",
    )
    p.add_argument("--prompt", default="", help="для seedream-edit; пусто = промпт пресета")
    p.add_argument(
        "--preset",
        choices=sorted(PROMPTS),
        default="restore",
        help="готовый промпт: restore (фотокнига, сходство лиц) | web (как в веб-версии)",
    )
    p.add_argument(
        "--aspect-ratio",
        default="traditional_3_4",
        dest="aspect_ratio",
        help="seedream-edit: square_1_1 / traditional_3_4 / classic_4_3 / portrait_2_3 / standard_3_2 …",
    )
    p.add_argument("--seed", type=int, default=None, help="seedream-edit: фиксирует результат")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    api_key = load_env_key("MAGNIFIC_API_KEY")
    if not api_key:
        return print("MAGNIFIC_API_KEY не задан (окружение или .env рядом с проектом)") or 2

    base_url = (os.getenv("MAGNIFIC_BASE_URL") or "https://api.magnific.com").rstrip("/")
    endpoint = base_url + ENGINES[args.engine]

    if args.downscale:
        original = args.image
        args.image = make_downscaled(original, args.downscale)
        print(f"эталон:  {original.name} — {image_size(original)} (с ним сравниваем результат)")

    src_bytes = args.image.read_bytes()

    print(f"вход:    {args.image.name} — {len(src_bytes) / 1024:.0f} КБ, {image_size(args.image)}")
    print(f"движок:  {args.engine}, scale={args.scale}")

    # Считаем площадь результата до отправки: превышение лимита = 400 и сожжённое время.
    # У seedream-edit масштаба нет — разрешение выхода выбирает модель (до 4 Мп).
    dims = image_dims(args.image)
    scale_num = float(str(args.scale).rstrip("x"))
    if args.engine in ("seedream-edit", "nano-banana"):
        print(f"промпт:  [{args.preset}] {(args.prompt or PROMPTS[args.preset])[:70]}…")
        ratio = args.aspect_ratio if args.engine == "seedream-edit" else "как у входа"
        print(f"формат:  {ratio}, разрешение выхода задаёт модель · {estimate_credits(args.engine, 0)}")
    elif dims:
        w, h = dims
        out_mp = w * h * scale_num**2 / 1e6
        print(f"печать:  оригинал при 300 DPI — {w / 300 * 2.54:.0f}x{h / 300 * 2.54:.0f} см")
        print(
            f"выход:   ~{int(w * scale_num)}x{int(h * scale_num)} ({out_mp:.1f} Мп)"
            f" · {estimate_credits(args.engine, out_mp)}"
        )
        if out_mp > MAX_OUTPUT_MP:
            max_scale = (MAX_OUTPUT_MP * 1e6 / (w * h)) ** 0.5
            print(
                f"\nСТОП: лимит Magnific — {MAX_OUTPUT_MP} Мп на результат, здесь {out_mp:.1f} Мп.\n"
                f"Максимум для этого фото: --scale {max_scale:.2f}"
                + ("  (то есть апскейл ему уже не нужен)" if max_scale < 1.5 else "")
            )
            return 2

    started = time.monotonic()
    body, headers = api_call(endpoint, api_key, build_payload(args, base64.b64encode(src_bytes).decode()))
    task_id = (body.get("data") or {}).get("task_id")
    if not task_id:
        return print(f"нет task_id в ответе: {json.dumps(body)[:300]}") or 1
    print(f"task_id: {task_id} (принят за {time.monotonic() - started:.1f}с)")
    if args.verbose:
        print(f"  ответ POST: {json.dumps(body, ensure_ascii=False)[:600]}")
    show_quota_headers(headers, "квоты после POST")

    deadline = started + args.timeout
    status, generated = "", []
    while time.monotonic() < deadline:
        time.sleep(args.poll)
        body, headers = api_call(f"{endpoint}/{task_id}", api_key)
        data = body.get("data") or {}
        new_status = data.get("status", "")
        if new_status != status:
            status = new_status
            print(f"  {time.monotonic() - started:6.1f}с  {status}")
        if status == "COMPLETED":
            generated = data.get("generated") or []
            break
        if status == "FAILED":
            return print(f"задача упала: {json.dumps(body)[:300]}") or 1
    else:
        return print(f"таймаут {args.timeout}с, последний статус: {status or 'нет'}") or 1

    elapsed = time.monotonic() - started
    show_quota_headers(headers, "квоты после COMPLETED")
    if args.verbose:
        print(f"  ответ COMPLETED: {json.dumps(body, ensure_ascii=False)[:600]}")
    if not generated:
        return print(f"COMPLETED, но пустой generated[]: {json.dumps(body, ensure_ascii=False)[:400]}") or 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (
        f"{args.engine}-{args.preset}"
        if args.engine in ("seedream-edit", "nano-banana")
        else f"{args.engine}_x{str(args.scale).rstrip('x')}"
    )
    out_path = OUT_DIR / f"{args.image.stem}_{suffix}.jpg"
    with urllib.request.urlopen(generated[0], timeout=300, context=ssl_context()) as resp:
        out_path.write_bytes(resp.read())

    print(f"\nготово за {elapsed:.1f}с")
    print(f"выход:   {out_path} — {out_path.stat().st_size / 1024 / 1024:.2f} МБ, {image_size(out_path)}")
    print(f"рост:    {out_path.stat().st_size / len(src_bytes):.1f}× по весу файла")
    if out_path.stat().st_size > 20 * 1024 * 1024:
        print("ВНИМАНИЕ: >20 МБ — file-platform такой файл не примет, нужен downscale/пережатие")
    return 0


if __name__ == "__main__":
    sys.exit(main())
