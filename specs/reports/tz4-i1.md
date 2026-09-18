# И1 — Фундамент · отчёт

> Восстановлен 10.09.2026 по коммитам и переписке приёмки. Оригинальный отчёт исполнителя вёлся в чате и в репо не сохранялся.

**Статус:** ✅ принята владельцем · **Даты:** 05–06.09.2026

## Что сделано

- **Эталонный тест рендерера** (отдельным коммитом ДО любых изменений): фиксирует вывод шаблонов `classic` и `spoiler` байт-в-байт, чтобы регрессия прода была видна сразу.
- **Пакет `llm_core/`** — портирована архитектура из `OnixFireOne/morning-post` (`src/ai/`): `client.py`, `config.py`, `providers.py`, `usage.py`, `usage_report.py`, `validator.py`, `mask.py`, `list_models.py`. Без импортов из `analyzer`/`bot`, конфиг передаётся снаружи.
- **Переключатель `llm_local_mode`** (локальная модель / облачный прокси): при `false` в облако не уходят `chat_template_kwargs` и не создаётся lock-файл.
- **Retry на 429/5xx**, учёт расхода токенов по каждому вызову в логах.
- **Миграция БД** — фундамент для дедупа по URL, идемпотентная (есть тест).
- **Конфиг-блоки** — в `DEFAULT_CONFIG` и `settings.json` одновременно; новые переменные — в `.env.example` с комментариями.

**Объём:** 20 файлов, +1011 / −69. Тесты: 26 passed.

## Коммиты

| Хеш | Заголовок |
|---|---|
| `ddad8f2` | test: lock in classic/spoiler renderer output before ТЗ4 И1 |
| `4aebfbd` | feat: ТЗ #4 И1 — llm_core package, local/cloud LLM toggle, DB migration foundation |
| `110a265` | fix: make `python -m mypy` pass end-to-end without touching legacy code |
| `1b9ec95` | fix: make docker compose run --rm analyzer actually work for ТЗ4 checks |
| `ce5b4d7` | fix: un-baseline database.schema — fix its 2 pre-existing mypy errors |

## Новые зависимости

Нет. `tenacity` и `respx` уже присутствовали в `analyzer/requirements.txt` до итерации.

## Расхождения со спекой

Нет. Спека обновлена до v1.4 по итогам итерации (`d3e9e78`): дефолт `llm_local_mode`, требования к типизации, дисциплина тестов.

## Побочные находки (важно для следующих итераций)

1. **Общий mypy в проекте никогда не работал.** Дублирование модулей («Source file found twice») из-за отсутствия `analyzer/__init__.py` + inline-комментарий, из-за которого `no_implicit_optional` вообще не применялся. Починено через `explicit_package_bases = True` (безопаснее, чем добавлять `__init__.py` — не меняет рантайм-импорты) и вынос комментария на отдельную строку.
2. **106 старых ошибок mypy забейзлайнены**, а не починены — `ignore_errors = True` по семи legacy-файлам. Владелец поймал, что `analyzer.analyzer` и `database.schema` — файлы рабочие, а не legacy, и глушилка накрыла бы новый код. `database/schema.py` вычищен и снят с baseline (`ce5b4d7`); `analyzer/analyzer.py` оставлен в baseline с пометкой **снять при И3**.
3. **`docker-compose.yml`** не монтировал `./llm_core` и `mypy.ini` в контейнер `analyzer` (код монтируется томами, а не копируется в образ) — команды приёмки владельца физически не работали.
4. **`pytest.ini`** без `testpaths = tests` — pytest сканировал весь `/app`, включая примонтированную `./data`, и подхватывал пользовательские скрипты (`send_test.py`) как тест-модули.
5. **Hot-reload gap:** смена `llm_local_mode` требует рестарта контейнера, конфиг-вотчер её не подхватывает. Для владельца приемлемо (рестарт при деплое), но зафиксировано.

## Нарушения процесса

- Исполнитель гонял `pip install` **на хосте** для локальной итерации — прямое нарушение Docker-only. Признано, пакеты с хоста удалены, `.mypy_cache/` вычищен. По итогам правило Docker-only внесено в спеку (v1.5) и в `CLAUDE.md`.
