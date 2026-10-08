# Config Hot-Reload: ConfigWatcher

## Проблема

Раньше конфигурация читалась один раз при старте. Чтобы изменить `breaking_alert_min_temp` — нужен был рестарт контейнера. В Docker это downtime.

**Решение**: файловый watcher, callback-driven обновления.

## Как работает

```python
class ConfigWatcher:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self._callbacks: dict[str, list[Callable]] = {}
        self._change_event = asyncio.Event()

    def on_change(self, key: str, callback: Callable) -> None:
        self._callbacks.setdefault(key, []).append(callback)

    async def watch(self) -> None:
        # watchdog PollingObserver в фоновом потоке
        observer = PollingObserver(timeout=3)  # каждые 3 секунды
        observer.schedule(Handler(), str(self.config_path.parent))

        while True:
            await self._change_event.wait()  # ждём сигнала от watchdog
            self._change_event.clear()
            await asyncio.sleep(0.2)          # debounce (некоторые редакторы пишут в 2 прохода)
            await self._apply_changes()       # перечитать файл, fire callbacks
```

## PollingObserver (не inotify)

```python
# Комментарий из кода:
"""
Inotify НЕ работает внутри Docker Desktop на Windows:
файловые изменения с хоста невидимы для Linux inotify.
PollingObserver проверяет mtime каждые 3 секунды — работает на всех платформах.
"""
```

**Trade-off**: 3 секунды latency вместо мгновенного. Приемлемо для настроек.

## Callback examples

```python
# Collector: hot-reload folder filter
async def on_folder_change(new_folder: str):
    await collector._sync_dialogs(folder_name=new_folder)
cfg.on_change("telegram_folder", on_folder_change)

# Collector: hot-reload min_length
def on_min_length_change(new_val: int):
    collector.min_length = new_val
cfg.on_change("min_message_length", on_min_length_change)

# Analyzer: llm_thinking_mode перечитывается при каждом анализе
# (не через callback, а через get() в момент вызова — hot enough)
thinking_mode = self.cfg.get("llm_thinking_mode", "full")
```

## Env vars override

```python
def _load(self) -> dict:
    config = dict(DEFAULT_CONFIG)

    if self.config_path.exists():
        with open(self.config_path) as f:
            data = json.load(f)
        for key in DEFAULT_CONFIG:
            if key in data:
                config[key] = data[key]

    # Env vars всегда важнее файла
    env_map = {
        "telegram_folder": os.environ.get("TELEGRAM_FOLDER", ""),
        "min_message_length": int(os.environ.get("MIN_MESSAGE_LENGTH", 0)) or None,
    }
    for key, val in env_map.items():
        if val:
            config[key] = val

    return config
```

Это позволяет переопределять параметры через `docker run -e` без редактирования файла.

## Слияние только верхнего уровня

`_load()` выше копирует значение из файла **целиком**, по ключам верхнего
уровня `DEFAULT_CONFIG` — вложенного слияния нет. Для блока вроде `sources`
это значит: если `settings.json` определяет `sources`, то он определяет его
полностью, и подключи из `DEFAULT_CONFIG` (`telegram`, `fulltext`, ...) в
итоговый конфиг **не подмешиваются**.

Отсюда два правила для новых вложенных ключей:

1. Ключ добавляется в оба места — `DEFAULT_CONFIG` и `settings.json`. В
   `DEFAULT_CONFIG` его отсутствие в файле не спасёт.
2. Читать его нужно с безопасным дефолтом на месте чтения, а не полагаться
   на `DEFAULT_CONFIG`: `sources.get("telegram", {}).get("enabled", True)`.
   Так деплой со старым `settings.json` сохраняет прежнее поведение.

## topics.json (отдельный файл)

```python
def load_topics(self) -> dict:
    # topics.json рядом с settings.json
    topics_path = self.config_path.parent / "topics.json"
    # Формат: {"bitcoin": {"aliases": ["btc", "биткоин"], "alert": false}, ...}
    # Ключи с _ игнорируются (мета-данные)
    return {k: v for k, v in data.items() if not k.startswith("_")}
```

Отдельный файл, не часть settings.json — hot-reload по той же схеме. Загружается при каждом вызове `_normalize_topic()`.
`llm_local_mode` действует только в legacy-режиме (пустая `LLM_PROVIDERS`).

`llm_strict_json_tasks` (01.10) — задачи каталога, у которых JSON-ответ берётся через схему-инструмент в строгом
режиме: `[]` по умолчанию (как раньше — JSON из текста), в `settings.json` — `["knowledge", "digest", "classify"]`.
Работает, только если вызов передаёт схему (`analyzer/json_schemas.py`; у классификатора — своя) и у провайдера
в `config/providers.json` стоит `"strict_tools": true` (сейчас только `openai.chat_completions`); иначе схема
уходит без `strict` или JSON разбирается из текста. Читается на каждом цикле анализа и каждом выпуске.
В режиме каталога локальные особенности задаёт активный профиль; провайдеры
и их настройки применяются при запуске процесса, без hot reload.

## Ключи ТЗ #4, И3 шаг 5

- `categories.<имя>.analyzer` — `"crypto"` | `"ai_value"`, читается на каждом цикле анализа (заменил `analysis_profile` в И4.1).
- `knowledge` (И4) — `enabled` (дефолт false), `repo`, `branch`, `dir`, `min_value_score`, `max_input_chars`, `targets` (И4.1: `["github"]` по умолчанию; в `settings.json` с И4.4 — `[]`, разборы только на сайт, см. `06_digest.md`), `batch_commit` (дефолт `false`, в `settings.json` — `true`: один коммит на прогон), `format` (`brief` по умолчанию, в `settings.json` — `full`; там же `max_input_chars` 40000 (с 30.09; было 24000) — при `split_over_chars` > 0 не режет; `split_over_chars` — 0 по умолчанию, в `settings.json` 150000: статья целиком, длиннее — через заметки по частям). `sources.fulltext.include_comments` — комментарии читателей в полном тексте (`true` по умолчанию, как `trafilatura`; в `settings.json` `false`). `digest_templates.ai_value.carryover_days` — пул с переносом для отбора по квотам (0 по умолчанию — окно «с прошлого выпуска»; в `settings.json` — 7); `digest_templates.ai_value.tie_break` (`temperature` по умолчанию, в `settings.json` `oldest`) и `candidates_list` (`{"enabled": false, "min_score": 6}` по умолчанию, в `settings.json` включён) — с 01.10, см. `06_digest.md`; токен — env `GITHUB_TOKEN`. Читается на каждом дайджесте.
- `digest_templates.ai_value` — квоты воронки, с И4 ещё `types` (эмодзи и метка по `content_type`), `show_md_link`, `title_max_words`, `summary_max_sentences`, `text_max_chars`. Из-за слияния только верхнего уровня блок продублирован целиком и в `DEFAULT_CONFIG`, и в `settings.json`: если в `settings.json` есть `digest_templates`, дефолтный блок не подмешивается.

## Категории и расписание (И4.1, шаг 2b)

Новые ключи верхнего уровня: `categories` (по умолчанию `{}`), `digests` (по умолчанию `[]`).
`analysis_profile` удалён из DEFAULT_CONFIG и settings.json; анализатор теперь выбирается категорией.
В поставляемом конфиге включены `articles` (RSS/HN, `ai_value`, без алертов и подписок, md в local)
и дайджест `articles` в 09:10 Europe/Moscow. Категория и дайджест `crypto` выключены;
для включения нужны оба `enabled: true`, а для новых Telegram-записей — также `sources.telegram.enabled`.
Крипта сохраняет слоты 12:00/20:00 Europe/Moscow и шаблон spoiler.

Поля категории: `enabled`, `sources`, `analyzer`, `hooks`, `select`, `template`, `extras`,
необязательный `params` (переопределяет значения `digest_templates.<template>` на верхнем уровне).
Неизвестный кирпичик — ERROR и пропуск категории; спор за источник — WARNING, первая категория выигрывает.
Пустой список источников разрешён только внутреннему legacy-набору, не расширяет настроенную категорию на всё.
У `ai_value` без каталога провайдеров — WARNING и пропуск цикла, без подмены крипто-анализатором.

Анализатор запускает `cfg.watch()` вместе с основным циклом и перечитывает категории перед анализом,
проверкой очереди и циклом трендов. API читает свежий файл на запрос. Бот получает полный `/settings`
при старте и каждые 60 секунд, заменяет только задания `digest:*` при изменении слотов или часового пояса.
Ошибка API после старта сохраняет последнее рабочее расписание; при первом недоступном API — старые
12:00/20:00 МСК. Непустой список с выключенными дайджестами означает отсутствие заданий.

`quotas.crypto` и `crypto_min_temperature` удалены из настроек шаблона статей; отсутствие крипто-квоты
уже трактуется `value_funnel` как 0. Новых переменных окружения и зависимостей нет.

## Статистика дайджеста

`digest_stats: {"enabled": false}` объявлен в `DEFAULT_CONFIG`; в рабочем
`settings.json` установлен `true` по решению владельца. Ключ управляет добавлением
`stats` в ответ генерации именованного дайджеста и автоматической доставкой админам.
API читает актуальный конфиг при каждом запросе; перезапуск для флага не нужен.
Постоянный учёт вызовов в `llm_usage` и ручная `/stats` не отключаются этим флагом.

## Публикация на сайт (И4.3)

`site` читается на каждом выпуске, объявлен в `DEFAULT_CONFIG` и `settings.json`:

| Ключ | Дефолт кода | Назначение |
|---|---|---|
| `enabled` | `false` | Запись разборов и дайджеста на сайт; в settings `true` |
| `live` | `false` | После успешного коммита Telegram-ссылки разборов ведут на сайт |
| `repo` | `OnixFireOne/neuronavt` | Репозиторий сайта |
| `branch` | `radar-preview` | Ветка предпросмотра |
| `base_url` | `https://neuronavt.blog` | Публичные URL выпусков и разборов |
| `posts_dir` | `blog/src/content/posts/_digests` | Каталог дайджест-постов |
| `reviews_dir` | `blog/src/content/reviews` | Каталог разборов |
| `digest_slug` | `{date}-ai-radar` | Имя поста с датой UTC `YYYY-MM-DD` |

Extra `site` должен идти после `knowledge`. Площадка сайта не добавляется в `knowledge.targets`.
При `enabled: true` список кандидатов переезжает внутрь поста, отдельного md и Telegram-ссылки списка нет.
Env `NEURONAVT_GITHUB_TOKEN` описан в `.env.example`; его добавление требует пересоздания
контейнера, hot reload применим только к JSON. Публикация на сайт пропускается без токена;
сбой не мешает выпуску Telegram. Детали путей и fallback — `06_digest.md`.
