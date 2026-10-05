# API: FastAPI endpoints

## Все эндпоинты

```
GET  /feed              — лента с AI-анализом (фильтры: temp, topic, source, hours)
GET  /topics            — топ топиков по avg temperature
GET  /digest/latest     — последний дайджест
GET  /digest             — список дайджестов
POST /digest/generate    — триггер генерации (опционально: hours, force)
POST /digest/raw         — raw messages для агента (без отправки)
POST /digest/queue       — Agent.submit digest → сохранение + отправка
GET  /sources            — список каналов
GET  /sources/{name}/stats — reliability метрики источника
GET  /stats              — система: messages, analyzed, pending, 1h/24h stats
GET  /health             — health check (ChromaDB status)
GET  /search?q=          — семантический поиск (BGE-m3 + ChromaDB)
GET  /similar?id=        — похожие сообщения на данное
GET  /duplicates         — группы near-duplicate сообщений
GET  /trends             — активные тренды (HDBSCAN кластеры)
GET  /trends/{id}/posts  — все сообщения конкретного тренда
GET  /subscriptions      — подписки пользователя
POST /subscriptions      — добавить подписку
DELETE /subscriptions    — удалить подписку
GET  /settings           — текущая конфигурация (settings.json)
PATCH /settings          — обновить конфиг (hot-reload)
GET  /dispatch-log       — аудит dispatch-событий
GET  /alerts             — пуш alert в Telegram (для OpenClaw)
```

## Семантический поиск

```python
@app.get("/search")
async def semantic_search(q: str, limit=10, min_temperature=0.0):
    embedder = get_embedder()
    # CPU-bound encode → thread pool
    loop = asyncio.get_running_loop()
    query_embedding = await loop.run_in_executor(None, embedder.encode, q)

    chroma = get_chroma()
    results = chroma.search(query_embedding=query_embedding, limit=limit)

    # Обогащаем данными из SQLite
    for r in results:
        row = conn.execute("""SELECT text, collected_at, s.name
            FROM messages m JOIN sources s ON m.source_id=s.id WHERE m.id=?""",
            (r["message_id"],)).fetchone()
```

**Важно**: encode() — CPU-bound, запускается в `run_in_executor` чтобы не блокировать event loop.

## /settings PATCH

```python
@app.patch("/settings")
async def patch_settings(updates: dict):
    current = _read_settings()
    for key, raw_value in updates.items():
        expected_type = _SETTINGS_SCHEMA[key]
        if expected_type is bool:
            value = str(raw_value).lower() in ("true", "1", "yes")
        else:
            value = expected_type(raw_value)
        current[key] = value
    _write_settings(current)
    return {"status": "applied", "updated": applied}
```

Изменения применяются в течение 3 секунд (watchdog детектит изменение → ConfigWatcher.fire callbacks).

## /digest/queue (Agent flow)

```python
@app.post("/digest/queue")
async def queue_digest(digest: DigestQueueRequest):
    # 1. Сохраняем narrative text в digests table
    conn.execute("""INSERT INTO digests (content_md, period_start, period_end)
        VALUES (?, ?, ?)""", (digest.narrative_text, start, end))

    # 2. Подтверждаем обработанные сообщения
    conn.execute("UPDATE messages SET in_digest=1 WHERE in_digest=2")

    # 3. Отправляем в Telegram
    for uid in allowed_users:
        await client.post(f"sendMessage", json={"chat_id": uid, "text": digest.narrative_text})
```

Этот endpoint — точка входа для OpenClaw Agent. Agent получает raw messages → пишет narrative → шлёт сюда → news-radar отправляет.
## Статистика именованного дайджеста (И4.1, шаг 2d)

При `digest_stats.enabled=true` успешный `POST /digest/generate?name=articles`
добавляет строку `stats` к существующему ответу с `parts`. Статистика общая для всех
сохранённых частей запуска. Если сохранённых частей нет (например, `dispatched`),
ключа нет. Ошибка расчёта даёт WARNING и не меняет успешный ответ дайджеста.

`GET /digest/stats?name=articles` возвращает `{"text": "📊 …"}` для последнего
выпуска этого имени; без имени выбирается первый включённый дайджест.
Неизвестное имя или отсутствие выпусков — HTTP 404. Запрос доступен независимо
от флага автоматической доставки; в Telegram его вызывает админская `/stats`.

Окно UTC: от `created_at` последней части предыдущего выпуска **того же имени**
до `created_at` последней части текущего; первый выпуск — от своего `period_start`.
Границы `[since, until)`, части объединены через `run_id`. Время в тексте — Москва.
Собрано/разобрано/реклама/очередь относятся к сообщениям, **собранным в окне**,
из источников настроенных включённых категорий дайджеста. Разобрано и очередь —
текущее состояние этой группы, а не число переходов состояния в окне; `/stats`
может показать обновлённые значения после доанализа. Порог — `min_value_score`
с учётом `category.params`, по умолчанию 5, без рекламы; для категорий без квот — н/д.
Это подсчёт по оценке до квот и дедупликации, не повтор полного отбора.
«В выпуске» — уникальные выбранные сообщения сохранённых частей с `in_digest=1`,
включая выбранные из более раннего окна; md — среди них с `analysis.md_path IS NOT NULL`
(в том числе повторно использованные файлы).

«Ждут оценки» (до 04.10 — «в очереди») — сообщения окна, ещё не прошедшие классификатор. «Очередь выпуска»
(04.10) — только для квотных категорий с `carryover_days` > 0: статьи источников категории, не реклама,
`in_digest=0`, оценка не ниже порога, собранные за `carryover_days` до момента выпуска, по целой оценке;
«выпадет за сутки» — из них старше `carryover_days − 1` дня; «выпало без выпуска» — такие же статьи, чей срок
истёк в окне отчёта. `in_digest` — текущее состояние: в позднем `/stats` взятые следующими выпусками не считаются.

LLM группируется по задаче и категориям за то же окно. Вызовы без категории
не приписываются именованным дайджестам. Стоимость суммирует известные цены;
при NULL хотя бы в одной строке показаны «≈» и «цена неизвестна».
Если категория входит в несколько дайджестов, её расходы могут присутствовать
в отчётах каждого: это окна наблюдения, а не распределение общего счёта между выпусками.
Учёт начинается после обновления; статистика исторических расходов неполна.
