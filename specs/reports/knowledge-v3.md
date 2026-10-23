# knowledge-v3 — реализация 23.10.2026, ожидает приёмки

## Что сделано

- `analyzer/prompts.py`, `analyzer/json_schemas.py`: отдельные промпт и схема v3, поля v2 + `glossary`, `table`, без `ask_ai`; v2 сохранён.
- `analyzer/knowledge_publisher.py`: выбор версии, WARNING и возврат к v2 при неизвестной версии, новые блоки и проверка таблицы без потери разбора.
- `config/config_watcher.py`, `config/settings.json`: `knowledge.prompt_version` = `knowledge-v2`.
- `tests/test_knowledge_publisher_v3.py`: новые проверки версий, порядка блоков, таблиц, пустых полей и недоверенного текста. Старые тесты не менялись.
- `tests/eval_knowledge_prompts.py`: повторяемый `--message-id`, варианты промпт/модель, `--db` с дефолтом `DB_PATH` или `data/news.db`; SQLite только `mode=ro`, без публикации и записи в БД. JSON/Markdown и метрики, поддержка длинных статей через существующую генерацию.
- `mypy.ini`: оба новых Python-файла в `files=`, отдельные strict-секции. `docs/06_digest.md` и `specs/STATE.md` обновлены.

## Коммиты

- `feat(knowledge): add opt-in knowledge-v3 retellings and read-only eval` (код, тесты, docs, этот отчёт).

## Проверки (Claude Code, 23.10, в докере)

- `pytest -q --ignore=tests/collector`: 396 passed, 20 skipped. `tests/collector/` не собирается в образе analyzer (как и до изменений).
- `mypy`: 2 ошибки `unused-ignore` в `tests/test_strict_json.py` — есть и на HEAD до изменений, новых нет.
- Eval «до/после» — ждёт свежую копию БД с сервера (скачивание делает владелец).

## Новые зависимости

Нет.

## Расхождения со спекой

Нет; решения владельца в конце brief имеют приоритет: `ask_ai` исключён.

## Что не сделано / неопределённости

- Docker, pytest, mypy и платный eval не запускались по ограничениям Codex. Приёмка не заявляется.
- ID пяти статей выбирает оркестратор из свежего снимка БД. Качество текста, цена и ограничение роста длины требуют реального сравнения.
- Числа сравниваются лексически с нормализацией десятичной запятой; округления, единицы и преобразования могут давать ложные сигналы. Это подсказка для ручной проверки, не оценка достоверности.
- Стили GFM-таблиц на живом сайте, включая мобильную ширину, ещё не проверены. Переключение v3 не выполнено.

## Побочные находки

- `tests/test_strict_json.py`: 2 устаревших `type: ignore[method-assign]` валят `mypy` (было до knowledge-v3).
- Локально пропала внешняя сеть `ai-network` (после перезапуска OrbStack); создана заново `docker network create ai-network`.

## Команды приёмки

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q tests/test_knowledge_publisher_v3.py tests/test_knowledge_publisher_full.py tests/test_knowledge_publisher.py tests/test_strict_json.py
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python tests/eval_knowledge_prompts.py --db data/news.server-2026-10-23.db --message-id <id> --variants knowledge-v2:gpt-6-luna knowledge-v3:gpt-6-luna knowledge-v3:gpt-6.1-sol
```
