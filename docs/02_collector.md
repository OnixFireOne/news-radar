# Collector: Telegram Userbot

## Как работает

Collector — это **Telethon userbot**, а не бот. Разница:
- Бот: ограничен командами `/start @ChannelName`
- Userbot: читает ВСЕ сообщения во всех каналах, где есть аккаунт

При старте запрашивает SMS-код (`TelegramClient.start()`). Сессия сохраняется в `data/sessions/news_radar.session`.

## Выключатель: `sources.telegram.enabled`

ТЗ #4 И3 поставил крипту на паузу, не удаляя коллектор. `main()` читает
`sources.telegram.enabled` из `settings.json` **до** чтения `TELEGRAM_*` из
окружения — выключенному деплою credentials не нужны вовсе.

```python
if not _is_telegram_enabled(cfg.get("sources", {})):
    logger.info("Telegram collector disabled (sources.telegram.enabled=false) — idling.")
    while True:
        await asyncio.sleep(3600)
```

Сервис **идлит, а не выходит**: у него `restart: unless-stopped`, и выход
превратился бы в рестарт-луп. Тот же паттерн — в `collectors/poll_runner.py`,
когда не включён ни один poll-коллектор.

`sources.fulltext.include_comments` (30.09): передаётся в `trafilatura.extract`; `trafilatura` 2.2 по умолчанию включает комментарии читателей в текст статьи. В `settings.json` — `false`; ключ оставлен, чтобы позже разбирать комментарии.

Отсутствие ключа означает «включён»: деплой со старым `settings.json`
продолжает собирать как раньше. Возврат крипты — это правка одного значения
в конфиге, без изменений кода и compose.

## Startup: три concurrent задачи

```python
await asyncio.gather(
    listen_loop(),       # 1. Real-time listener (запускается ПЕРВЫМ)
    catchup_then_done(), # 2. Catchup missed messages
    cfg.watch(),         # 3. Hot-reload config watcher
)
```

**Проблема с очередностью**: раньше collector запускал catchup ДО listen. За это время (30+ секунд) могли прийти новые сообщения — и они терялись.

**Решение**: listen_loop запускается сразу при.connect(), а catchup работает параллельно. `INSERT OR IGNORE` в `_save_message` защищает от дубликатов.

## Smart Catchup

При первом старте для каждого канала:

```
Есть unread > 0?  →  Fetch messages от last_read_id (всё пропущенное)
Первый раз?        →  Fetch последние N сообщений (load_history_limit)
unread = 0?        →  Skip (не нужен ни один API-запрос)
```

Это экономит API-лимиты Telegram: каналы без новых сообщений не трогаются.

## Folder filter

```python
# settings.json
telegram_folder: "Ton/DeFi"
```

Collector читает **Telegram folders** через `GetDialogFiltersRequest`. Берёт только те каналы, которые лежат в папке `Ton/DeFi`. Это позволяет фильтровать каналы прямо в Telegram, не редактируя код.

## Metadata sync

При sync-е каждого канала одновременно upsert-ится metadata:
- subscribers, description, verified, scam
- linked_chat_id, channel_created

Commit происходит **после каждого канала**, до `fetch_history`:

```python
conn.execute("""INSERT OR IGNORE INTO sources (...) VALUES (...)""")
conn.commit()  # ← отпускает write lock перед fetch_history
```

**Проблема**: `_save_message()` открывает свой write connection. Если держать здесь не-committed изменения — SQLite WAL отказывает второму writer-у ("database is locked").

**Решение**: коммитить metadata до fetch_history.

## Mark as read

После сохранения каждого сообщения:
```python
await self.client.send_read_acknowledge(event.chat_id, max_id=event.id)
```
Не фаatal: если не получилось (нет прав) — логим и продолжаем. Визуально — на стороне аккаунта снимает непрочитанные счётчики.

## Forward tracking

Когда канал пересылает сообщение из другого канала:
```python
forward_from_channel = str(msg.fwd_from.from_id.channel_id)
forward_from_msg_id  = msg.fwd_from.channel_post
```

Эти поля используются в TrendTracker для "unique sources" counting: если агрегатор пересылает сообщение — оригинальный канал получает credit, а не агрегатор.

## Hot-reload на папку

```python
async def on_folder_change(new_folder: str):
    await collector._sync_dialogs(folder_name=new_folder)

cfg.on_change("telegram_folder", on_folder_change)
```

При изменении `telegram_folder` в settings.json — collector пересматривает список каналов.
## Полный текст статей: `sources.fulltext` (ТЗ #4, после И3 шаг 5)

`FullTextFetcher` (trafilatura) общий для RSS и HN. Ключи:

- `mode` — `"short_only"` (дефолт в коде): страница качается, только если чистый анонс < 500 символов; `"always"` (в `settings.json`): для каждой новой RSS-записи, берётся более длинный из «анонс / страница». Хабр отдаёт тизеры 300–1500 символов с «Читать далее» — порог 500 их не ловил. HN качает страницу всегда, независимо от режима.
- `max_per_cycle` / `max_per_feed` — 60 / 15 в `settings.json` (дефолт 20 / 5). Задержка 2 с на домен и блок домена после 401/403 — без изменений.
- **Упёрлись в лимит → запись откладывается**, а не сохраняется тизером: не выдаётся и не помечается увиденной, следующий цикл берёт её снова (окно `max_age_hours` действует). Признак — `fetcher.last_capped` (проверять `is True`). Раньше такая запись навсегда оставалась анонсом.
- openai.com отвечает 403 на любого бота — там остаётся анонс.
