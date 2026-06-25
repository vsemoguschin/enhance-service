# Журнал сборки enhance-service

## 2026-06-25 — MVP-движок (контракт C1) написан и проверен локально

### Сделано
- Каркас сервиса (Python/FastAPI, без torch):
  - `app/config.py` — конфиг из env (auth, движок, лимиты).
  - `app/engine.py` — обёртка `realesrgan-ncnn-vulkan`: x4 → resize cover-crop под target
    (или дефолт x2), unsharp, JPEG с капом ≤20 МБ.
  - `app/queue.py` — single-worker bounded queue, job-store, метрики, TTL-уборка.
  - `app/main.py` — эндпоинты C1: `/enhance/start`, `/status/{id}`, `/result/{id}`, `/health`,
    `/metrics`; auth `X-Enhance-Api-Key`.
  - `requirements.txt`, `.env.example`, `.gitignore`, `scripts/download_models.sh`.

### Smoke-тест (локально, macOS, venv) — PASS
- `/health` → ok (bin+model видны).
- Target-aware: вход 480×640, `target=1500×2000` → выход **ровно 1500×2000** (валидный JPEG).
- Дефолт x2: вход 426×640 → **852×1280**.
- `/metrics`: done 2, error 0, avg 20.7с, p95 17.5с (CPU без GPU — прокси codex).
- Запуск: `ENHANCE_WORK_DIR=./work .venv/bin/uvicorn app.main:app --port 8011`.

### Известные ограничения / TODO
- **Прогресс грубый:** ncnn буферизует stderr через pipe → прогресс держится 10, потом 100
  (на UI это «обрабатывается»). Опционально — тайм-тикер оценки прогресса.
- **Восстановление лиц** не реализовано: флаг `face_restore` принимается, но no-op (MVP).
  Позже — gfpgan-ncnn или отдельный torch-воркер.
- **Второй проход** для target >×4 не делаем (кап ×4); для low-DPI редко нужно.
- Безвредный warning libpng «chunk data is too large» при чтении ncnn-PNG (на результат не влияет).
- Тестов (unit/integration) пока нет.

### Деплой на codex (когда дойдём)
- `PLATFORM=ubuntu bash scripts/download_models.sh`.
- **Vulkan ICD обязателен** на CPU-VM без GPU: `apt-get install -y libvulkan1 mesa-vulkan-drivers`
  (lavapipe). **Проверить до деплоя** (`vulkaninfo --summary`), иначе бинарь упадёт «no vulkan device».
- pm2 с `max_memory_restart` + `nice`; задать `ENHANCE_API_KEY`.

### Локальные dev-артефакты (в .gitignore, не коммитятся)
`.venv/`, `bin/`, `models/`, `work/`, `pip-tmp/` — для локального прогона; на сервере ставятся заново.

### Статус контракта
**C1 реализован и подтверждён.** Готово к интеграции из book-editor (C3) и к параллельной ветке B
(file-platform intake). Полноценный async с поллингом совпал с контрактом без изменений.

## 2026-06-25 (по итогам ручного теста из book-editor)

- **413 на реальном фото** — пороги входа были тесны (low-DPI фото на крупной странице бывает
  много-мегапиксельным). Поднял дефолты: `MAX_INPUT_MP 24→80`, `MAX_INPUT_MB 20→60` (≥ лимита
  EditorAsset 50МБ в book-editor). Требует **рестарта** сервиса (uvicorn без --reload).
- **Защита от раздувания ncnn:** ncnn-x4plus всегда даёт ×4 — на крупном входе промежуток мог
  достигать сотен Мпикс (OOM/медленно). Движок теперь капит вход ncnn до `MAX_OUTPUT_PX/4`
  (x4 ≤ MAX_OUTPUT_PX). Мелкие low-DPI фото (типовой кейс) не затронуты.
