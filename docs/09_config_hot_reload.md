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