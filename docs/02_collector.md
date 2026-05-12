# Collector: Telegram Userbot

## Как работает

Collector — это **Telethon userbot**, а не бот. Разница:
- Бот: ограничен командами `/start @ChannelName`
- Userbot: читает ВСЕ сообщения во всех каналах, где есть аккаунт

При старте запрашивает SMS-код (`TelegramClient.start()`). Сессия сохраняется в `data/sessions/news_radar.session`.

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