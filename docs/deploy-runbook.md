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

## 2b. Переключение на провайдера magnific (2026-08-10)

Причина, по которой фича стояла выключенной (нет GPU), снимается: компьют уходит во внешний API,
ncnn-бинарь и модели в этом режиме не нужны.

```bash
ssh codex
cd /opt/enhance-service
git fetch && git checkout main && git pull        # после merge ветки agent/2026-08-10-magnific-api-test
.venv/bin/pip install -r requirements.txt          # добавился certifi
```

В окружение процесса (pm2 env или `.env`, **в git не коммитить**):
```
ENHANCE_PROVIDER=magnific
MAGNIFIC_API_KEY=<ключ из кабинета Magnific>
MAGNIFIC_PRESET=texture       # дефолт прода, принят владельцем
MAGNIFIC_UPSCALE=false        # апскейл платится по площади результата
```

```bash
pm2 restart enhance-service --update-env
curl -s localhost:8011/health   # ожидается provider=magnific, status=ok, engine.key=true
```

⚠️ **Проверить исходящий HTTPS от имени сервиса** — на codex OUTPUT для uid 109 закрыт по портам:
```bash
sudo -u codex curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.magnific.com/v1/ai/image-upscaler
```
`401` = хост доступен (ключа в запросе нет — так и надо). `000` = егресс закрыт, нужно правило OUTPUT
на 443 для uid 109 (по аналогии с правилом для 8011) + персистентность в up-script.

Smoke на боксе (полный цикл C1, ~25 с, спишет 50 кредитов ≈ 4.6 ₽):
```bash
curl -s -X POST localhost:8011/enhance/start -H "X-Enhance-Api-Key: $ENHANCE_API_KEY" \
  -F "file=@/tmp/test.jpg" -F "target_w=2000" -F "target_h=1700"
curl -s localhost:8011/enhance/status/<job_id> -H "X-Enhance-Api-Key: $ENHANCE_API_KEY"
curl -s -o /tmp/out.jpg localhost:8011/enhance/result/<job_id> -H "X-Enhance-Api-Key: $ENHANCE_API_KEY"
```
Ожидается: `done` за ~25 с, на выходе JPEG с **той же пропорцией**, что у входа (инвариант сервиса).

## 2c. Переключение на провайдера codex (2026-08-11)

Оплата подпиской вместо кредитов, качество лучшее из проверенных, но медленнее (~130 с на фото)
и один воркер. Требует рабочего sandbox — см. AppArmor в [operations.md](operations.md).

```bash
# 1. Разово, root на боксе: профиль AppArmor для bwrap (иначе агент отвечает FAIL)
#    файл /etc/apparmor.d/codex-bwrap уже создан; проверка:
sudo aa-status | grep codex

# 2. В .env сервиса:
#    ENHANCE_PROVIDER=codex
#    CODEX_BIN=/opt/codex/.npm-global/bin/codex   (полный путь: PATH pm2 не содержит npm-global)
#    CODEX_PRESET=identity
pm2 restart enhance-service --update-env
curl -s localhost:8011/health   # ожидается provider=codex, engine.cli=true

# 3. В book-editor на crm поднять ожидание: генерация ~130 с против дефолтных 180 с — впритык.
#    ENHANCE_MAX_WAIT_MS=300000
pm2 restart book-editor-backend --update-env
```

Откат на Magnific — одна строка: `ENHANCE_PROVIDER=magnific` + рестарт.

## 4. Постепенная раскатка
1. `ENHANCE_ENABLED=true` (pm2 restart --update-env) — сперва для себя/узкой группы.
2. End-to-end: улучшить фото → создан вариант `enhanced` в file-platform → экспорт PDF/ZIP использует enhanced.
3. Мониторинг: enhance-service `/metrics` (очередь/ошибки/тайминги); pm2 память codex + **рестарты ai-assistant** (сосед по боксу); book-editor 429/ошибки.
4. Ок → оставить; проблемы → `ENHANCE_ENABLED=false` (мгновенный выкл).

## Откат
- Быстрый: `ENHANCE_ENABLED=false` на book-editor — кнопка перестаёт работать, остальное не затронуто.
- Полный: revert merge book-editor. file-platform/enhance-service можно оставить (не мешают).

## Чеклист после деплоя
- [ ] `vulkaninfo` на codex показывает device — **только для `ENHANCE_PROVIDER=local`**
- [ ] при `magnific`: `/health` отдаёт `provider=magnific`, `engine.key=true`
- [ ] при `magnific`: исходящий на `api.magnific.com` от uid 109 даёт `401`, не `000`
- [ ] при `magnific`: smoke-цикл C1 отработал, пропорция выхода = пропорции входа
- [ ] health enhance-service доступен с crm (с ключом)
- [ ] file-platform: новый EXIF-фото → вариант ровный; миграция `enhanced` применена
- [ ] ключи совпадают (book-editor↔file-platform, book-editor↔enhance-service)
- [ ] улучшить → вариант `enhanced` создан → экспорт использует enhanced; откат после reopen жив
- [ ] старые варианты перегенерены (или запланировано)
