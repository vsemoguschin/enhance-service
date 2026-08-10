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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "work" / "magnific"

# path — куда слать POST; статус читается тем же path + /{task_id}
ENGINES = {
    "precision-v2": "/v1/ai/image-upscaler-precision-v2",
    "precision": "/v1/ai/image-upscaler-precision",
    "creative": "/v1/ai/image-upscaler",
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


def image_size(path: Path) -> str:
    """WxH через Pillow, если он есть; иначе пусто — размер не критичен для теста."""
    try:
        from PIL import Image  # noqa: PLC0415 — опциональная зависимость
    except ImportError:
        return "?"
    try:
        with Image.open(path) as im:
            return f"{im.width}x{im.height} ({im.width * im.height / 1e6:.1f} Мп)"
    except Exception:
        return "?"


def api_call(url: str, api_key: str, payload: dict | None = None) -> tuple[dict, dict]:
    """POST (payload) или GET (payload=None). Возвращает (body, headers)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("x-magnific-api-key", api_key)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
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
    if args.engine == "creative":
        payload = {
            "image": image_b64,
            "scale_factor": args.scale if "x" in str(args.scale) else f"{args.scale}x",
            "optimized_for": args.optimized_for,
            "creativity": args.creativity,
            "hdr": args.hdr,
            "resemblance": args.resemblance,
            "fractality": args.fractality,
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
    p.add_argument("--timeout", type=int, default=int(os.getenv("MAGNIFIC_TIMEOUT_S", "300")))
    p.add_argument("--poll", type=float, default=3.0, help="интервал опроса статуса, сек")
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
    p.add_argument("--prompt", default="")
    args = p.parse_args()

    if not args.image.is_file():
        return print(f"нет файла: {args.image}") or 2

    api_key = load_env_key("MAGNIFIC_API_KEY")
    if not api_key:
        return print("MAGNIFIC_API_KEY не задан (окружение или .env рядом с проектом)") or 2

    base_url = (os.getenv("MAGNIFIC_BASE_URL") or "https://api.magnific.com").rstrip("/")
    endpoint = base_url + ENGINES[args.engine]
    src_bytes = args.image.read_bytes()

    print(f"вход:    {args.image.name} — {len(src_bytes) / 1024:.0f} КБ, {image_size(args.image)}")
    print(f"движок:  {args.engine}, scale={args.scale}")

    started = time.monotonic()
    body, headers = api_call(endpoint, api_key, build_payload(args, base64.b64encode(src_bytes).decode()))
    task_id = (body.get("data") or {}).get("task_id")
    if not task_id:
        return print(f"нет task_id в ответе: {json.dumps(body)[:300]}") or 1
    print(f"task_id: {task_id} (принят за {time.monotonic() - started:.1f}с)")
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
    if not generated:
        return print("COMPLETED, но пустой generated[]") or 1

    show_quota_headers(headers, "квоты после COMPLETED")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.image.stem}_{args.engine}_x{str(args.scale).rstrip('x')}.jpg"
    with urllib.request.urlopen(generated[0], timeout=300) as resp:
        out_path.write_bytes(resp.read())

    print(f"\nготово за {elapsed:.1f}с")
    print(f"выход:   {out_path} — {out_path.stat().st_size / 1024 / 1024:.2f} МБ, {image_size(out_path)}")
    print(f"рост:    {out_path.stat().st_size / len(src_bytes):.1f}× по весу файла")
    if out_path.stat().st_size > 20 * 1024 * 1024:
        print("ВНИМАНИЕ: >20 МБ — file-platform такой файл не примет, нужен downscale/пережатие")
    return 0


if __name__ == "__main__":
    sys.exit(main())
