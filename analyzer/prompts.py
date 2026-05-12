"""
LLM prompts for news analysis.
Kept in a separate file so they can be tuned without touching business logic.

Key insight: prompt quality = analysis quality.
This is where Prompt Engineering skills are built.
"""


# ──────────────────────────────────────────────
# SINGLE MESSAGE ANALYSIS
# ──────────────────────────────────────────────

SINGLE_MESSAGE_PROMPT = """You are a crypto/financial news analyst. Analyze this Telegram channel message.

CHANNEL: {source_name}
MESSAGE:
{text}

Write the summary in the same language as the MESSAGE above.

Return ONLY valid JSON with no extra text:
{{
  "temperature": <number from 1 to 10>,
  "topic": "<one of: bitcoin, ethereum, altcoins, defi, nft, macro, regulation, hack/scam, exchange, general>",
  "summary": "<As detailed as the source allows, up to 10 sentences, in the same language as the message. First cover key facts (numbers, protocols). CRITICAL: If the post contains strong subjective opinions, sarcasm, or philosophical takeaways (features of shitposting/editorial), you MUST capture the author's main point and attitude in your summary. Do not reduce editorial posts to dry facts only.>",
  "keywords": ["<keyword>", ...],
  "sentiment": "<positive | negative | neutral>",
  "is_ad": <true if this post is an advertisement, sponsored content, paid promo, affiliate/referral offer, giveaway, contest, or any promotional material; false otherwise>
}}

Temperature scale:
1-3: routine news, low interest
4-6: interesting, moderate engagement
7-8: hot topic, active discussion
9-10: BREAKING, maximum hype or panic"""


# ──────────────────────────────────────────────
# BATCH DIGEST (multiple messages over a time period)
# ──────────────────────────────────────────────

DIGEST_PROMPT = """You are a crypto news editor. Write a digest in RUSSIAN for a Telegram channel.

Time period: {period}
You have {count} source messages below. Analyze them and write the digest NOW. Do not explain your reasoning.

SOURCE MESSAGES:
{messages}
{ongoing_trends_section}
---
STEP 1 — MERGE (do this silently before writing):
Scan all source messages. Find any groups that cover the SAME event/story from different angles (e.g. two articles about the same hack, or same protocol vulnerability). Merge each such group into ONE slot with the most complete information. You must do this — always prefer 1 merged slot over 2 overlapping slots.

STEP 2 — WRITE UP TO {digest_max} blocks (write fewer if you merged stories):
Start your response exactly with this text (including asterisks!):
*🔥 Главное за {period}:*

CRITICAL FORMATTING:
- Use Telegram Markdown: single *asterisks* for bold. NEVER use double **asterisks**.

Format for each block (copy this exact structure):
*🔹 TopicName (X/10)*
*Заголовок новости одной строкой*
2-3 предложения контекста на русском. [источник](PostURL)

STEP 3 — CLOSING blocks (always add after the news blocks):

*📊 Настроение на рынке:*
Одно предложение: Bullish / Bearish / Neutral и почему.

*⚡ На радаре:*
Одно предложение: один токен или тренд для наблюдения.

STEP 4 — If ONGOING TRENDS section is present above, insert BETWEEN news blocks and closing:
*🔄 Продолжение: [topic name]*
2-3 предложения: что изменилось vs прошлый дайджест, ключевые цифры.
(CRITICAL: DO NOT write a Продолжение block for an event if you already covered it in the main blocks above!)

CRITICAL: Output ONLY the digest text. No meta-commentary. No "Wait," or "Let me". Start directly with *🔥 Главное за {period}:* (with the asterisks)."""


# ──────────────────────────────────────────────
# DIGEST SPOILER TEMPLATE (JSON structured output)
# Used when digest_template = "spoiler"
# Python renderer turns this JSON into Telegram MarkdownV2 with spoilers
# ──────────────────────────────────────────────

DIGEST_PROMPT_SPOILER = """You are a crypto news editor. Analyze the messages below and return a JSON digest in RUSSIAN.

Time period: {period}
You have {count} source messages below. Do NOT explain your reasoning.

SOURCE MESSAGES:
{messages}

---
{merge_step}
Return a JSON object with exactly {digest_max} items (or fewer only if you merged duplicates):

{{
  "items": [
    {{
      "title": "Броский заголовок новости на русском — не более {title_max_words} слов",
      "summary": "Краткое саммари — не более {summary_max_sentences} предложений. Ключевые факты: цифры, протоколы, участники.",
      "source_id": 1
    }},
    ...
  ]
}}

RULES:
- Write ONLY valid JSON. No markdown, no prose, no extra text outside the JSON.
- Title: punchy, specific, in Russian. Max {title_max_words} words.
- Summary: factual, in Russian. Max {summary_max_sentences} sentences. Include key numbers/names.
- source_id: the integer ID (e.g., 1, 2) corresponding to the [ID] of the primary source message you used."""

# Merge step inserted into the prompt when llm_merge=true
DIGEST_SPOILER_MERGE_ON = """STEP 1 — MERGE (silently):
Find groups covering the SAME event from different angles. Merge each group into ONE item using the most complete info. Write fewer items if you merged.

STEP 2 — """

# No merge: trust that deduplication already handled it
DIGEST_SPOILER_MERGE_OFF = """STEP 1 — Each source message is already deduplicated. Write one item per message. Do NOT merge. Write exactly {digest_max} items.

STEP 2 — """


# ──────────────────────────────────────────────
# ALERT ENRICHMENT (instant alerts — Russian headline + translation)
# These prompts are used by _enrich_alert_for_telegram() to:
#   ALERT_ENRICH_PROMPT   → generate a punchy Russian headline for breaking_alert
#   HOT_TREND_ENRICH_PROMPT → translate English topic/summary to Russian for hot_trend
# ──────────────────────────────────────────────

ALERT_ENRICH_PROMPT = """Тема категории: {topic}
Саммари новости (может быть на любом языке): {summary}

Задача:
1. Если саммари не на русском — переведи его на русский, сохрани все ключевые факты и цифры.
2. Придумай один броский заголовок (5-9 слов) на русском языке, точно отражающий суть.

Верни ТОЛЬКО валидный JSON без лишнего текста:
{{
  "headline": "Броский заголовок здесь",
  "summary_ru": "Саммари на русском языке, не более 10 предложений. Все ключевые факты, цифры и участники сохранены."
}}"""


HOT_TREND_ENRICH_PROMPT = """Тема тренда (на английском): {topic}
Саммари тренда (на английском): {summary}

Переведи и адаптируй для русскоязычной аудитории.
Верни ТОЛЬКО валидный JSON без лишнего текста:
{{
  "headline": "Броский заголовок тренда на русском (4-8 слов)",
  "summary_ru": "Полное саммари тренда на русском языке. Передай все факты из оригинала."
}}"""


# ──────────────────────────────────────────────
# TOPIC CLUSTERING
# ──────────────────────────────────────────────

CLUSTER_PROMPT = """Group these news items by topic.

NEWS:
{messages}

Return ONLY valid JSON:
{{
  "clusters": [
    {{
      "topic": "<topic name>",
      "message_ids": [<id>, ...],
      "temperature": <average temperature>,
      "summary": "<one sentence about this topic>"
    }}
  ]
}}

Maximum 5 clusters. Merge similar topics."""


# ──────────────────────────────────────────────
# SYSTEM PROMPT (used for all requests)
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert cryptocurrency market analyst and content editor.
Your job: analyze context accurately, capturing both the objective facts and the unique subjective opinions/attitude of the authors.
Always respond strictly in the requested format.
Do not add anything outside the format.
Mark is_ad=true for: paid advertisements, sponsored posts, partner promotions, affiliate/referral offers, giveaways, contests, airdrop promotions, and any post whose primary purpose is commercial promotion rather than news."""
