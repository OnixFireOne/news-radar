# Бриф Codex: И4.3, шаг 3 — «Главное за день» и анонс выпуска в Telegram

Репозиторий: `~/Documents/Ai/apps/news-radar`. Сначала `AGENTS.md` → `CLAUDE.md` (жёсткие правила), затем
`specs/reports/tz4-i4.3.md` (решения владельца, шаг 2 — что уже сделано) и `docs/06_digest.md` («Публикация на сайт»).

## Цель

1. В черновике дайджеста `ai_value` появляются поля **`lead`** («Главное за день», 1–2 предложения) и
   **`highlights`** (2–3 коротких пункта «что интересного»). Сайт уже умеет показывать `lead`
   (`analyzer/site_digest.py`, блок `radar-lead` выводится, только если `lead` непустой).
2. Новый режим Telegram для шаблона `ai_value`: вместо полного дайджеста — **короткий анонс** со ссылкой на выпуск на сайте.
   Режим по умолчанию — прежний полный дайджест.
3. Перед отправкой анонса — дождаться, пока страница выпуска на сайте откроется (деплой занимает ~1–2 мин).

Принятый владельцем вид анонса (HTML Telegram):
```
🤖 <b>AI-радар — 7 октября</b>
8 статей: 3 инструмента, 3 практики, 2 исследования

Интересное:
• Claude Code: фоновые агенты
• RAG-индекс в 4 раза меньше
• Агенты сами чинят промпты

<a href="https://neuronavt.blog/posts/2026-10-07-ai-radar/">Читать выпуск на сайте →</a>
```

## Что сделать

### 1. Промпт и схема — новая версия, выбор в конфиге
- `analyzer/prompts.py`: **не менять** `DIGEST_PROMPT_AI_VALUE`. Добавить `DIGEST_PROMPT_AI_VALUE_V2` — тот же текст плюс
  `lead` и `highlights` в JSON. Требования: на русском; `lead` — 1–2 предложения, о главном за день по **этим** статьям,
  без выдуманных фактов; `highlights` — 2–3 пункта, каждый до ~8 слов, только о статьях выпуска (по одному на статью,
  самые полезные). Правило про недоверенный текст между разделителями — как в v1.
- Реестр версий по образцу `AI_VALUE_PROMPTS` (`analyzer/value_classifier.py`): например `DIGEST_AI_VALUE_PROMPTS = {"ai_value-digest-v1": (prompt, schema), "ai_value-digest-v2": (...)}`.
- `analyzer/json_schemas.py`: `DIGEST_AI_VALUE_SCHEMA` **не менять**; новая `DIGEST_AI_VALUE_SCHEMA_V2` — `items` как было,
  `lead` (string), `highlights` (array of string). Учти strict-режим провайдера (`llm_strict_json_tasks` содержит `digest`):
  в strict все поля объекта должны быть в `required` — посмотри, как устроен `_object`/`JsonSchemaTool`, и сделай так же.
- Конфиг (в ДВА места): `digest_templates.ai_value.digest_prompt_version` — `DEFAULT_CONFIG`: `"ai_value-digest-v1"`,
  `settings.json`: `"ai_value-digest-v2"`. Неизвестная версия → WARNING и v1.
- `analyzer/pipeline/writers.py::_ai_value_compose` выбирает промпт и схему по версии. Пустые/неверные `lead`/`highlights`
  не ломают выпуск: просто отсутствуют.

### 2. Анонс — режим `telegram`
- Конфиг (в ДВА места): `digest_templates.ai_value.telegram` — `"full"` (по умолчанию в обоих местах; владелец переключит
  на `"announce"` вместе с `site.live: true`).
- Функция `build_telegram_announce(draft, selected, digest_url, template_cfg, now) -> str` — **в `analyzer/site_digest.py`**
  (рядом со склонениями и подписями типов; переиспользуй их, не дублируй). `renderer.py` **не трогать**.
  - заголовок `🤖 <b>AI-радар — {день} {месяц}</b>`; строка «N статей: …» — та же разбивка, что в `description` поста;
  - «Интересное:» + пункты из `highlights`; если `highlights` нет — первые 3 заголовка из `items`;
  - ссылка «Читать выпуск на сайте →» на `digest_url`;
  - всё из LLM — `html.escape`; URL в `href` — экранирован с кавычками; итог короче 1000 символов (обрезать пункты).
- `_ai_value_render`: если `telegram == "announce"` **и** в artifacts есть `site_digest_url` (коммит на сайт прошёл) —
  вернуть `(build_telegram_announce(...), "HTML")`; иначе — прежний полный дайджест (WARNING «announce fallback: no site digest»).
  Так сбой сайта никогда не оставляет выпуск без текста.

### 3. Ожидание страницы
- Ключ `site.wait_for_page_sec` (в ДВА места, по умолчанию `300`).
- В extra `site` после успешного коммита, **только если `site.live: true`**: опрашивать `GET site_digest_url`
  (httpx, без ретраев на каждый чих: раз в ~15 с) до кода 200 или таймаута. Таймаут → WARNING, выпуск идёт дальше.
  При `live: false` (ветка предпросмотра не деплоится) — не ждать.
- В тестах ожидание не должно спать реально: интервал и часы/`sleep` — параметр или monkeypatch.

### 4. Документация
`docs/06_digest.md` — «Главное за день», версии промпта, режим `telegram`, ожидание страницы, порядок включения:
`site.live: true` + `telegram: "announce"` (+ `branch: "main"`).

## Ограничения
- Шаблоны `classic`/`spoiler`, их промпты, `renderer.py`, `DIGEST_PROMPT_AI_VALUE` и `DIGEST_AI_VALUE_SCHEMA` — ни строки.
- Новых зависимостей нет. Новые файлы — в `files=` `mypy.ini`, `--strict`. Существующие тесты не менять.
- Комментарии в коде — английские. Docker и `.git` недоступны: pytest/mypy и коммит — Claude Code.

## Тесты (новый файл, напр. `tests/test_site_announce.py`, без сети)
1. v2: промпт содержит `lead`/`highlights` и правило про недоверенный текст; выбор версии по конфигу; неизвестная → v1.
2. `build_telegram_announce`: формат как выше, экранирование `<script>`/кавычек, fallback на заголовки без `highlights`,
   длина < 1000, склонения («1 статья», «2 статьи», «5 статей»).
3. `_ai_value_render`: `announce` + `site_digest_url` → анонс; `announce` без `site_digest_url` → полный дайджест;
   `full` → полный (байт-в-байт как раньше на том же черновике).
4. Ожидание: 404, 404, 200 → успех без реального сна; вечный 404 → таймаут, WARNING, без исключения; `live: false` → ни одного запроса.

## Приёмка (Claude Code, в докере)
```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
```

## Ответ
Кратко: файлы, как устроен выбор версии промпта, что спорно.
