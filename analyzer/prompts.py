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

Return ONLY valid JSON with no extra text:
{{
  "temperature": <number from 1 to 10>,
  "topic": "<one of: bitcoin, ethereum, altcoins, defi, nft, macro, regulation, hack/scam, exchange, general>",
  "summary": "<2-3 sentences in RUSSIAN: what this news is about>",
  "keywords": ["<keyword>", ...],
  "sentiment": "<positive | negative | neutral>"
}}

Temperature scale:
1-3: routine news, low interest
4-6: interesting, moderate engagement
7-8: hot topic, active discussion
9-10: BREAKING, maximum hype or panic"""


# ──────────────────────────────────────────────
# BATCH DIGEST (multiple messages over a time period)
# ──────────────────────────────────────────────

DIGEST_PROMPT = """You are creating a short, engaging crypto news digest for the period: {period}.

Here are {count} messages from different Telegram channels:

{messages}

Write a punchy and easy-to-read digest in RUSSIAN using Markdown format. Limit text to the essentials.
IMPORTANT: Every story MUST include a source reference at the end like: (источник: @channel)

Format exactly like this example — do NOT add extra headers or titles before the list:

*🔥 Главное за {period}:*

🔹 *Bitcoin / Температура: 9/10*
BTC пробил $70k на фоне рекордного притока ETF. Аналитики ждут продолжения роста. (источник: @some_channel)

🔹 *Regulation / Температура: 8/10*
SEC подала новый иск против биржи. Рынок отреагировал снижением на 3%. (источник: @another_channel)

[... include up to 5 top stories maximum, one per 🔹 bullet]

*📊 Настроения на рынке:*
[One short sentence in Russian: Bullish / Bearish / Neutral and main reason]

*⚡ На радаре:*
[One specific trend, token, or narrative to watch, 1 short sentence in Russian]"""


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

SYSTEM_PROMPT = """You are a cryptocurrency market analyst.
Your job: analyze news briefly, accurately, and objectively.
Always respond strictly in the requested format.
Do not add anything outside the format."""
