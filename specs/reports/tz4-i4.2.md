# ТЗ #4 — И4.2: калибровка оценок статей (промпт v4, dev.to через API)

**Статус:** ✅ закрыта 07.10.2026 (владелец: v4 отложена, очередь DEV помечена). Вне раздела 10 спеки, ближе всего к калибровке из И5.

## Почему

В очереди 272 статьи с оценкой ≥ 7, приходит 45–77 в день, выпуск забирает 1–7. 1001 из 1401
оценённой статьи — DEV Community (226 из них ≥ 7), с 02.10 оттуда по 240–250 статей в день.
У `practical_case` средняя 6.5, больше половины ≥ 7: шкала v3 оценивает жанр, а не новизну.

## Решения владельца 04.10

- Вес источника вручную не городить — брать реакции читателей из API dev.to (шаг 2).
- Промпт v4 — да; переоценка — только на сегодняшней выборке, без записи в базу.
- Склейка повторов по теме — на паузе («есть нюанс»).

## Шаг 1 — промпт `ai_value-v4` и замер

**Что сделано:**
- `analyzer/prompts.py` — `AI_VALUE_MESSAGE_PROMPT_V4` (шкала по доказанному, а не по жанру; потолок 5
  для общих советов без своих данных; потолок 6 для пересказа чужого релиза; резюме всегда на русском),
  формат вывода и правила `is_ad` — дословно хвост v3. Словарь `AI_VALUE_PROMPTS`. Текст v3 не менялся.
- `analyzer/value_classifier.py` — параметр `prompt_version`, неизвестная версия падает на старте.
- `analyzer/pipeline/analyzers.py`, `analyzer/analyzer.py` — ключ `ai_value_prompt_version` доходит до
  классификатора; передаётся, только если отличается от v3 (вызов по умолчанию прежний).
- Конфиг: `ai_value_prompt_version: "ai_value-v3"` в `DEFAULT_CONFIG` и `settings.json` — прод не меняется.
- `tests/eval_value_scoring.py` — `--prompt`.
- `scripts/rescore_sample.py` — переоценка выборки без записи в базу, итог в `data/rescore/`.
  `docker-compose.yml`: `./scripts` примонтирован в анализатор.
- `tests/test_prompt_v4.py` — 9 тестов.

**Замер 04.10, 97 статей с 04.10 (96 DEV, 1 Хабр), gpt-6-luna, strict:**

| | ≥ 7 | ≥ 8 | не на русском |
|---|---|---|---|
| v3 в базе | 24 | 7 | — |
| v3 повторно | 24 | 12 | 15 из 97 |
| v4 | 24 | 15 | 0 |
| v3.1 (104 статьи, позже) | 24 из 104 | 7 | 0 |

Повтор v3 меняет оценку у 49 из 97 (среднее ±0.62); v4 против повтора v3 — у 55 (±0.88).
**Вывод: строже v4 не стала, разница в пределах разброса** (грабля 45). Единственный явный плюс —
резюме на русском. Стоимость обоих прогонов — $0.047.

## Шаг 1b — `ai_value-v3.1` (решение владельца 04.10)

v3 без изменений плюс правило языка в самом конце промпта (`AI_VALUE_LANGUAGE_RULE`).
`settings.json` → `ai_value_prompt_version: "ai_value-v3.1"`; `DEFAULT_CONFIG` остаётся `ai_value-v3`.

Первая версия ставила строку про язык перед блоком формата — на 104 статьях за 04.10 одна тайская
статья утянула на тайский всю пачку из 5 (1325–1329). Правило в конце промпта, с явным «даже если
другие статьи пачки на другом языке» — 0 из 104 не на русском; ≥ 7: 24, ≥ 8: 7 (в базе 26 / 8) —
в пределах разброса. Стоимость двух прогонов — $0.05. Анализатор перезапущен 04.10.

## Шаг 2 — коллектор dev.to через API (решение владельца 04.10: порог ≥ 10, старую очередь убрать)

**Что сделано** (код — Codex по брифу, проверка и приёмка — Claude Code):
- `collectors/devto.py` — `DevtoCollector`: `GET /api/articles?tag=ai&top=3`, берёт статьи 24–72 ч
  с `public_reactions_count ≥ 10`, без `ai_disclosure_level = fully_autonomous`; полный текст —
  `body_markdown` из `/api/articles/{id}`, заголовок первой строкой; реакции → `reactions_count`,
  комментарии → `replies_count`. Статья ниже порога или моложе 24 ч не запоминается — перепроверяется
  в следующих циклах. Сбой одной статьи (404 у удалённой) цикл не роняет.
- `collectors/poll_runner.py` — запуск при `sources.devto.enabled`.
- Конфиг: блок `sources.devto` в `DEFAULT_CONFIG` (выключен) и `settings.json`; `devto` в
  `categories.articles.sources`.
- `tests/collector/test_devto.py` — 7 тестов (в одном Claude Code поправил `respx.mock(assert_all_called=False)`:
  маршрут нужен, чтобы доказать, что он не вызывается).

**Включение 04.10:** `settings.json` — `devto.enabled: true`, лента `dev.to/feed/tag/ai` убрана из RSS
(`DEFAULT_CONFIG` не менялся); образ `collector-feeds` пересобран и контейнер пересоздан. Первый цикл — 16 статей
(реакции 10–56). **Находка:** ни одной из них не было в базе — RSS отдаёт только самые свежие посты, и при
~250 в день популярные статьи в него не попадали. RSS приносил поток без отбора, API — то, что RSS пропускал.

**Старая очередь DEV из RSS (решение (б)):** копия базы — `data/news.db.bak-before-devto-queue-drop`.
Пометка `in_digest=3` (1001 строка, из них 205 с оценкой ≥ 7): запись в прод-базу была
заблокирована фильтром прав Claude Code, команду выполнил владелец. Проверка 07.10: у ленты
`DEV Community: ai` `in_digest=3` — 997, `=1` — 33, `=0` — нет (4 статьи успели уйти в выпуск до пометки).
`in_digest=3` код нигде не читает: пул берёт `= 0`, статистика опубликованного — `= 1`, `2` — резерв сборки.

## Новые зависимости

Нет.

## Расхождения со спекой

- Работа вне раздела 10; вес источника (р.3.2 п.1) по плану — И5, владелец решил заменить его реакциями dev.to.

## Что НЕ сделано / отложено

- `eval_value_scoring.py --prompt ai_value-v4` на golden set не прогонялся — v4 не включаем, пока не решено, что с ней делать.
- Склейка повторов — на паузе.
- ~~Пометка старой очереди DEV `in_digest=3`~~ → выполнена владельцем, проверена 07.10.
- **Промпт `ai_value-v4` отложен (решение владельца 07.10):** не строже v3, разброс прогонов сравним с эффектом. В проде — `ai_value-v3.1`; черновик v4 в коде остаётся, не включён.

## Команды приёмки

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python scripts/rescore_sample.py --since 2026-10-04 --prompt ai_value-v4   # платно, ~$0.02
docker compose --profile feeds build collector-feeds
docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector
# (б) убрать старую очередь DEV из RSS из пула; откат: ... SET in_digest=0 WHERE in_digest=3
docker compose exec analyzer python -c "import sqlite3; c=sqlite3.connect('/app/data/news.db'); print(c.execute(\"UPDATE messages SET in_digest=3 WHERE in_digest=0 AND source_id IN (SELECT id FROM sources WHERE type='rss' AND name='DEV Community: ai')\").rowcount); c.commit()"
```
