# enhance-service — эксплуатация (codex)

## Статус
Задеплоено и работает на codex через blue/green router: active slot `a`, nginx `:8014`,
`/health` и end-to-end Codex smoke проходят. В book-editor улучшение включено и обращается к
стабильному router endpoint; плановый deploy больше не требует обрывать принятые задачи.

## Топология
- **Хост:** codex (AI-бокс), публичный IP `89.191.229.110`. Сам codex за OpenVPN (remote `194.35.116.242`,
  `tun0=10.28.4.x`); весь исходящий по умолчанию идёт в VPN, кроме host-route на crm (`85.92.110.56`) через eth0.
- **crm → codex:** по **публичному IP** `89.191.229.110:8014` (НЕ по VPN-IP — он с crm недоступен).
- **Router:** nginx слушает `0.0.0.0:8014`; новые задачи направляет в active slot, а status/result —
  в слот из `job_id`.
- **Процессы:** pm2 slots `enhance-service-a` / `enhance-service-b`, `/opt/codex/enhance-service`,
  юзер `codex` (uid 109), loopback-порты `8012` / `8013`. Одновременно активен один слот; старый
  остаётся online только до drain принятых задач.
- **GPU:** нет → Vulkan = программный **llvmpipe** (`mesa-vulkan-drivers`). `vulkaninfo --summary` показывает llvmpipe.

## ⚠️ Перф (критично для решения о включении)
- 2 ядра + программный Vulkan: Real-ESRGAN x4 фото 1000×750 **не завершается и за 4.5 мин** (188% CPU).
  Реальные клиентские фото (крупнее) — десятки минут.
- → На этом боксе движок **непригоден для интерактива**. Не включать `ENHANCE_ENABLED` на проде, пока нет:
  - **GPU-бокса** (T4 / RTX 3060 / A2000, ~1–3 c/фото) — сервис переносится **как есть**, Vulkan подхватит GPU; **или**
  - **внешнего API** (Replicate / Magnific).
- Доп. риск: ncnn пинит оба ядра codex → может душить **ai-assistant** (сосед по боксу). Ещё причина не гонять на codex.

## Egress-замок (uid 109) — почему важно
- На codex OUTPUT для `uid 109` на eth0 закрыт, кроме явно разрешённых портов (ai-assistant `8090`,
  enhance router `8014`).
- Для каждого нового порта, который должен **отвечать** на crm:
  `iptables -I OUTPUT 1 -d 85.92.110.56 -o eth0 -p tcp --sport <port> -m owner --uid-owner 109 -j ACCEPT`.
- Персистентность — в OpenVPN up-script `/etc/openvpn/client/codex-new-up.sh` (рядом с 8090), переживает ребут.
- Симптом отсутствия правила: с crm `curl .../health` = `000`, локально на codex = `200`.

## Мониторинг
- `GET /health` (без ключа): статус, slot, drain, очередь и готовность провайдера.
- `GET /metrics` (с ключом): очередь, возраст старейшей задачи, resident jobs, provider counters,
  `last_success_at`, global provider occupancy и состояние drain.
- `scripts/check_service.py` проверяет health, ожидаемый slot, возраст очереди и общий лимит
  параллельных provider-вызовов; ключ в вывод не попадает.
- pm2: память (`--max-memory-restart 1200M`), число рестартов; следить за **рестартами ai-assistant** (сосед).
- Post-deploy smoke: через router `POST /enhance/start` тестовым JPEG → `done` → `/result`; отдельно
  проверить, что `job_id` содержит active slot и `/metrics` не показывает очередь после завершения.

## Деплой / обновление
- Blue/green порядок без потери jobs: [blue-green-deploy.md](blue-green-deploy.md).
- Общая установка и провайдеры: [deploy-runbook.md](deploy-runbook.md).
- Краткая шпаргалка (хост, структура, egress, обновление): `1/#deploy&launch/enhance-service.md`.

## Надёжность очереди и fallback (с 2026-09-01)

- Ожидание в очереди не является ошибкой провайдера: Codex вызывается первым независимо от
  `queued_for_s`. Magnific используется только после фактической ошибки или таймаута Codex.
- Если оба провайдера завершились ошибкой, job хранит безопасную цепочку причин; ошибка Magnific
  не затирает первичную диагностику Codex.
- Два blue/green slot разделяют файловый семафор. Суммарное число provider-вызовов ограничено
  `ENHANCE_GLOBAL_CONCURRENCY=3`; kernel освобождает lock при аварийном завершении процесса.
- Job state остаётся в памяти owning slot. Drain сохраняет принятые задачи при плановом deploy,
  но аварийная потеря всего хоста всё ещё потеряет resident jobs; для этого нужен внешний job store.
- Диагностика Codex фиксирует этап, длительность, код завершения, размеры рабочего входа и роли
  references. Потенциальные credentials редактируются перед сохранением и выдачей.

## Провайдер magnific (с 2026-08-10)

`ENHANCE_PROVIDER=magnific` уносит компьют во внешний API, поэтому **GPU на codex не нужен** и
ncnn-бинарь с моделями не требуется (`/health` их не проверяет в этом режиме).

Что нужно на боксе:
- `MAGNIFIC_API_KEY` в окружении процесса (в git не хранить, в логах не печатать);
- **исходящий HTTPS до `api.magnific.com`** — критично. На codex OUTPUT для uid 109 закрыт по
  портам, весь трафик по умолчанию идёт в VPN. Проверять на самом боксе от имени сервиса:
  `sudo -u codex curl -s -o /dev/null -w '%{http_code}' https://api.magnific.com/v1/ai/image-upscaler -X POST`
  Ожидаемый ответ — `401` (хост доступен, ключа нет). `000` = егресс закрыт, нужно правило OUTPUT.

Профиль нагрузки другой: воркер почти всё время ждёт сеть, CPU не занят — соседний ai-assistant
больше не страдает. Тайминги: восстановление ~25 с; апскейл (`MAGNIFIC_UPSCALE=true`) добавляет
~130 с и деньги по площади результата, поэтому по умолчанию выключен.

## Провайдер codex — AppArmor и sandbox (2026-08-11)

`ENHANCE_PROVIDER=codex` использует Codex CLI, который на Linux изолирует свои shell-вызовы
через **bubblewrap**. На Ubuntu 24.04 AppArmor по умолчанию запрещает unprivileged user
namespaces (`kernel.apparmor_restrict_unprivileged_userns=1`), из-за чего bwrap падает:

```
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
bwrap: setting up uid map: Permission denied
```

Агент при этом **не может даже прочитать входной файл** и отвечает `FAIL` — генерация не стартует.
Ровно та же причина ломала попытки использовать codex как ИИ-менеджера.

**Решение (применено на боксе):** точечный профиль `/etc/apparmor.d/codex-bwrap` с правом `userns`
для двух бинарей — системного `/usr/bin/bwrap` и vendored внутри Codex CLI. Глобальный sysctl
**не** трогали: защита остальной системы сохранена.

```bash
# проверка после ребута или обновления codex:
sudo aa-status | grep codex          # должны быть codex-bwrap и codex-vendor-bwrap
/usr/bin/bwrap --unshare-all --dev-bind / / /bin/true && echo sandbox OK
```

Файл в `/etc/apparmor.d/` подхватывается при старте службы apparmor, то есть переживает ребут.
Путь vendored-бинаря в профиле задан через `*` по архитектуре — обновление Codex CLI его не сломает,
но **переустановка в другой каталог сломает**: тогда профиль надо поправить.

Замеры на боксе (2 ядра, 1 ГБ RAM): чистая генерация ~79 с, полный цикл C1 ~131 с,
23.5 тыс. токенов подписки на прогон, память в пике ~600 МБ из 1000. Один воркер —
это ~27 фото/час. Кредиты Magnific при этом не расходуются.

### Адаптивный input, face-reference и мягкий финиш (2026-08-28)

Один `codex exec` передаёт imagegen три изображения с разными ролями: основной кадр, локально
проявленные тени и локально усиленную фактуру. Большие оригиналы уменьшаются до 2048 px только
для основного кадра; crop лиц YuNet берётся из полного оригинала. При найденных основных лицах
добавляется четвёртый identity-reference, при одном явно тёмном — пятый shadow-reference.
Генерируется один результат. Встроенный CLI не показывает точный usage; лабораторные вызовы
с 3–5 входами имели медианы 100–137 с, разброс генерации был сильнее зависимости от числа входов.

Production-настройки:

```text
CODEX_MULTI_REFERENCE=true
CODEX_ADAPTIVE_INPUT=true
CODEX_INPUT_DOWNSCALE_THRESHOLD_PX=2560
CODEX_INPUT_MAX_PX=2048
CODEX_REFERENCE_MAX_PX=4096
CODEX_FACE_REFERENCE=true
CODEX_FACE_MODEL=/opt/enhance-service/models/face_detection_yunet_2023mar.onnx
CODEX_FACE_DETECT_MAX_PX=2560
CODEX_FACE_REFERENCE_MAX_PX=1024
CODEX_FACE_SCORE_THRESHOLD=0.85
CODEX_FACE_RELATIVE_MIN_SCALE=0.5
CODEX_TIMEOUT_S=240
FINISH_DETAIL=true
FINISH_DETAIL_AMOUNT=0.25
FINISH_MICRO_CONTRAST=0.0
FINISH_SHARPEN_AMOUNT=0.20
```

YuNet-модель ставится `scripts/download_models.sh`. Если файла нет или детекция неуверенная,
сервис не падает и отправляет прежние три references. Быстрый откат face-crop:
`CODEX_FACE_REFERENCE=false`; адаптивного resize: `CODEX_ADAPTIVE_INPUT=false`; всего
multi-reference: `CODEX_MULTI_REFERENCE=false`. После изменения нужен restart с `--update-env`.
Состояние видно в `/health`, включая `adaptive_input`, `face_reference` и наличие `face_model`.

## Инцидент 2026-08-12: обрыв внешней сети и зависшая очередь

**Симптом.** `/health` отвечает, но `active=3`, `queue_len` растёт, `done` не меняется, при этом
`pgrep -f "codex exec"` пуст. Улучшения не идут, помогает только рестарт.

**Причина (исправлена, см. [audits/2026-08-12-hard-deadlines.md](audits/2026-08-12-hard-deadlines.md)).**
Бокс потерял выход в интернет: `curl` изнутри не открывал ни один внешний адрес, включая `1.1.1.1`,
хотя связь с crm работала (у неё отдельный маршрут). В этот момент вскрылись два дефекта:
таймауты складывались (3 попытки по 120с плюс паузы, срок проверялся только между итерациями
опроса), и `subprocess` убивал лишь родителя — потомки `codex` держали pipe, поэтому воркер
оставался «занят» без единого процесса.

Теперь на задачу один сквозной `Deadline` (потолки 90/30/90с на отправку, опрос, скачивание) и
`killpg` по таймауту. Худший случай — codex 150с + fallback magnific 150с ≈ 300с, ровно столько
ждёт book-editor.

**Диагностика при повторе:**
```bash
# 1) жива ли внешняя сеть с бокса (от пользователя сервиса, не от root):
curl -s -o /dev/null -m 10 -w '%{http_code}\n' https://1.1.1.1
curl -s -o /dev/null -m 12 -w '%{http_code}\n' https://chatgpt.com/backend-api/ps/mcp
# 2) движется ли очередь: два замера подряд, done должен расти
curl -s localhost:8011/metrics -H "X-Enhance-Api-Key: $ENHANCE_API_KEY"
# 3) есть ли реальные процессы под активными задачами
pgrep -c -f 'codex exec'
```
`active > 0` при нулевом счётчике процессов и не растущем `done` — признак зависших воркеров.

⚠️ Сбой никто не заметил около суток: мониторинга живости у сервиса нет. На боксе рядом живёт
ai-assistant — его недоступность видна в CRM как `ASSISTANT_TIMEOUT` в логах nestjs-app.

## Известные TODO
- Таймаут+kill для ncnn-subprocess (иначе зависший job держит единственный воркер) — см. [architecture.md](architecture.md).
- ~~Выбор движка для прода: GPU-бокс vs внешний API~~ — выбран внешний API, затем codex.
- Внешний запуск `scripts/check_service.py` и доставка его non-zero результата в канал оповещений
  должны оставаться частью инфраструктуры; сам сервис не отправляет уведомления.
- Вебхуки вместо поллинга: при `MAGNIFIC_UPSCALE=true` цикл ~155 с против 180 с таймаута book-editor.
