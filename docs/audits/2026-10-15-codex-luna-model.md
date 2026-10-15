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
