# 2026-10-30-enhance-result-ownership

## Задача

Защитить улучшения от смешивания результатов параллельных задач. Сохранить текущие
модель, качество, очередь и лимиты параллельности. Production rollout и исправление
сохранённых пользовательских данных не входят в текущий этап.

После подготовки владелец явно поручил merge/push/выпуск, затем финализацию
и cleanup. Восстановление ранее сохранённых пользовательских данных не поручалось.

## Аудит

- В production используется enhance-service `7d52674`. Две параллельные генерации
  с разными оригиналами завершились одинаковыми размерами и размером PNG; сохранённые
  JPEG у двух проектов побайтно совпали. Точный выбор PNG внутри CLI не журналируется.
  Конкретный механизм подмены пока гипотеза; подтверждено отсутствие проверки provenance.
- `app/codex_provider.py:_run_codex` доверяет произвольному `out.png`, который копирует
  агент. Проверяет лишь exit code, наличие и ненулевой размер файла.
- Отдельные временные cwd не изолируют общий generated_images. Read-only metadata
  probe production подтвердил layout: generated_images/<UUID>/<file>.png.
- CLI `--json` сообщает `thread.started.thread_id` (официальная документация
  https://learn.chatgpt.com/docs/non-interactive-mode). Это отдельный машинный канал
  идентичности запуска; текст финального ответа агента источником идентичности не является.
- Сервисный wrapper должен выбирать артефакт только из каталога этой сессии. Другая
  структура/несколько результатов/отсутствие JSON identity должны завершаться ошибкой,
  без поиска «последнего файла» во всём generated_images.
- Затрагиваются provider и его unit tests. C1 API, book-editor, file-platform, БД,
  настройки моделей, concurrency, resize и finish остаются прежними.

## План

1. Добавить JSON identity текущего CLI-запуска и детерминированное получение одного
   артефакта из generated_images/<thread_id>. Не поручать агенту выбор/копирование output.
2. Отклонять отсутствующую/неоднозначную identity, отсутствующие/множественные/пустые/
   слишком большие артефакты и symlink. Логировать только opaque identity/hash/размер.
3. Проверить одновременные задачи с разным порядком завершения и чужим подложенным
   out.png, а также отрицательные случаи. Использовать синтетические fixtures без
   реальных фото, сетевых генераций, изменения production и credential files.
4. Выполнить существующие unit tests, diff checks и зафиксировать результат в ветке.

## Результат

- Добавлен `app/codex_artifact.py`: identity берётся только из единственного
  `thread.started` JSON-события CLI при завершённом turn. Выбирается единственный
  regular image artifact из generated_images/<thread_id>; ссылки, отсутствующие,
  неоднозначные, пустые и oversized файлы отклоняются. Чужие session dirs не обходятся.
- `app/codex_provider.py` запускает CLI с `--json`, сам сохраняет проверенные байты
  в свой временный output и игнорирует агентский выбор out.png. Hardlink заменяется
  новым файлом, symlink отклоняется; чужой объект при записи не изменяется.
- В prompt сохранены прежние refs и enhancement instructions; выбор/перенос файла
  убран из обязанностей агента. Модель, effort, concurrency, очередь, API, geometry,
  finish, fallback и качество JPEG не менялись. Новые ошибки provenance проходят
  существующий provider fallback; если он тоже недоступен, задача завершается ошибкой.
- Диагностика успеха содержит opaque thread_id и SHA-256 артефакта. JSON stdout
  не попадает в сообщения об ошибках (там могут быть image/tool payloads).
- Изменены `app/codex_provider.py`, `tests/test_codex_multiref.py`; добавлены
  `app/codex_artifact.py`, `tests/test_codex_artifact.py` и этот audit. Удалённых файлов нет.
- Проверки: `python -m unittest discover -s tests -v` — **30/30 PASS**;
  `python -m compileall -q app tests`, `git diff --check` и staged diff check — PASS.
  Тесты работали с отдельными temp work/lock paths, без credential files и сети.
- Регрессионный сценарий запускает два реальных локальных subprocess fake CLI
  одновременно, с различными синтетическими изображениями и намеренно перепутанными
  out.png. Проверены оба порядка завершения; каждая задача получает свой результат.
  Дополнительно проверены missing/ambiguous output, invalid identity, чужой output
  без собственного артефакта, links/nonregular files и отсутствие payloads в ошибках.
- Ограничения: это защита от выбора чужого файла между CLI-сессиями, а не семантическая
  проверка содержимого ответа самой image model. Неизвестный layout CLI отклоняется,
  без unsafe fallback на out.png или общий «последний файл». Перед rollout нужен
  live smoke с двумя синтетическими изображениями на установленной Linux CLI,
  подтверждающий thread_id/layout и реальный builtin imagegen. В этой задаче живые
  генерации не запускались; smoke с fake CLI не заменяет этот acceptance check.
- Merge/push/deploy/restart/миграции и изменения данных проектов не выполнялись.
  Работа изолирована в `agent/2026-10-30-enhance-result-ownership`; main не менялся.

### Проверка перед выпуском по разрешению владельца

- Выполнен настоящий isolated smoke на AI host с production Linux Python/dependencies
  и установленным Codex CLI: два синтетических кадра 640×480 запускались одновременно,
  через прежний общий provider semaphore с лимитом 3. Source кандидата временный;
  production source/config/process и пользовательские фото не менялись.
- Обе CLI-сессии выдали по одному `thread.started` и `turn.completed`, собственные
  каталоги generated_images найдены. Получены различные PNG SHA-256; оба JPEG
  декодируются, выход 1448×1086. Время полных provider-проходов 35.2 и 34.3 секунды.
- Сходство с правильным синтетическим исходником подтверждено для обоих выходов:
  RMSE 16.45 против 140.82 для чужого и 17.44 против 153.18 для чужого.
  Контракт thread_id → каталог imagegen подтверждён на настоящем runtime.
- У синтетических кадров не проверялись лица; проход использовал три стандартных
  references и штатный finish. Данные проектов и сохранённые улучшения не трогались.

### Выпуск и финализация

- Merge `e77cb474951236a245d0f41fc26d44e8d7a28a68` опубликован в origin/main;
  dev source обновлён штатным plan. Linux unit tests exact main: **30/30 PASS**.
- Штатный artifact deploy собрал и запустил immutable release в slot b, router
  переключился на b. Однако helper вернул `operation_blocked`, deploy-state стал
  `recovery_required`, старый a ещё не был draining. Первичная причина в нормализованном
  ответе helper не сохранена; гипотезу о задержке reload нельзя считать доказанной.
- До продолжения проверены exact SHA, kernel/PM2 identity обоих процессов, доступность
  обоих metrics, три последовательных router health b и старая очередь. Под deploy-lock
  сохранён private recovery receipt; затем штатные executor drain(a), PM2 save и
  write_state(complete) завершили уже подтверждённое переключение. Повторного deploy,
  restart, отката и преждевременной остановки процессов не было.
- Финальный active slot b: `e77cb47`, прежний a: `7d52674`, draining; 20 resident jobs
  сохранены до естественного TTL. Concurrency 3 и текущие модель/effort сохранены.
- Полный production C1 smoke через router: POST/start 200, affinity b, status done,
  result 200 image/jpeg, OpenCV decode PASS, 1448×1086, 212464 bytes, 32.1 секунды.
  Сходство с синтетическим исходником подтверждено; hashes обоих runtime-файлов
  (`codex_artifact.py`, `codex_provider.py`) совпали с локальным main.
- С сервера book-editor router health 200, status ok, slot b, engine cli/face_model/
  multi_reference/finish true. UI/export на пользовательском проекте не запускался;
  прежде сохранённое ошибочное улучшение не исправлялось.
- Финализация: `docs/providers.md` описывает контракт принадлежности и проверку CLI,
  operational runbook и отчёт владельцу обновлены отдельно в закрытом knowledge repo.
  Финализирующий commit меняет только документацию, повторный runtime deploy не нужен.
