# enhance-service — архитектура

## Статус
Реализовано, задеплоено на codex (2026-06-24). В book-editor фича выключена флагом `ENHANCE_ENABLED=false`
(на codex нет GPU — детали в [operations.md](operations.md)).

## Роль
AI-апскейл одного изображения. «Тупой» движок: байты на вход → улучшенные байты на выход.
Оркестрация (очередь, квоты, fair-share, DPI-гейт, UI) — на стороне book-editor (контракт C3).
Хранение результата — file-platform как вариант `enhanced` (контракт C2).

## API (контракт C1)
- `POST /enhance/start` — multipart `file` (+ опц. `target_w`, `target_h`, `scale_cap`, `face_restore`) → `{ "job_id": "ej_..." }`
- `GET /enhance/status/{job_id}` → `{ status: queued|processing|done|error, progress, width, height, error }`
- `GET /enhance/result/{job_id}` → `image/jpeg`
- `GET /health` — без авторизации; `GET /metrics` — с ключом
- Авторизация: заголовок `X-Enhance-Api-Key`. Если `ENHANCE_API_KEY` пуст — авторизация выключена (dev).
- Точные форматы — [contracts.md](contracts.md).

## Движок (`app/engine.py`)
- **realesrgan-ncnn-vulkan** (бинарь, без torch) — модель `realesrgan-x4plus`, всегда x4. Выбор обоснован в
  [план.md](план.md)/[задача.md](задача.md): легче и быстрее на слабом боксе, без враждебного torch-стека.
- **EXIF (гибрид fast/slow):** дешёвый peek тега Orientation; если нет/`=1` → быстрый `cv2.imread`; иначе
  PIL `exif_transpose` (пиксели физически выпрямляются, чтобы совпадало с тем, как фото показывает браузер).
- **Aspect-preserving:** апскейл под нужный размер **без cover-crop** — результат drop-in замена оригинала,
  кроп/раскладку оставляет редактор.
- **Target-aware масштаб:** `needed = max(target_w/orig_w, target_h/orig_h)`, кап `scale_cap` (деф. 4) и `max_output_px`.
  При `needed ≤ 1.05` — passthrough (не апскейлим, только EXIF-нормализация), не уменьшаем.
- **Бюджет ncnn:** вход пред-ужимается до `max_output_px/4` (иначе OOM/тормоз), ncnn даёт x4, затем resize до
  целевого + лёгкий unsharp.
- **Выход:** JPEG, кап `≤ max_output_bytes` (20 МБ) — сначала снижением качества, в крайнем случае даунскейлом.
- `face_restore` — заглушка (MVP без восстановления лиц; gfpgan-ncnn — отдельный шаг).

## Очередь и нагрузочная защита (`app/queue.py`)
- **Один воркер, bounded queue** (`QUEUE_MAX=20`). Сверх лимита — `429`.
- Это hard-backstop; основная сериализация/квоты/дедуп — на стороне book-editor (C3).
- ⚠️ Известный gap: в `_run_ncnn` нет таймаута на subprocess — зависший ncnn займёт единственный воркер
  навсегда (`ENHANCE_JOB_TIMEOUT_S` в конфиге к самому ncnn не применяется). TODO: timeout + kill.

## Конфиг (`app/config.py`, env)
- `ENHANCE_API_KEY`, `ENHANCE_NCNN_BIN` (`./bin/realesrgan-ncnn-vulkan`), `ENHANCE_MODEL_DIR` (`./models`),
  `ENHANCE_MODEL_NAME` (`realesrgan-x4plus`), `ENHANCE_NCNN_TILE` (`0`=auto), `ENHANCE_WORK_DIR` (`/tmp/enhance`).
- Лимиты: `MAX_INPUT_MP=80`, `MAX_INPUT_MB=60`, `MAX_OUTPUT_PX=7200`, `MAX_OUTPUT_MB=20`, `QUEUE_MAX=20`,
  `JOB_TIMEOUT_S=660`, `RESULT_TTL_S=3600`, `JPEG_QUALITY=95`.

## Контракты с другими сервисами
- **C1** (этот сервис) ← вызывает book-editor.
- **C2:** book-editor → file-platform `POST /internal/images/:id/variants/enhanced` (сохранение как вариант `enhanced`).
- **C3:** оркестрация в book-editor — DPI-гейт (<200), feature flag, дедуп, лимит одновременных.

## Связано
- [operations.md](operations.md) — рантайм на codex, перф, GPU/API-решение, egress-замок.
- [deploy-runbook.md](deploy-runbook.md) — пошаговый деплой 3 сервисов.
- [contracts.md](contracts.md) — форматы C1.
