# Deploy runbook — AI-улучшение изображений (3 сервиса)

Фича охватывает 3 сервиса. **Порядок: file-platform → enhance-service → book-editor → постепенная раскатка.**

## 0. Секреты (сгенерировать заранее)
- `ENHANCE_API_KEY` — общий для book-editor ↔ enhance-service.
- `FILE_PLATFORM_INTERNAL_API_KEY` — общий для book-editor ↔ file-platform (**должны совпадать**).

---

## 1. file-platform (его отдельный сервер)
Ветка: `agent/2026-06-25-enhance-variant-and-rotation`.
```bash
# merge в main по WORKTREE-WORKFLOW, затем на сервере:
cd <file-platform>/back && npm ci && npm run build
npx prisma migrate deploy            # ⚠️ применяет enum-вариант enhanced
pm2 restart <file-platform> --update-env
```
Env: `FILE_PLATFORM_INTERNAL_API_KEY=<secret>`.
Проверка: загрузить НОВОЕ фото c EXIF-поворотом → его вариант выпрямлен (фикс `.rotate()`); `POST /internal/images/:id/variants/enhanced` с ключом → 200.
⚠️ **Перегенерация старых вариантов:** фикс ротации действует только на НОВЫЕ варианты. Существующие фото останутся «боком», пока не перегенерить (re-enqueue генерации по всем `UploadImage`). Проверить наличие механизма; если нет — мелкий follow-up (скрипт/админ-эндпоинт re-enqueue).

---

## 2. enhance-service (на codex — AI-бокс)
Сейчас локально, не под git. Перенести на codex (напр. `/opt/enhance-service`), при желании завести git.
```bash
ssh codex
# ⚠️ КРИТИЧНО: Vulkan ICD для ncnn-vulkan на CPU-VM:
sudo apt-get install -y libvulkan1 mesa-vulkan-drivers
vulkaninfo --summary                 # должен показать device (llvmpipe/lavapipe), иначе ncnn упадёт
cd /opt/enhance-service
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PLATFORM=ubuntu bash scripts/download_models.sh
```
Env: `ENHANCE_API_KEY=<secret>` (+ опц. лимиты ENHANCE_*).
⚠️ **Сеть (проверено на codex 2026-06-24):** crm обращается к codex по **публичному IP `89.191.229.110`** напрямую (НЕ по VPN-IP: tun0 у codex — `10.28.4.8`, он нужен codex для исходящего antizapret-трафика и с crm недоступен). enhance-service слушает `0.0.0.0:8011`.
```bash
pm2 start ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8011" \
  --name enhance-service --max-memory-restart 1200M
pm2 save
```

⚠️⚠️ **codex egress-замок (uid 109) — без этого crm получает `000`:**
На codex действует OUTPUT-фаервол: пользователю `uid 109` (под ним крутятся и ai-assistant, и enhance-service) запрещён **весь исходящий на eth0, кроме явно разрешённых портов**. Маршрут на crm есть и входящий на 8011 проходит, но **ответ** сервиса режется правилом
`-A OUTPUT -o eth0 -m owner --uid-owner 109 -j REJECT`, пока нет зеркального ACCEPT для порта. Поэтому для каждого нового порта, отвечающего на crm, нужно правило (как у 8090):
```bash
# root на codex (85.92.110.56 — IP crm):
iptables -I OUTPUT 1 -d 85.92.110.56 -o eth0 -p tcp --sport 8011 -m owner --uid-owner 109 -j ACCEPT
```
Симптом отсутствия правила: с crm `curl .../health` → `000`, при этом локально на codex `/health` = 200.
**Персистентность:** добавить это правило туда, где восстанавливается аналогичное для 8090 (netfilter-persistent `/etc/iptables/rules.v4` или стартовый скрипт) — иначе после ребута codex enhance снова отвалится.

Проверка с crm: `curl --max-time 5 http://89.191.229.110:8011/health` → `200`. `/health` открыт без ключа; тяжёлые `/enhance/*` — только с `X-Enhance-Api-Key`.

---

## 3. book-editor (на crm, pm2 `book-editor-backend`/`book-editor-frontend`)
Ветка: `agent/2026-06-25-image-enhance` (15 коммитов). Способ деплоя book-editor уточнить (git pull на crm).
```bash
# merge в main, затем на crm:
cd <book-editor>/book-editor-backend && npm ci && npm run build && pm2 restart book-editor-backend --update-env
cd ../book-editor-frontend && npm ci && npm run build && pm2 restart book-editor-frontend --update-env
```
Env (book-editor-backend `.env`):
```
ENHANCE_SERVICE_URL=http://89.191.229.110:8011
ENHANCE_API_KEY=<secret>                 # = enhance-service
FILE_PLATFORM_INTERNAL_API_KEY=<secret>  # = file-platform
FILE_PLATFORM_API_BASE_URL=<уже задан>
ENHANCE_ENABLED=false                    # на старте ВЫКЛ
ENHANCE_MAX_CONCURRENT=2
```
Проверка: при `ENHANCE_ENABLED=false` → `POST /api/photo-enhance` отвечает 503.

---

## 4. Постепенная раскатка
1. `ENHANCE_ENABLED=true` (pm2 restart --update-env) — сперва для себя/узкой группы.
2. End-to-end: улучшить фото → создан вариант `enhanced` в file-platform → экспорт PDF/ZIP использует enhanced.
3. Мониторинг: enhance-service `/metrics` (очередь/ошибки/тайминги); pm2 память codex + **рестарты ai-assistant** (сосед по боксу); book-editor 429/ошибки.
4. Ок → оставить; проблемы → `ENHANCE_ENABLED=false` (мгновенный выкл).

## Откат
- Быстрый: `ENHANCE_ENABLED=false` на book-editor — кнопка перестаёт работать, остальное не затронуто.
- Полный: revert merge book-editor. file-platform/enhance-service можно оставить (не мешают).

## Чеклист после деплоя
- [ ] `vulkaninfo` на codex показывает device
- [ ] health enhance-service доступен с crm (с ключом)
- [ ] file-platform: новый EXIF-фото → вариант ровный; миграция `enhanced` применена
- [ ] ключи совпадают (book-editor↔file-platform, book-editor↔enhance-service)
- [ ] улучшить → вариант `enhanced` создан → экспорт использует enhanced; откат после reopen жив
- [ ] старые варианты перегенерены (или запланировано)
