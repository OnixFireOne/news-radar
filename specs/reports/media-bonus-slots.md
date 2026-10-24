# Бонусные медиа-слоты (24.10.2026)

- **Что сделано:** `analyzer/value_funnel.py` — до трёх дополнительных медиа после неизменённого основного отбора, общий порядок ранжирования; невалидные patterns отключают бонус. Матчинг: text[:300], takeaway, summary. `config/config_watcher.py` — выключенный дефолт; `config/settings.json` — включено, стартовые регулярки. `analyzer/analyzer.py` — лог количества бонусов и сохранение результата quotas в return_raw. `tests/test_media_bonus.py`, `mypy.ini` — новые проверки и strict.
- **Коммиты:** см. `git log` — `feat(digest): media bonus slots on top of quota selection`.
- **Новые зависимости:** нет.
- **Расхождения со спекой:** нет.
- **Приёмка (Claude Code, 24.10):** `pytest -q --ignore=tests/collector` — 428 passed, 20 skipped. `mypy` — 2 ошибки
  `unused-ignore` в `tests/test_strict_json.py:52,71`; файл не менялся, к правке не относятся (долг вне задачи).
- **Проверка на реальном пуле** (локальная копия `data/news-server.db`, 387 статей за 7 дней): основной отбор — те же
  7 статей (в нём уже есть одна медиа — пиксель-арт, 8), бонус добавил 3: генерация видео через Veo 3.1 (8),
  Stable Diffusion на ESP32 (7), Nano Banana 2.1 (7). Ещё медиа-семёрки (ComfyUI-сэмплеры, TorchTPU) остаются в пуле.
  Первый прогон поймал ложное срабатывание: `Flux` → Kubernetes-клиент (Flux CD); регулярка сужена (грабля 53).
- **Что НЕ сделано:** боевой выпуск с бонусом не проверен — смотреть первый выпуск после деплоя (10 статей,
  список «Ещё» в анонсе `cards` не обрезан). TorchTPU совпал по `summary` («генерации изображений» как пример) —
  пограничный случай, оставлен.
- **Побочные находки / лимиты:** генератор ai_value передаёт все выбранные статьи; сайт и candidates_list не имеют лимита 7. Cards ограничивает picks тремя, остальное — бюджетом 3500 символов сырого HTML: длинные URL/заголовки могут обрезать список «Ещё»; полный выпуск доступен по ссылке. Шаблоны classic/spoiler и промпты не менялись.
- **Команды приёмки:**

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q tests/test_media_bonus.py tests/test_digest_ai_value.py tests/test_digest_candidates.py tests/test_announce_cards.py tests/test_site_digest.py tests/test_pipeline_characterization.py
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict analyzer/value_funnel.py analyzer/analyzer.py tests/test_media_bonus.py
```
