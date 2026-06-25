# Контракты: AI-улучшение изображений (MVP)

**Дата:** 2026-06-25 · **Статус:** спецификация (не код) · См. [план.md](план.md), [задача.md](задача.md)

Это «контракты на бумаге» из §6/§10 плана: точные интерфейсы между сервисами, чтобы A
(enhance-service) и B (file-platform) можно было делать параллельно, а C (book-editor) — интегрировать.
Изменения здесь — до старта веток; при реализации каждый сервис копирует свой контракт в свой audit-док.

## Карта потока и точки контрактов

```
FE book-editor ──C3.FE──► book-editor BE (EnhanceJob) ──┐
                                                        │ worker (fair-share, concurrency=1)
        file-platform ◄─C3.read── GET content?variant=original
        codex enhance ◄─C1────── POST /enhance/start (bytes+target) → poll → GET result
        file-platform ◄─C2────── POST internal variant-intake (enhanced bytes)
        book-editor BE ◄──────── FINALIZE: photo.variants.enhanced=url, job=done
```

Узкое место (codex) сериализуется воркером book-editor (concurrency=1..2). Все лимиты — в C3 (book-editor),
hard-backstop — в C1 (enhance-service). Запись в file-platform редкая (компьют-bound) → throttle не задеваем.

---

## C1. enhance-service (codex) — HTTP API

**Назначение:** «тупой» AI-движок. Один клиент — worker book-editor. Stateless (джобы в памяти, состояние держит book-editor).
**Транспорт:** HTTP по внутренней сети (codex VPN). **Auth:** заголовок `X-Enhance-Api-Key: <ENHANCE_API_KEY>` (общий секрет).
**Движок:** ncnn Real-ESRGAN (Этап 0); лица (GFPGAN/CodeFormer) — опционально по флагу.

### POST /enhance/start
Body: `multipart/form-data`
- `file` — байты оригинала (обяз.)
- `target_w`, `target_h` — целевой размер в px (размер слота при 300 DPI). Опц.; нет → дефолт ×2.
- `scale_cap` — макс. кратность апскейла (дефолт 4). Защита компьюта/размера.
- `face_restore` — `"true"|"false"` (дефолт false).

Ответ `200`: `{ "job_id": "ej_..." }`
Ошибки: `401` (ключ), `413` (вход > лимита px/МБ), `415/422` (не картинка/битая), `429` (очередь полна/занят — backstop), `503` (модель недоступна).

### GET /enhance/status/{job_id}
`200`: `{ "job_id", "status": "pending|processing|done|error", "progress": 0..100, "message": "...", "width": null|int, "height": null|int }`
`404` — неизвестный job.

### GET /enhance/result/{job_id}
`200`: тело — `image/jpeg` (улучшенное). **Выход капится ≤20 МБ** (лимит файла file-platform): движок ограничивает разрешение (`MAX_OUTPUT_PX`) и JPEG-качество.
Ошибки: `404` (нет job), `409` (status≠done).

### GET /health, GET /metrics
`/health`: `{ "status":"ok", "version", "busy":bool, "queue_len":int, "gfpgan":bool }`
`/metrics`: глубина очереди, jobs total/ok/err, avg/p95 time, активный job (JSON или Prometheus).

**Лимиты/инварианты C1:** один воркер (обрабатывается 1 джоб); bounded queue (сверх — `429`); вход ≤ ~24 Мпикс / ≤ ~20 МБ;
job timeout (≈660с); TTL temp-файлов; target-aware масштаб (выбор x2/x4/проходов внутри). pm2 `max_memory_restart`, `nice`.

---

## C2. file-platform — internal приём варианта `enhanced`

**Назначение:** принять извне готовый улучшенный артефакт и зарегистрировать как вариант существующего изображения.
Компьют остаётся снаружи (инвариант платформы). **Auth:** `X-Internal-Api-Key: <FILE_PLATFORM_INTERNAL_API_KEY>` (как у `/internal/*`).
**Предусловие в схеме:** в `UploadImageVariantCode` добавлен `enhanced`; в `allowedVariants` контент-эндпоинта добавлен `enhanced`.

### POST /internal/images/{imageId}/variants/enhanced
Body: `multipart/form-data`
- `file` — байты улучшенного изображения (обяз.)
- `source` — метка происхождения, напр. `"realesrgan-x4"` (опц., для аудита).

Поведение:
1. Найти `UploadImage` по `imageId` (иначе `404`).
2. Валидировать: тип = image (file-type detection), размер ≤ `UPLOAD_MAX_FILE_MB` (20), пиксели ≤ `MAX_PREVIEW_SOURCE_PIXELS` (40М). Иначе `413/415`.
3. Сохранить в storage по детерминированному ключу `uploads/{imageId}/variants/enhanced/v{N}.jpg` (N = next version).
4. Создать/обновить `UploadImageVariant(variantCode=enhanced)` + `UploadImageVariantArtifact(variantVersion=N)`, `status=ready`, `currentArtifactId=N`. **Повторный вызов → новая версия** (idempotent-replace, бамп версии).
5. Ответ `200`: `{ "variantCode":"enhanced", "version":N, "width":int, "height":int, "size":int, "contentPath":"/api/upload-links/{token}/images/{imageId}/content?variant=enhanced" }`.

Ошибки: `401` (ключ), `404` (нет image), `413/415` (лимит/тип), `500`.

### Отдача (существующий эндпоинт, расширить allowlist)
`GET /upload-links/{token}/images/{imageId}/content?variant=enhanced` → байты (token-auth, `@SkipThrottle`).
Fallback-цепочка: если `enhanced` нет — обычное поведение (original/др. вариант).

**Инвариант:** не throttled internal, запись редкая. Никакого AI/GPU в платформе — только хранение присланных байт.

---

## C3. book-editor — API (FE↔BE) и использование C1/C2 воркером

### C3.FE — FE ↔ BE (новый модуль `/api/enhance`)

- `POST /api/enhance/photos/{filePlatformImageId}/start`
  Body: `{ "projectExternalId", "placementId"?|"slotW"?,"slotH"?, "faceRestore"?: bool }`
  BE: проверяет квоты/dedup, **сам авторитетно считает** `effectiveDpi` и target (см. Shared) из сохранённого layout; ставит EnhanceJob.
  `200`: `{ "jobId" }`. Ошибки: `403` (квота), `409` (уже улучшено/в работе — dedup), `422` (не low-DPI/нет данных).

- `POST /api/enhance/projects/{projectExternalId}/bulk`
  Body: `{ "faceRestore"?: bool }` → ставит джобы на **все low-DPI размещения** проекта (dedup, cap очереди).
  `200`: `{ "enqueued":int, "skipped":int, "jobIds":[...] }`.

- `GET /api/enhance/jobs/{jobId}` → `{ "jobId","status","stage","progress","error"?, "enhancedUrl"? }` (по EnhanceJob).
- `GET /api/enhance/projects/{projectExternalId}/status` → `{ "done","processing","queued","error","total" }` (прогресс bulk).

После `done`: BE проставляет `photo.variants.enhanced = .../content?variant=enhanced`; FE переключает before/after на этот URL.
**Гейт видимости кнопки на FE** — тем же DPI-util (Shared), но решение авторитетно у BE.

### C3.read — worker ↔ file-platform (чтение оригинала)
`GET {FILE_PLATFORM_API_BASE_URL}/api/upload-links/{uploadLinkToken}/images/{filePlatformImageId}/content?variant=original`
(публично по токену; **без throttle**). Токен и imageId уже есть в полях фото book-editor.

### C3.enhance — worker ↔ enhance-service
Использует **C1**: `POST /enhance/start` (байты оригинала + target_w/h из layout + face_restore) → poll `status` → `GET result` (байты).

### C3.store — worker ↔ file-platform (запись enhanced)
Использует **C2**: `POST /internal/images/{imageId}/variants/enhanced` (байты результата). Требует у book-editor конфиг `FILE_PLATFORM_INTERNAL_API_KEY`.

---

## Shared

### Расчёт DPI и target (общий util, BE-авторитетно, FE для UI)
- `effectiveDpi = max(photoW*300/slotW, photoH*300/slotH)`, где slot = `clipWidth/clipHeight` иначе `width/height` размещения (px при 300 DPI).
- **Гейт:** улучшать только при `effectiveDpi < 200` (порог уже принят в render.service).
- **target_w/target_h = slotW/slotH** (px слота — ровно столько нужно под 300 DPI).
- Вне ячейки (PhotoPreviewModal): брать самое крупное размещение фото в проекте.

### EnhanceJob (Postgres, book-editor) — состояние/очередь/история/квоты
Поля и индексы — см. план §4. Статусы: `queued → fetching → enhancing → storing → done|error`.
Стадии = pipeline §2. Fair-share по `projectExternalId` (round-robin), global concurrency=1..2.

### Лимиты/квоты (оборона в глубину — задача.md «Лимиты и метрики»)
- L0 FE: гейт low-DPI, disabled во время job, сообщения.
- L1 BE: per-user/день, per-project в очереди, глобальный дневной cap, dedup (ключ `imageId+target`).
- L2 очередь: cap длины → `429`/сообщение.
- L3 C1: один воркер + bounded queue + input cap + timeout.

### Конфиг / секреты (значения не печатать; маскировать)
- **enhance-service:** `ENHANCE_API_KEY`, `ENHANCE_MODEL_DIR`, `MAX_OUTPUT_PX`, `ENABLE_GFPGAN`, лимиты входа.
- **book-editor:** `ENHANCE_SERVICE_URL`, `ENHANCE_API_KEY`, `FILE_PLATFORM_API_BASE_URL` (есть), **`FILE_PLATFORM_INTERNAL_API_KEY` (новый для book-editor)**.
- **file-platform:** `FILE_PLATFORM_INTERNAL_API_KEY` (есть), лимиты загрузки (есть).

## Открытые вопросы по контрактам
1. **C1 sync vs async:** взят async (start/poll/result, как у Макса) — устойчив к 20с–2мин. Sync (один POST → картинка) проще, но риск долгих соединений. Оставляем async.
2. **C2 идентификация:** по `imageId` (у book-editor есть `filePlatformImageId`). Проверить, достаточно ли imageId без linkId на стороне file-platform (resolve image→link внутри).
3. **C2 версии vs overwrite:** взят versioned-replace (бамп `variantVersion`). Подтвердить storage-политику file-platform.
4. **target при нескольких размещениях** одного фото: берём максимум; договорить UX при разных ячейках.
5. **C3 кто считает target** — BE авторитетно (из сохранённого layout); FE дублирует для видимости кнопки. Подтвердить, что layout (slot/clip) доступен на BE для нужного фото.
