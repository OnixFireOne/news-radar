# TrendTracker: детекция трендов

## Идея

> Когда МНОЖЕСТВО каналов независимо пишут об одном и том же — это и есть горячая новость.

Не нужно ML для детекции трендов. Нужно:
1. Взять эмбеддинги всех сообщений за последние часы
2. Кластеризовать семантически похожие
3. Посчитать, из скольких разных каналов они пришли

## Flow

```python
async def run_cycle(self):
    # 1. Fetch recent analyzed+embedded messages из SQLite
    messages = self._fetch_recent_messages()

    # 2. Get embeddings из ChromaDB в одном batch call
    embeddings, valid = self._fetch_embeddings(messages)

    # 3. HDBSCAN на предвычисленных эмбеддингах
    clusters = await self._cluster_messages(valid, embeddings)

    # 4. Filter: только кластеры с ≥3 уникальными каналами
    significant = [c for c in clusters if c.unique_sources >= min_unique_sources]

    # 5. LLM naming топ-N кластеров (по trend_score)
    for cluster in significant[:10]:
        await self._name_cluster(cluster)

    # 6. Mark old trends as dead
    self._expire_old_trends()

    # 7. Upsert в SQLite
    count = self._upsert_trends(significant)
```

## HDBSCAN clustering

```python
def _run_hdbscan():
    arr = np.array(embeddings, dtype=np.float32)
    clusterer = HDBSCAN(
        min_cluster_size=2,             # кластер может начинаться с 2 сообщений
        min_samples=1,
        metric="euclidean",             # для нормализованных = cosine-equivalent
        cluster_selection_epsilon=0.25, # merge clusters ближе чем 0.25 расстояния
        core_dist_n_jobs=1,             # однопоточно для Docker
    )
    return clusterer.fit_predict(arr)
```

**Почему не BERTopic**: BERTopic заново кодирует тексты. У нас уже есть эмбеддинги в ChromaDB. Используем HDBSCAN напрямую — экономим память и CPU.

**Проблема с epsilon**: 0.35 — слишком мало кластеров. 0.15 — слишком много (мелкие группы объединяются в один шум). Стало 0.25 через конфиг, tunable через `trend_hdbscan_epsilon`.

**Fallback**: если HDBSCAN не установлен — группировка по LLM topic label (менее точно, но работает).

## TrendScore formula

```python
@dataclass
class TrendCluster:
    @property
    def compute_trend_score(self) -> float:
        # unique_sources × avg_temperature × recency_factor × (1 + log(1 + avg_views))
        return (
            self.unique_sources
            * self.avg_temperature
            * math.exp(-0.3 * self.hours_since_first)  # recency decay
            * (1.0 + math.log1p(self.avg_views))       # log-safe для views=0
        )
```

`unique_sources` — самый важный множитель. 10 каналов о том же = важнее, чем 1 канал с горячим постом.

`recency_factor = exp(-0.3 × hours)`:
- 0 часов: 1.0
- 2.3 часа: 0.5
- 6 часов: 0.17

Новые тренды ранжируются выше.

## Жизненный цикл

```
emerging (1-2 канала, только появился)
    │
    ▼ (5+ каналов, velocity > 3/hour)
   hot  ←── alert.fire()
    │
    ▼ (velocity < 1/hour, каналы перестали писать)
 cooling
    │
    ▼ (>6 часов без обновлений)
   dead
```

`velocity = message_count / hours_span` — постов в час внутри кластера.

## Effective source (unique counting)

```python
# При построении кластера:
sources = [
    m.get("forward_from_channel") or m["source_name"]
    for m in messages
]
unique_sources = len(set(sources))
```

Если канал переслал сообщение из другого — оригинальный канал получает credit.
Иначе агрегатор с 50K подписчиков, который пересылает всё подряд, накручивал бы unique_sources.

## Message overlap merging (проблема из коммита)

```python
# Проблема: HDBSCAN может разбить один тренд на 2 кластера
# (особенно при изменении epsilon или на границе time window)
# Решение: если два кластера делят ≥2 сообщений — они один тренд

MESSAGE_OVERLAP_MERGING:
if not existing and len(cluster.message_ids) >= 2:
    row = conn.execute(f"""
        SELECT t.id, COUNT(*) as overlap
        FROM trends t
        JOIN trend_messages tm ON tm.trend_id = t.id
        WHERE tm.message_id IN ({placeholders})
          AND datetime(t.last_seen) >= datetime(?)
        GROUP BY t.id
        HAVING overlap >= 2
        LIMIT 1
    """, ...).fetchone()
```

**Кейс**: один тренд (например, "ETH ETF approval") сначала кластеризуется как "ETF news", потом при новых сообщениях — как "Ethereum". Message overlap merging мерджит их.

## Hot Trend alert

```python
if cluster.unique_sources >= min_sources and not alerted_at_val and is_fresh:
    conn.execute("UPDATE trends SET alerted_at=CURRENT_TIMESTAMP WHERE id=?", ...)
    asyncio.create_task(self.analyzer._route_event("hot_trend", {...}))
```

Alert.fire() только ОДИН РАЗ: когда `alerted_at IS NULL` и порог crossing. Последующие запуски цикла не пере-алертят тот же тренд.