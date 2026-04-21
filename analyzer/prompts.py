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

Here are {count} fresh messages from different Telegram channels:

{messages}
{ongoing_trends_section}
Write a punchy, easy-to-read digest in RUSSIAN using Markdown. Limit text to the essentials.
IMPORTANT: Fresh stories MUST end with a Markdown link: [источник](PostURL)

Format:

*🔥 Главное за {period}:*

🔹 *[TOPIC] / Температура: [TEMP]/10*
*Суть новости*
Описание 2-3 предложения. [источник](PostURL)

[up to 5 fresh stories]

If ONGOING TRENDS section is present above:
Add this block BEFORE the market sentiment:

🔄 *Продолжение: [main ongoing topic]*
[Write 2-3 sentences synthesizing all the ongoing updates — what’s new compared to last digest, how the story is developing, any numbers or outcomes]

*📊 Настроения на рынке:*
[Bullish / Bearish / Neutral — one sentence in Russian]

*⚡ На радаре:*
[One trend or token to watch, one sentence in Russian]"""


# ──────────────────────────────────────────────
# BREAKING ALERT (legacy mode — direct LLM → Telegram)
# ──────────────────────────────────────────────

ALERT_PROMPT = """Write a short breaking news alert in Russian for a Telegram crypto channel.

Source: @{source}
Topic: {topic}
Temperature: {temperature}/10
Summary: {summary}
Link: {source_url}

Rules:
- Maximum 4 sentences
- Start with a fitting emoji (🚨, ⚡, 🔥, etc.)
- Bold the topic using *asterisks*
- End with [источник]({source_url}) as a clickable link
- Write ONLY the post text, nothing else"""


# ──────────────────────────────────────────────
# HOT TREND ALERT (legacy mode — direct LLM → Telegram)
# ──────────────────────────────────────────────

HOT_TREND_PROMPT = """Write a short hot trend report in Russian for a Telegram crypto channel.

Topic: {topic}
Trend Score: {score:.1f}
Unique Sources: {sources}
Summary: {summary}
Channels: {channels}

Rules:
- Maximum 4 sentences
- Start with 🔥
- Bold the topic using *asterisks*
- Mention how many channels are covering this
- Write ONLY the post text, nothing else"""


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
