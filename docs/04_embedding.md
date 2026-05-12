# Embedding & ChromaDB: семантический слой

## Зачем нужны эмбеддинги

Два применения:
1. **Дедепликация**: похожие сообщения из разных каналов о том же событии
2. **Поиск**: найти новости по смыслу, а не по ключевым словам

Текст "Bitcoin упал на 5%" и "BTC dump 5%" — семантически одно и то же.
Keyword search это не найдёт. Vector search — найдёт.

## BGE-m3

```python
# embedder.py
class Embedder:
    def encode(self, text: str) -> list[float]:
        # BAAI/bge-m3: 1024 dimensions, cosine similarity
        vector = self._model.encode(
            text,
            normalize_embeddings=True,   # cosine = dot product
            show_progress_bar=False,
        )
        return vector.tolist()  # list[float], не np.array
```

`normalize_embeddings=True` — после нормализации косинусное сходство = dot product. Это то, что использует ChromaDB по умолчанию.

**Почему BGE-m3:**
- Мультиязычный (русский + английский — основные языки крипто-каналов)
- 1024 dimensions — хороший tradeoff speed/quality
- Open source, работает локально, ~570MB
- `sentence-transformers` вместо Ollama: не нужен отдельный сервис, загружается один раз в процесс

## ChromaDB

```python
# chroma_client.py
class ChromaClient:
    def _connect(self, force=False):
        import chromadb
        self._client = chromadb.HttpClient(host=self.host, port=self.port)
        self._collection = self._client.get_or_create_collection(
            name="news_radar",
            metadata={"hnsw:space": "cosine"},  # косинусное расстояние
        )
```

Каждый document = одно проанализированное сообщение. Metadata хранит source, timestamp, temperature, topic.

## Dedup flow

```
Сообщение X приходит на анализ
        │
        ▼
  encode(X) → embedding
        │
        ▼
  ChromaDB.search(query_embedding, limit=1)
        │
        ├─ similarity > 0.90  → клонируем AI response из найденного сообщения
        │
        └─ similarity < 0.90  → LLM analysis (обычный путь)
```

Фактический кейс: один и тот же релиз новости появляется на 10 каналах. Первый идёт на LLM, остальные 9 — клонируют его результат. Экономия ~90% LLM-токенов.

## Reconnect on restart

ChromaDB может перезапуститься (например, OOM killer). После перезапуска collection пропадает.

```python
def _reconnect_if_collection_lost(self, e: Exception) -> bool:
    if "does not exist" in str(e) or "404" in str(e):
        self._client = None
        self._collection = None
        self._connect(force=True)
        return True
```

`add_message` делает retry один раз после reconnect. Ошибка не фатальна для анализа.

## ChromaDB как не獨立ный сервис

Отдельный контейнер `chromadb` в docker-compose. Не шарятся с другими проектами (polymarket-ai). Причины:
- News Radar владеет своими данными независимо
- Если polymarket-ai перезапускается — News Radar не страдает
- Отдельные бэкапы и volume management

## find_duplicates: Union-Find

```python
# Находит группы near-duplicate сообщений среди N IDs
def find_duplicates(message_ids, threshold=0.92):
    embeddings = collection.get(ids=..., include=["embeddings"])
    # Union-Find grouping
    parent = {mid: mid for mid in message_ids}
    for i in range(len(message_ids)):
        for j in range(i+1, len(message_ids)):
            sim = np.dot(embeddings[i], embeddings[j])  # cosine (нормализованные)
            if sim >= threshold:
                union(i, j)
    return groups_with_2_plus_members
```

O(n²) сравнение, но n ≤ 500 (сообщения за 6 часов), так что это нормально.