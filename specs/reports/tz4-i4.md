# ТЗ #4 — И4: витрина (шаблон `ai_value`, `DIGEST_PROMPT_AI_VALUE`, `knowledge_publisher.py`)

**Статус:** в работе с 27.09. План одобрен владельцем 27.09. Код пишет Codex по брифу `~/.codex-bridge/briefs/news-radar/tz4-i4-showcase.md`, оркестратор проверяет диф, гоняет приёмку и коммитит.

## Решения владельца 27.09

- **md для базы знаний — отдельный LLM-вызов на статью** (вариант б), а не пересказ JSON дайджеста. Причина: владелец ставит краткое содержание в md выше текста дайджеста.
- Перед И4 включены `analysis_profile: "ai_value"` и `digest_template: "ai_value"` в `settings.json` (`f5b450f`).

## Решения оркестратора по умолчанию (подтверждены владельцем 27.09, откатываются конфигом)

- md пушится в `main` этого репозитория (`OnixFireOne/news-radar`, папка `knowledge/`), как решено 05.09. Ветка — ключ `knowledge.branch`. Минус: локальная `knowledge/` расходится с `main`, пока её не подтянуть.
- `knowledge.enabled: false` и в коде, и в `settings.json`: включает владелец после того, как впишет `GITHUB_TOKEN`.
- Имя файла `YYYY-MM-DD-<slug>-<message_id>.md`: суффикс id делает путь стабильным и без коллизий (расхождение со спекой р.5 — там `YYYY-MM-DD-slug.md`).
- md-вызов идёт задачей каталога `knowledge`; у `openai.chat_completions` — **gpt-6-sol (решение владельца 27.09)**. У остальных профилей задачи нет → `default`.
- Замер «opus 5.5 против sol» на тексте дайджеста — после того как рендер заработает, оценивает владелец. Маршрут «задача → провайдер» делаем, только если opus заметно лучше.

## Что сделано

- **`analyzer/prompts.py`** — `DIGEST_PROMPT_AI_VALUE`, `KNOWLEDGE_MD_PROMPT_AI_VALUE` (`knowledge-v1`), статьи в разделителях; старые промпты не тронуты.
- **`analyzer/renderer.py`** — `render_digest(..., md_map=None)`, ветка `ai_value` → `_render_ai_value`; classic/spoiler не тронуты (регрессионный тест зелёный).
- **`analyzer/knowledge_publisher.py`** (новый, `--strict`) — md с фронтматтером, slug-транслит, пуш через GitHub Contents API, `md_path` в БД и повторное использование, сбой не ломает дайджест.
- **`analyzer/analyzer.py`** — ветка `ai_value` в `generate_digest`: промпт → md → рендер; `content_type` из БД; в SELECT добавлены `url`, `collected_at`, `source_type`, `takeaway`, `md_path`.
- **Конфиг** — `knowledge` (выключен) и расширение `digest_templates.ai_value` (`types`, `show_md_link`, …) в `DEFAULT_CONFIG` и `settings.json`; `GITHUB_TOKEN` в `.env.example`; `knowledge` → gpt-6-sol в `providers.json`.
- **Тесты (+40, существующие не тронуты):** `test_renderer_ai_value.py`, `test_knowledge_publisher.py`, `test_digest_ai_value.py`.
- **docs:** `06_digest.md`, `09_config_hot_reload.md`, `12_llm_providers.md`.
- Решения Codex по ходу: дата md — `collected_at` статьи, дата в заголовке дайджеста — UTC; нет заголовка статьи → первая строка текста.

## Новые зависимости

Нет (`httpx` уже был).

## Расхождения со спекой

- Р.5: имя файла с суффиксом `-<message_id>` (см. решения выше).

## Что НЕ сделано / отложено

- Живой дайджест и живой пуш в GitHub — ждут `GITHUB_TOKEN` и прогона на прод-хосте.
- Замер «opus 5.5 против sol» на тексте дайджеста.

## Команды приёмки

```bash
docker compose build analyzer news-radar-api
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps news-radar-api python -c "import api.main; print('ok')"
```

**Результат 27.09:** 195 passed · mypy `Success: no issues found in 38 source files`.
