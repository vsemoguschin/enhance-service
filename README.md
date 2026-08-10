# enhance-service

AI-улучшение (апскейл) изображений для EASY-CRM. Отдельный сервис («тупой» движок):
картинка на вход → улучшенная на выход. Деплой на **codex**-бокс. Используется
book-editor'ом через async-API (контракт **C1**).

**Статус:** задеплоено на codex. С 2026-08-10 у сервиса **два провайдера**:

- `ENHANCE_PROVIDER=local` — ncnn Real-ESRGAN на своём железе. Без GPU непригоден
  (минуты на фото, см. [docs/operations.md](docs/operations.md));
- `ENHANCE_PROVIDER=magnific` — внешний API (Seedream 4.5 edit). Компьют уходит наружу,
  **боксу GPU не нужен**: ~25 с и 4.6 ₽ за фото, выход ~4 Мп. Контракт C1 не меняется —
  book-editor работает с обоими одинаково. Замеры и выбор промпта —
  [docs/audits/2026-08-10-magnific-api-test.md](docs/audits/2026-08-10-magnific-api-test.md).

Это снимает причину, по которой фича была выключена в book-editor (`ENHANCE_ENABLED=false`).

## Роль в системе

Один из трёх компонентов фичи (детали — в [docs/план.md](docs/план.md)):
- **enhance-service** (этот репозиторий) — AI-движок, контракт **C1** ([docs/contracts.md](docs/contracts.md)).
- **file-platform** — хранит результат как вариант `enhanced` (контракт C2).
- **book-editor** — оркестрация (очередь, fair-share, квоты, DPI-гейт) + UI (контракт C3).

Узкое место (codex) сериализуется воркером book-editor; здесь — hard-backstop (один воркер,
bounded queue, лимиты входа).

## Подход к реализации (из Этапа 0)

- Движок — **realesrgan-ncnn-vulkan** (бинарь, без torch): легче и быстрее на слабом боксе.
  В Этапе 0 подтверждено качество и скорость (~16–19с/фото на CPU). Тяжёлый Python/torch-стек
  (basicsr/gfpgan) — НЕ берём в прод (враждебная установка, см. [docs/задача.md](docs/задача.md)).
- API — FastAPI: `POST /enhance/start` → `GET /enhance/status/{id}` → `GET /enhance/result/{id}`,
  плюс `/health`, `/metrics`. Точные форматы — [docs/contracts.md](docs/contracts.md) (C1).
- Target-aware масштаб (x2/x4 под размер ячейки), выход капится ≤20 МБ.
- Восстановление лиц (opt-in) — **следующий шаг** (нужен gfpgan-ncnn или отдельный torch-воркер);
  MVP — только Real-ESRGAN.

## Структура

```
enhance-service/
├── README.md
├── .gitignore
├── requirements.txt          # fastapi, uvicorn, opencv-python-headless, numpy, pillow
├── app/
│   ├── main.py               # FastAPI, эндпоинты C1
│   ├── engine.py             # обёртка realesrgan-ncnn-vulkan (subprocess) + target/resize/unsharp
│   ├── queue.py              # single-worker bounded queue, job-store (in-memory)
│   ├── config.py             # env: ENHANCE_API_KEY, ENHANCE_MODEL_DIR, MAX_OUTPUT_PX, лимиты
│   └── faces.py              # (позже) opt-in восстановление лиц
├── scripts/
│   ├── download_models.sh    # скачать ncnn-бинарь + модели (не в git)
│   └── (pm2/ecosystem)       # запуск на codex
└── docs/                     # планирование + контракты + eval
```

## Запуск

Локально (macOS, dev):
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PLATFORM=macos bash scripts/download_models.sh          # bin/ + models/ (не в git)
ENHANCE_WORK_DIR=./work .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011
# проверка: curl -s localhost:8011/health
```

Деплой на codex (Linux): `PLATFORM=ubuntu bash scripts/download_models.sh`, pm2 с
`max_memory_restart` + `nice`, задать `ENHANCE_API_KEY`. **Важно:** на CPU-VM без GPU нужен
Vulkan ICD (`apt-get install -y libvulkan1 mesa-vulkan-drivers`) — проверить до деплоя.
Подробности и known-limitations — [docs/build-log.md](docs/build-log.md).

## Документация

- [docs/architecture.md](docs/architecture.md) — архитектура: C1, движок (EXIF/aspect/ncnn), очередь, конфиг.
- [docs/operations.md](docs/operations.md) — эксплуатация на codex: топология, перф/GPU-решение, egress-замок, мониторинг.
- [docs/deploy-runbook.md](docs/deploy-runbook.md) — пошаговый деплой 3 сервисов.
- [docs/задача.md](docs/задача.md) — контекст, решения, метрики, результаты Этапа 0.
- [docs/план.md](docs/план.md) — архитектура, pipeline, лимиты, этапы, индекс веток.
- [docs/contracts.md](docs/contracts.md) — контракты C1/C2/C3.
- [docs/eval-stage0/](docs/eval-stage0/) — проверка качества (before/after, скрипты).
