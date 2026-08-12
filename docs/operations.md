# enhance-service — эксплуатация (codex)

## Статус
Задеплоено и работает на codex (pm2 online, `/health` ок, доступ с crm есть, end-to-end smoke на codex проходит).
В book-editor **выключено** флагом `ENHANCE_ENABLED=false` — из-за перфа (нет GPU, см. ниже).

## Топология
- **Хост:** codex (AI-бокс), публичный IP `89.191.229.110`. Сам codex за OpenVPN (remote `194.35.116.242`,
  `tun0=10.28.4.x`); весь исходящий по умолчанию идёт в VPN, кроме host-route на crm (`85.92.110.56`) через eth0.
- **crm → codex:** по **публичному IP** `89.191.229.110:8011` (НЕ по VPN-IP — он с crm недоступен).
- **Процесс:** pm2 `enhance-service`, `/opt/codex/enhance-service`, юзер `codex` (uid 109), слушает `0.0.0.0:8011`.
- **GPU:** нет → Vulkan = программный **llvmpipe** (`mesa-vulkan-drivers`). `vulkaninfo --summary` показывает llvmpipe.

## ⚠️ Перф (критично для решения о включении)
- 2 ядра + программный Vulkan: Real-ESRGAN x4 фото 1000×750 **не завершается и за 4.5 мин** (188% CPU).
  Реальные клиентские фото (крупнее) — десятки минут.
- → На этом боксе движок **непригоден для интерактива**. Не включать `ENHANCE_ENABLED` на проде, пока нет:
  - **GPU-бокса** (T4 / RTX 3060 / A2000, ~1–3 c/фото) — сервис переносится **как есть**, Vulkan подхватит GPU; **или**
  - **внешнего API** (Replicate / Magnific).
- Доп. риск: ncnn пинит оба ядра codex → может душить **ai-assistant** (сосед по боксу). Ещё причина не гонять на codex.

## Egress-замок (uid 109) — почему важно
- На codex OUTPUT для `uid 109` на eth0 закрыт, кроме явно разрешённых портов (ai-assistant `8090`, enhance `8011`).
- Для каждого нового порта, который должен **отвечать** на crm:
  `iptables -I OUTPUT 1 -d 85.92.110.56 -o eth0 -p tcp --sport <port> -m owner --uid-owner 109 -j ACCEPT`.
- Персистентность — в OpenVPN up-script `/etc/openvpn/client/codex-new-up.sh` (рядом с 8090), переживает ребут.
- Симптом отсутствия правила: с crm `curl .../health` = `000`, локально на codex = `200`.

## Мониторинг
- `GET /health` (без ключа): `{ status, busy, queue_len, engine:{bin,model} }`.
- `GET /metrics` (с ключом): очередь / тайминги / ошибки.
- pm2: память (`--max-memory-restart 1200M`), число рестартов; следить за **рестартами ai-assistant** (сосед).
- Post-deploy smoke: на codex локально `POST /enhance/start` тестовым JPEG → `done` → `/result` 4x; с crm — `/health` = 200.

## Деплой / обновление
- Пошагово: [deploy-runbook.md](deploy-runbook.md).
- Краткая шпаргалка (хост, структура, egress, обновление): `1/#deploy&launch/enhance-service.md`.

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
- **Нет проверки живости.** Обрыв сети 12.08 обнаружился только вручную, через сутки. Минимум —
  внешний пинг `/health` и сигнал при `active > 0` с не растущим `done`.
- Вебхуки вместо поллинга: при `MAGNIFIC_UPSCALE=true` цикл ~155 с против 180 с таймаута book-editor.
