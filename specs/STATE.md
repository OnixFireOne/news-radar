# ТЗ #4 — состояние

**Ветка:** `feature/tz4` · **Спека:** `specs/tz4-ai-value.md` (v1.7) · **Обновлено:** 11.09.2026

Этот файл — точка входа. Кто открывает новую сессию (исполнитель или архитектор) — читает его первым.
Обновляет: исполнитель в конце итерации, владелец при приёмке.

## Итерации (раздел 10 спеки)

| # | Итерация | Статус | Отчёт |
|---|---|---|---|
| И1 | Фундамент: миграция БД, конфиг, `llm_local_mode`, `llm_core/`, retry/usage | ✅ принята владельцем 05–06.09 | `reports/tz4-i1.md` |
| И2 | Коллекторы, волна 1: RSS + Hacker News, дедуп по URL, полный текст | 🔴 **приёмка 11.09 — возврат на доработку И2.1** (HN почти пуст, нет окна по возрасту, догрузка текста не работает, pytest-команда падает) | `reports/tz4-i2.md` |
| И2.1 | Доработка И2 по итогам приёмки — задание в конце `reports/tz4-i2.md` | ⏭ **следующая** | `reports/tz4-i2.md` |
| И3 | Мозг: профиль `ai_value`, golden set + evals, воронка с квотами | ⏸ после приёмки И2.1 | — |
| И4 | Витрина: шаблон `ai_value`, `DIGEST_PROMPT_AI_VALUE`, `knowledge_publisher.py` | — | — |
| И5 | Коллекторы, волна 2: GitHub + Reddit, калибровка порогов | — | — |

## Долги и открытые пункты

- [ ] **СЛЕДУЮЩЕЕ ДЕЙСТВИЕ: исполнитель делает И2.1** — задание и критерий приёмки в конце `specs/reports/tz4-i2.md`. Потом владелец гоняет `bash scripts/tz4_i2_check.sh` на чистой базе (перед этим `mv data/news.db data/news.db.i2`).
- [ ] **B.3 — живой дайджест `spoiler`/`classic` на прод-хосте** после И2.1 (на маке владельца нет прод-базы и LLM).
- [ ] **LLM в облачном режиме не подключится (найдено 11.09):** `LLMClient(timeout=300)` в `analyzer.py`/`api/main.py` получает `api_key="not-needed"` по умолчанию, поэтому `LLM_API_KEY` из `.env` не читается никогда; модель берётся только из env `LLM_MODEL`, ключ `llm_model` в settings.json никто не читает. Чинить в И3 до первого облачного прогона. Подключение — прокси aiprimetech.io из morning-post (`/v1/chat/completions`, `claude-sonnet-5`); у этого эндпоинта `prompt_tokens: 0` — учёт входных токенов по нему врёт.
- [ ] **`analyzer/analyzer.py` в mypy-baseline** (`ignore_errors = True`) — снять при И3, когда файл серьёзно правится.
- [ ] **Golden set для И3** (~25 примеров, раздел 1 и 9 ТЗ) — размечает владелец. Исполнитель без размеченного набора к классификатору не приступает.
- [ ] **Ручные скрипты** (`send_test.py` и подобные) — вынести в `scripts/`, чтобы не лежали в примонтированном томе `data/`.

## Команды приёмки (всё в докере, на хост ничего)

```bash
docker network inspect ai-network >/dev/null 2>&1 || docker network create ai-network   # на машине без llm-stack
docker compose --profile feeds build analyzer collector-feeds
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector   # после И2.1
docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict llm_core
bash scripts/tz4_i2_check.sh   # вся приёмка коллекторов разом, лог в data/tz4_i2_check.log
```

## Как запускается итерация

Исполнителю (Claude Code или субагент) достаточно одной строки:

```
Делаем И<N> по specs/tz4-ai-value.md. Сначала прочитай specs/STATE.md и CLAUDE.md.
Перед кодом — план: файлы + новые зависимости. Жди «ок».
```
