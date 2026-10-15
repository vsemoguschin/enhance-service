# 2026-10-15-codex-luna-model

## Задача

Восстановить улучшение изображений после отказа gpt-5.5; владелец выбрал
самую быструю и дешёвую модель для передачи готового промпта в imagegen.

## Аудит

- Production Codex CLI наследует gpt-5.5 из общего config.toml: модель отклоняется
  для ChatGPT account. Резервный Magnific отвечает 422 Account not found.
- Процесс и health доступны, но это не проверяет успешность генерации.
- Серверный каталог содержит gpt-5.6-luna; ephemeral read-only запрос с low
  успешно завершился за 4.8с. Генерация этим запросом не проверялась.
- app/codex_provider.py не передаёт модель/effort, app/config.py не имеет настроек.
- scripts/codex_smoke.py подменяет _run_codex для --model устаревшей сигнатурой,
  без identity references. Production нельзя переключать этим monkeypatch.
- WORKTREE-WORKFLOW.md, CLAUDE.md и AGENTS.md в сервисе отсутствуют;
  применяются корневые инструкции. Main чист; isolated worktree создан штатным скриптом.

## План

1. Явные CODEX_MODEL=gpt-5.6-luna и CODEX_REASONING_EFFORT=low, параметры CLI
   и ephemeral режим; общий конфиг CLI не менять.
2. Убрать подмену provider из smoke, использовать те же runtime-настройки.
3. Проверить передачу параметров и существующие тесты; выполнить изолированный
   smoke на синтетическом изображении без пользовательских данных.
4. Production deploy/restart — только после явного поручения владельца согласно
   корневому AGENTS.md и .agent/memory/README.md.

## Результат

- Добавлены независимые CODEX_MODEL=gpt-5.6-luna и CODEX_REASONING_EFFORT=low;
  CLI получает явные -m/-c и --ephemeral, без изменения общего config.toml.
- scripts/codex_smoke.py использует production provider вместо устаревшего monkeypatch.
- Проверены все 16 unittest, git diff --check и diff вручную.
- Полный isolated provider smoke на codex: синтетическое изображение 640x480,
  три reference, настоящий imagegen, финиш и JPEG — успех за 53.1с,
  результат 1448x1086, 520066 bytes, декодируется OpenCV.
  Каталог теста временный, production source/config/process не менялись.
- Изменены .env.example, app/config.py, app/codex_provider.py,
  scripts/codex_smoke.py, tests/test_codex_multiref.py; добавлен этот audit.
- Production release ожидает явного разрешения владельца. При выпуске проверить
  текущие jobs, использовать штатный drain/blue-green, проверить router end-to-end.
  Magnific остаётся отдельной проблемой аккаунта, в этом изменении не затронут.
- Не выполнялись merge/push/deploy/restart, destructive или DB-mutating команды.

### Выпуск после разрешения владельца

- Владелец разрешил merge, push и deploy сообщением «го». Main fast-forward
  до 3e94b36 и push origin/main выполнены; origin был проверен fetch.
- Production app/config.py и app/codex_provider.py совпали с исходной базой
  по SHA256. Установлены только эти runtime-файлы, без изменения .env, CLI
  config.toml, зависимостей и соседних сервисов. Резервная копия и receipt:
  /opt/codex/releases/enhance-luna-3e94b36-backup.
- Запущен inactive enhance-service-b (8013). Полный C1 API smoke на
  синтетическом изображении: done за 38.1с, JPEG 1448x1086, 500679 bytes,
  HTTP 200, OpenCV decode успешен, face model присутствует.
- Штатный switch-active-slot.sh b через существующий codex-root выполнил
  nginx -t и reload; active router теперь b. Через router 8014 проверены
  health, статус synthetic job и полная выдача результата с совпадением bytes.
- С сервера редактора (ssh crm) router health вернул status=ok и slot=b.
- Старый slot a переведён в drain: active=0, queue_len=0, drained=true;
  затем остановлен; PM2 state сохранён для autostart.
- Полный UI/export сценарий на пользовательском проекте не запускался.
  БД и пользовательские файлы не изменялись. Magnific по-прежнему требует
  отдельного восстановления аккаунта.
