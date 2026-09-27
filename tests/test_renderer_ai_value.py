"""Public HTML contract for the AI digest template."""
from typing import Any

import pytest

from analyzer.renderer import render_digest
from config.config_watcher import DEFAULT_CONFIG


def render(items: list[dict[str, Any]], **kwargs: Any) -> tuple[str, str]:
    return render_digest({"date_label": "27 сентября", "items": items}, "ai_value", **kwargs)


@pytest.mark.parametrize("kind,emoji,label", [
    ("practical_case", "💡", "Кейс: "), ("tutorial", "📚", "Туториал: "),
    ("tool_release", "🛠", "Инструмент: "), ("research", "🔬", "Research: "),
    ("opinion", "💬", "Мнение: "), ("hype_news", "📰", "Новость дня: "),
    ("crypto", "₿", "Крипто-тренд: "), ("unknown", "🔹", ""),
])
def test_header_and_types(kind: str, emoji: str, label: str) -> None:
    defaults: dict[str, Any] = DEFAULT_CONFIG
    text, mode = render([{"title": "Заголовок", "content_type": kind}],
                        template_cfg=defaults["digest_templates"]["ai_value"])
    assert mode == "HTML"
    assert "🤖 <b>AI-радар — 27 сентября</b>" in text
    assert f"{emoji} <b>{label}Заголовок</b>" in text


def test_escaping_and_links() -> None:
    text, _ = render([{"title": "<Title>", "takeaway": "A & B", "summary": "<Summary>", "source_id": 1}],
                     source_map={"1": 'https://example.org/?a=1&b="2"'}, md_map={"1": "https://github.com/doc"})
    assert "&lt;Title&gt;" in text and "A &amp; B" in text
    assert '<blockquote expandable>&lt;Summary&gt;\n<a href=' in text
    assert '&amp;b=&quot;2&quot;' in text
    assert 'источник</a> · <a href="https://github.com/doc">разбор (md)</a></blockquote>' in text


@pytest.mark.parametrize("show_md,source,md,expected", [
    (True, True, False, "источник"), (False, True, True, "источник"),
    (True, False, True, "разбор (md)"), (True, False, False, ""),
])
def test_optional_links(show_md: bool, source: bool, md: bool, expected: str) -> None:
    text, _ = render([{"title": "Title", "source_id": "1"}], template_cfg={"show_md_link": show_md},
                     source_map={"1": "https://source"} if source else {},
                     md_map={"1": "https://md"} if md else {})
    assert ("источник</a>" in text) == (expected == "источник")
    assert ("разбор (md)</a>" in text) == (expected == "разбор (md)")
    assert " · " not in text


@pytest.mark.parametrize("items", [[], [{"title": " "}], [{"summary": "No title"}]])
def test_empty(items: list[dict[str, Any]]) -> None:
    assert render(items) == ("", "")


def test_without_date() -> None:
    text, _ = render_digest({"items": [{"title": "Title"}]}, "ai_value")
    assert text.startswith("🤖 <b>AI-радар</b>")
