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

## Известные TODO
- Таймаут+kill для ncnn-subprocess (иначе зависший job держит единственный воркер) — см. [architecture.md](architecture.md).
- Выбор движка для прода: GPU-бокс vs внешний API.
