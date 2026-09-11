#!/usr/bin/env bash
# ТЗ #4 И2 — прогон приёмки (specs/reports/tz4-i2-acceptance.md), всё в докере.
# Запуск из корня репо:  bash scripts/tz4_i2_check.sh
# Полный лог: data/tz4_i2_check.log (data/ в .gitignore)
set -u
cd "$(dirname "$0")/.."
mkdir -p data
LOG=data/tz4_i2_check.log
exec > >(tee "$LOG") 2>&1

DC="docker compose"
FEEDS="docker compose --profile feeds"
step() { echo; echo "================ $* ================"; date '+%F %T'; }
rc()   { echo ">>> exit=$1"; }

sql_counts() {
  $FEEDS exec -T collector-feeds python - <<'PY'
import sqlite3
c = sqlite3.connect('/app/data/news.db'); c.row_factory = sqlite3.Row
q = lambda s: c.execute(s).fetchall()
print('total messages:', q("SELECT COUNT(*) n FROM messages")[0]['n'])
print('по типам источников:')
for r in q("SELECT s.type t, COUNT(*) n FROM messages m JOIN sources s ON s.id=m.source_id GROUP BY s.type"):
    print('  ', r['t'], r['n'])
print('по источникам (n / min / avg / max длина текста):')
for r in q("""SELECT s.type t, s.name nm, COUNT(*) n, MIN(LENGTH(m.text)) mn,
                     CAST(AVG(LENGTH(m.text)) AS INT) av, MAX(LENGTH(m.text)) mx
              FROM messages m JOIN sources s ON s.id=m.source_id GROUP BY s.id ORDER BY s.type, n DESC"""):
    print(f"   {r['t']:<10} {r['n']:>4}  {r['mn']:>6} {r['av']:>6} {r['mx']:>7}  {r['nm']}")
print('с непустым url:', q("SELECT COUNT(*) n FROM messages WHERE url IS NOT NULL AND url != ''")[0]['n'])
print('url IS NULL / пустой:', q("SELECT COUNT(*) n FROM messages WHERE url IS NULL OR url = ''")[0]['n'])
print('ДУБЛИ ПО URL:', q("SELECT COUNT(*) n FROM (SELECT url FROM messages WHERE url IS NOT NULL AND url != '' GROUP BY url HAVING COUNT(*)>1)")[0]['n'])
print('индексы messages:', [r[1] for r in q("PRAGMA index_list(messages)")])
PY
}

total() {
  $FEEDS exec -T collector-feeds python -c "import sqlite3;print(sqlite3.connect('/app/data/news.db').execute('SELECT COUNT(*) FROM messages').fetchone()[0])" 2>/dev/null | tr -d '\r' || echo 0
}

wait_stable() {  # ждём, пока число сообщений не перестанет расти 60с подряд (макс ~12 мин)
  local prev=-1 same=0 n i
  for i in $(seq 1 72); do
    sleep 10; n=$(total); echo "  [$(date +%T)] messages=$n"
    if [ "$n" = "$prev" ] && [ "$n" != "0" ]; then same=$((same+1)); else same=0; fi
    [ $same -ge 6 ] && return 0
    prev=$n
  done
  echo "  (не стабилизировалось за 12 мин — идём дальше)"
}

step "0. окружение"
docker version --format 'docker {{.Server.Version}}' ; docker compose version
git log --oneline -1; git status --short
echo "committed defaults (должно быть false/false):"
git show HEAD:config/settings.json | python3 -c "import json,sys;s=json.load(sys.stdin)['sources'];print(' rss',s['rss']['enabled'],' hn',s['hackernews']['enabled'])"
docker network inspect ai-network >/dev/null 2>&1 || { echo "создаю внешнюю сеть ai-network (в проде её даёт llm-stack)"; docker network create ai-network; }

step "A1. build analyzer + collector-feeds"
$FEEDS build analyzer collector-feeds; rc $?

step "A2. pytest (analyzer image — no feedparser/trafilatura there, tests/collector excluded)"
$DC run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector 2>&1 | tail -40; rc ${PIPESTATUS[0]}

step "A2b. pytest (collector-feeds image — tests/collector only)"
$FEEDS run --rm --no-deps collector-feeds python -m pytest -q tests/collector 2>&1 | tail -40; rc ${PIPESTATUS[0]}

step "A3. mypy"
$DC run --rm --no-deps analyzer python -m mypy 2>&1 | tail -20; rc ${PIPESTATUS[0]}

step "A4. mypy --strict llm_core"
$DC run --rm --no-deps analyzer python -m mypy --strict llm_core 2>&1 | tail -20; rc ${PIPESTATUS[0]}

step "B2. сервисы по умолчанию (без профиля collector-feeds быть не должно)"
$DC config --services
echo "--- с --profile feeds:"; $FEEDS config --services

step "C. живой прогон: up collector-feeds, первый цикл"
$FEEDS up -d collector-feeds; rc $?
wait_stable
echo "--- логи первого цикла:"
$FEEDS logs --no-color collector-feeds 2>&1 | tail -150
echo "--- сводка по логам:"
L=$($FEEDS logs --no-color collector-feeds 2>&1)
echo "  WARNING:          $(grep -c 'WARNING' <<<"$L")"
echo "  ERROR/Traceback:  $(grep -c -E 'ERROR|Traceback' <<<"$L")"
echo "  fetch cap reached: $(grep -c 'cap reached' <<<"$L")"
echo "  fetch failed:      $(grep -c 'Full-text fetch failed' <<<"$L")"
echo "  too short/empty:   $(grep -c 'too short/empty' <<<"$L")"
echo "  429:               $(grep -c '429' <<<"$L")"
echo "  habr в fulltext-логах: $(grep -i 'habr' <<<"$L" | grep -c -i 'full-text')"

step "D. база после первого цикла"
sql_counts
N1=$(total)

step "E. второй цикл (рестарт → те же URL прилетят заново, вставиться не должны)"
$FEEDS restart collector-feeds
wait_stable
N2=$(total)
echo "до рестарта: $N1   после: $N2   прирост: $((N2-N1))"
sql_counts
echo "--- логи после рестарта (хвост):"
$FEEDS logs --no-color --since 15m collector-feeds 2>&1 | tail -40

step "стоп collector-feeds"
$FEEDS stop collector-feeds
echo; echo "ГОТОВО. Лог: $LOG"
