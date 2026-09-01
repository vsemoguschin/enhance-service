# 2026-09-01-enhance-reliability

## Задача

Устранить потери и ложные ошибки улучшения изображений: не пропускать Codex из-за ожидания
в очереди, сохранить причины обоих провайдеров, добавить диагностику и мониторинг, а также
подготовить беспрерывный двухслотовый деплой без уменьшения числа референсов и без изменения
Magnific.

## Аудит

- `JobManager` хранит очередь, jobs и результаты только в памяти. Перезапуск PM2 теряет
  in-flight job и делает его `job_id` неизвестным.
- `ENHANCE_FALLBACK_AFTER_WAIT_S=60` заставляет долго ожидавшую задачу пропустить основной Codex
  и сразу перейти к Magnific. Это не связано с реальной ошибкой Codex.
- При последовательных ошибках основной причины не видно клиенту: в `job.error` остаётся только
  исключение последнего fallback-провайдера.
- `job_id` не содержит владельца процесса, поэтому reverse proxy не может направить status/result
  в старый слот после переключения новых задач.
- Нет режима drain; `/metrics` не показывает возраст очереди, число resident jobs, состояние
  слота, последнюю успешную обработку и счётчики по провайдерам.
- Два одновременно запущенных слота удвоят число Codex-процессов. Нужен общий межпроцессный
  семафор, освобождающийся также при аварийном завершении процесса.
- Ошибка Codex содержит код выхода или хвост ответа, но не фиксирует длительность, этап,
  размеры рабочего входа и состав референсов.
- В репозитории отсутствует обязательный `scripts/create-agent-worktree.sh`; worktree этой задачи
  создан через временный project-local bootstrap, не затрагивавший runtime и удалённый из main.

Затронутые области: `app/config.py`, `app/main.py`, `app/queue.py`, `app/codex_provider.py`,
deployment scripts/config, `.env.example`, targeted tests и этот audit-документ.

## План

1. Сделать slot-aware `job_id`, drain API и расширенные метрики.
2. Убрать wait-based bypass Codex, сохранив fallback только после фактической ошибки; возвращать
   полную безопасную цепочку ошибок.
3. Ограничить суммарные provider-вызовы двух слотов файловым межпроцессным семафором.
4. Добавить безопасную диагностику Codex и тесты маршрутизации/метрик/семафора.
5. Добавить воспроизводимый worktree entrypoint и двухслотовые deployment artifacts/runbook.

## Результат

- `job_id` теперь содержит slot (`ej_a_...` / `ej_b_...`); добавлен authenticated drain endpoint,
  который закрывает приём новых задач, не прерывая принятые.
- Wait-based bypass удалён из runtime: даже при старом env `ENHANCE_FALLBACK_AFTER_WAIT_S=60`
  сначала вызывается Codex. Magnific вызывается только после фактической ошибки и не изменён.
- Ошибки провайдеров накапливаются цепочкой; `Magnific 402` больше не скрывает причину Codex.
- Диагностика Codex содержит stage, exit code, elapsed time, размеры original/working input,
  количество/роли reference inputs и очищенный хвост ответа с редактированием credential-like
  значений. Схема и число image references не менялись.
- Добавлен общий для процессов файловый семафор: оба blue/green slot суммарно используют не
  больше `ENHANCE_GLOBAL_CONCURRENCY`; locks снимаются ядром при crash.
- `/metrics` дополнен slot/drain state, resident jobs, возрастом очереди, provider counters,
  `last_success_at` и global provider occupancy. Добавлен внешний health probe.
- Добавлены PM2/nginx templates, atomic active-slot switch и runbook первой миграции/обычного
  deploy. Nginx template локально не запускался: его нужно проверить `nginx -t` на целевом хосте.
- Добавлен отсутствовавший обязательный worktree entrypoint.

Проверено:

- `python -m unittest discover -s tests -v` — 15/15;
- `python -m compileall -q app tests`;
- `bash -n` для изменённых/новых shell scripts;
- `node --check deploy/ecosystem.blue-green.config.cjs`;
- `git diff --check`.

Не входит в scope: переживание аварийного падения всего хоста — для него нужен внешний
персистентный job store. Реализация подготовлена к review; merge, push и prod-deploy не выполнялись.
