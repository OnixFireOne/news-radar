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
- Eval «до/после» (23.10, снимок `data/news.server-2026-10-23.db`) — `tests/golden/knowledge_runs/20261023T080916012071Z-m*.{md,json}`.
  Первый запуск упал на `insufficient_quota` (нет кредитов OpenAI), повтор после пополнения — 15/15 успешно, ≈ $0.08.

| Статья | v2/luna, симв. | v3/luna | v3/sol | Термины luna/sol | Таблица luna/sol |
|---|---|---|---|---|---|
| 1695 ML Intern (practical_case) | 5937 | 3290 (0.55) | 3964 (0.67) | 3 / 4 | нет / **да** |
| 2494 Claude Code (practical_case) | 5393 | 3194 (0.59) | 3261 (0.61) | 3 / 4 | нет / нет |
| 2433 research (VocabX) | 5292 | 3394 (0.64) | 3230 (0.61) | 4 / 3 | да / да |
| 2518 tutorial | 4992 | 3129 (0.63) | 2971 (0.60) | 3 / 2 | нет / нет |
| 2452 opinion | 4476 | 2720 (0.61) | 2575 (0.58) | 2 / 1 | нет / нет |

  Цена одного разбора: luna ≈ $0.0007–0.0009, sol ≈ $0.012–0.018 (×≈20); латентность sol 15–26 с против 8–10 с.
  Выводы по автокритериям:
  - **Длина:** v3 на 33–45% короче v2 — критерий «не длиннее на 20%» выполнен с запасом.
  - **Выдуманные числа:** не найдено. Все «числа без совпадения» — ложные срабатывания метрики на разделителях
    тысяч («113 426» в разборе против «113,426» в оригинале, «152 тысяч» против «152,000»). Цифры таблиц
    сверены с оригиналом вручную (1695: все шесть строк расходов совпадают с таблицей статьи; 2433 — тоже).
  - **Таблица ML Intern:** восстановлена только у sol; luna её пропустила — главный кейс брифа на luna не закрыт.
  - **Термины у статьи про Claude Code:** 3–4 (MCP-сервер, хук, скилл, бенчмарк) вместо ожидаемых «≈ 0» —
    правило «не объяснять привычную лексику ИИ-инструментов» модели не держат. Кандидат на правку промпта.

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

- Метрика `numbers_absent_from_source` в `tests/eval_knowledge_prompts.py` не нормализует пробел/запятую как разделитель тысяч → ложные срабатывания.
- **Прод:** по `llm_usage` снимка все LLM-вызовы с 23.10 01:09 UTC — с нулём токенов (последний успешный 22.10 23:09 UTC); выпуска за 23.10 в `digests` нет. Та же причина — нет кредитов.

## Команды приёмки

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q tests/test_knowledge_publisher_v3.py tests/test_knowledge_publisher_full.py tests/test_knowledge_publisher.py tests/test_strict_json.py
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python tests/eval_knowledge_prompts.py --db data/news.server-2026-10-23.db --message-id 1695 --message-id 2494 --message-id 2433 --message-id 2518 --message-id 2452 --variants knowledge-v2:gpt-6-luna knowledge-v3:gpt-6-luna knowledge-v3:gpt-6.1-sol
```
