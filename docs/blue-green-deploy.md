# Blue/green deploy без потери enhance jobs

## Инварианты

- slot `a` слушает `127.0.0.1:8012`, slot `b` — `127.0.0.1:8013`;
- CRM обращается только к стабильному nginx endpoint `:8014`;
- новый `POST /enhance/start` идёт в active slot;
- status/result маршрутизируются по `ej_a_...` / `ej_b_...`, поэтому переключение active slot
  не отрывает уже созданные jobs;
- оба процесса используют один `ENHANCE_GLOBAL_LOCK_DIR` и один
  `ENHANCE_GLOBAL_CONCURRENCY=3`;
- `/admin/drain` доступен только напрямую через loopback и требует API key.

## Однократная подготовка

1. Установить `deploy/nginx-enhance-router.conf.example` как отдельный nginx-конфиг.
2. Создать два snippet-файла:
   - `/etc/nginx/snippets/enhance-active-a.conf`: `proxy_pass http://enhance_slot_a;`
   - `/etc/nginx/snippets/enhance-active-b.conf`: `proxy_pass http://enhance_slot_b;`
3. Сделать `enhance-active-proxy.conf` symlink на активный snippet.
4. Разрешить в firewall ответы с source port `8014` к CRM; слоты 8012/8013 наружу не открывать.
5. Перевести `book-editor` на `ENHANCE_SERVICE_URL=http://<codex-host>:8014`.

Legacy `ej_<uuid>` не содержит affinity. При первой миграции старый backend должен продолжать
уже открытые запросы к `:8011`, а новый backend — принимать новые через `:8014`. После завершения
старых HTTP-запросов процессы `book-editor` и legacy enhance можно остановить. Это единственный
переходный случай; последующие deploy используют slot-aware ids.

## Обычный deploy

1. Определить inactive slot и запустить в нём новую версию с
   `ENHANCE_SLOT=<slot>`, `ENHANCE_PORT=<8012|8013>`.
2. Проверить напрямую `/health`: `status=ok`, правильный `slot`, `draining=false`.
3. Выполнить `sudo bash ./scripts/switch-active-slot.sh <slot>` — nginx проверяется и reload
   выполняется атомарно, новые jobs сразу идут в новый процесс.
4. На старом loopback-порту вызвать `POST /admin/drain` с `X-Enhance-Api-Key`.
5. Опросить старый `/metrics` до `drained=true`, после чего остановить его PM2 process.

Ожидание drain не блокирует deploy и не требует окна: новые jobs уже обслуживает новый слот,
а старый живёт только ради ранее выданных job ids.

## Мониторинг

Запускать `scripts/check_service.py` с `ENHANCE_MONITOR_URL=http://127.0.0.1:8014` и ключом из
существующего `.env`. Probe не печатает ключ и возвращает non-zero при degraded health,
несовпадении слота, очереди старше `ENHANCE_MONITOR_MAX_QUEUE_AGE_S` (по умолчанию 600 секунд)
или нарушении общего concurrency limit. JSON `/metrics` дополнительно содержит provider counters,
resident jobs, `last_success_at` и drain state для графиков и расследований.

## Откат

Пока предыдущий slot не остановлен, снова переключить active symlink через
`switch-active-slot.sh`. После остановки сначала поднять предыдущую версию и проверить `/health`.

## Ограничение

Схема закрывает плановые deploy/restart, но не аварийное падение всего хоста. Для этого нужна
персистентная внешняя очередь/job store; она сознательно не входит в текущий minimal safe diff.
