"""
Regression lock for analyzer/renderer.py — classic and spoiler templates.

Expected outputs below were captured verbatim from renderer.py as it stood
BEFORE ТЗ #4 / iteration И1 (see specs/tz4-ai-value.md). CLAUDE.md forbids
changing the classic/spoiler branches by even one line; this test proves
byte-for-byte equality so any future change (accidental or in a later
iteration) is caught immediately.
"""

from analyzer.renderer import render_digest

_CLASSIC_INPUT = (
    "🔥 Главное за последнее время:\n\n"
    "🔹 Биткоин пробил $105K на фоне ETF-рекорда\n"
    "    Спотовые BTC ETF зафиксировали приток $1.2B за день. "
    "[источник](https://example.com/1)\n\n"
    "🔹 SEC одобрила листинг Solana ETF\n"
    "    Комиссия дала зелёный свет трём провайдерам. "
    "[источник](https://example.com/2)\n\n"
    "📊 Настроение на рынке:\n"
    "Bullish на фоне рекордных притоков.\n\n"
    "⚡ На радаре:\n"
    "Solana как токен для наблюдения."
)

_CLASSIC_EXPECTED_TEXT = (
    "*🔥 Главное за последнее время:*\n\n"
    "🔹 Биткоин пробил $105K на фоне ETF-рекорда\n"
    "    Спотовые BTC ETF зафиксировали приток $1.2B за день. "
    "[источник](https://example.com/1)\n\n"
    "🔹 SEC одобрила листинг Solana ETF\n"
    "    Комиссия дала зелёный свет трём провайдерам. "
    "[источник](https://example.com/2)\n\n"
    "📊 Настроение на рынке:\n"
    "Bullish на фоне рекордных притоков.\n\n"
    "⚡ На радаре:\n"
    "Solana как токен для наблюдения."
)

_SPOILER_INPUT = {
    "items": [
        {
            "title": "Биткоин пробил $105K",
            "summary": "Спотовые BTC ETF зафиксировали приток $1.2B за день. Рынок реагирует ростом.",
            "source_id": 1,
        },
        {
            "title": "SEC одобрила Solana ETF",
            "summary": "Комиссия дала зелёный свет трём провайдерам.",
            "source_url": "https://example.com/raw",
        },
    ]
}
_SPOILER_SOURCE_MAP = {"1": "https://example.com/btc"}

_SPOILER_WITH_SUMMARY_EXPECTED = (
    "🔥 <b>Главное за последнее время:</b>\n\n"
    "🔹 <b>Биткоин пробил $105K</b>\n"
    "<blockquote expandable>Спотовые BTC ETF зафиксировали приток $1.2B за день. "
    "Рынок реагирует ростом.</blockquote>\n"
    '<a href="https://example.com/btc">источник</a>\n\n'
    "🔹 <b>SEC одобрила Solana ETF</b>\n"
    "<blockquote expandable>Комиссия дала зелёный свет трём провайдерам.</blockquote>\n"
    '<a href="https://example.com/raw">источник</a>'
)

_SPOILER_NO_SUMMARY_EXPECTED = (
    "🔥 <b>Главное за последнее время:</b>\n\n"
    "🛠 <b>Биткоин пробил $105K</b>\n"
    '<a href="https://example.com/btc">источник</a>\n\n'
    "🛠 <b>SEC одобрила Solana ETF</b>\n"
    '<a href="https://example.com/raw">источник</a>'
)


def test_classic_template_byte_for_byte() -> None:
    text, parse_mode = render_digest(_CLASSIC_INPUT, "classic")
    assert text == _CLASSIC_EXPECTED_TEXT
    assert parse_mode == "Markdown"


def test_spoiler_template_with_summary_byte_for_byte() -> None:
    text, parse_mode = render_digest(
        _SPOILER_INPUT,
        "spoiler",
        template_cfg={"item_emoji": "🔹", "show_summary": True},
        source_map=_SPOILER_SOURCE_MAP,
    )
    assert text == _SPOILER_WITH_SUMMARY_EXPECTED
    assert parse_mode == "HTML"


def test_spoiler_template_without_summary_byte_for_byte() -> None:
    text, parse_mode = render_digest(
        _SPOILER_INPUT,
        "spoiler",
        template_cfg={"item_emoji": "🛠", "show_summary": False},
        source_map=_SPOILER_SOURCE_MAP,
    )
    assert text == _SPOILER_NO_SUMMARY_EXPECTED
    assert parse_mode == "HTML"


def test_spoiler_template_empty_items_returns_empty() -> None:
    assert render_digest({"items": []}, "spoiler") == ("", "")


def test_spoiler_template_wrong_type_returns_empty() -> None:
    assert render_digest("not a dict", "spoiler") == ("", "")
