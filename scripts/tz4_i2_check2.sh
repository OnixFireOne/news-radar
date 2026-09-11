#!/usr/bin/env bash
# ТЗ #4 И2 — добор фактов после первого прогона. Лог: data/tz4_i2_check2.log
set -u
cd "$(dirname "$0")/.."
exec > >(tee data/tz4_i2_check2.log) 2>&1
FEEDS="docker compose --profile feeds"
step() { echo; echo "================ $* ================"; }

step "1. тесты коллекторов в образе collector-feeds (там есть feedparser/trafilatura)"
$FEEDS run --rm --no-deps collector-feeds python -m pytest -q tests/collector 2>&1 | tail -25; echo ">>> exit=${PIPESTATUS[0]}"

step "2. остальной набор в analyzer без tests/collector"
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector 2>&1 | tail -15; echo ">>> exit=${PIPESTATUS[0]}"

step "3. почему упали все 20 догрузок полного текста (из полных логов контейнера)"
$FEEDS logs --no-color collector-feeds 2>&1 | grep 'Full-text fetch failed' | sed -E 's/.*fetch failed for //' | cut -c1-200 | sort | uniq -c | head -30

step "4. Hacker News: сколько историй проходит min_points=30"
$FEEDS run --rm --no-deps collector-feeds python - <<'PY'
import httpx
B = "https://hn.algolia.com/api/v1/search_by_date"
for q in ["llm", "ai agents"]:
    d = httpx.get(B, params={"query": q, "tags": "story"}, timeout=15).json()
    pts = sorted([(h.get("points") or 0) for h in d["hits"]], reverse=True)
    print(f"{q!r}: как сейчас — hits={len(d['hits'])}, из них points>=30: {sum(p>=30 for p in pts)}; топ points {pts[:8]}")
    d2 = httpx.get(B, params={"query": q, "tags": "story", "numericFilters": "points>=30"}, timeout=15).json()
    print(f"{q!r}: с numericFilters=points>=30 — hits={len(d2['hits'])}, nbHits={d2.get('nbHits')}")
PY

step "5. openai.com / huggingface.co отвечают нашему User-Agent?"
$FEEDS run --rm --no-deps collector-feeds python - <<'PY'
import httpx
for u in ["https://openai.com/index/harness-engineering", "https://huggingface.co/blog/gradio"]:
    for ua in ["news-radar/1.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/128 Safari/537.36"]:
        try:
            r = httpx.get(u, headers={"User-Agent": ua}, timeout=15, follow_redirects=True)
            print(r.status_code, len(r.text), ua[:14], u)
        except Exception as e:
            print("ERR", type(e).__name__, ua[:14], u)
PY
echo; echo "ГОТОВО"
