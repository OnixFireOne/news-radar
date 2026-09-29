"""knowledge.format "full": structured retelling instead of Идея/Вывод."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from analyzer.knowledge_publisher import build_full_body, build_markdown, generate_doc

ROW = {"id": 7, "text": "Article", "url": "https://example.org/a", "collected_at": "2026-09-28 10:00:00",
       "source_type": "rss", "content_type": "tutorial", "value_score": 8, "topic": "agents"}


def answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Статья", "tldr": "Суть статьи.", "context": "", "key_points": ["Один", "Два", "Три"],
        "how": "Шаги.", "results": "", "limitations": "Не проверено.", "takeaways": ["Урок"],
        "read_original_if": "Нужен код.", "tags": ["ai", "agents"],
    }
    return {**base, **overrides}


def test_body_skips_empty_sections_and_keeps_order() -> None:
    body = build_full_body(answer())
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == ["## Коротко", "## Главное", "## Как сделано", "## Ограничения",
                        "## Что взять себе", "## Читать оригинал, если…"]
    assert "## Главное\n- Один\n- Два\n- Три" in body


@pytest.mark.parametrize("bad", [{"tldr": ""}, {"key_points": ["Один", "Два"]}, {"key_points": "текст"}])
def test_body_requires_tldr_and_key_points(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        build_full_body(answer(**bad))


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["full", "brief"])
async def test_generate_doc_uses_format(fmt: str) -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = (answer() if fmt == "full"
                                      else {"title": "Статья", "idea": "Идея", "conclusion": "Вывод", "tags": ["ai", "agents"]})
    doc = await generate_doc(llm, ROW, {"format": fmt, "max_input_chars": 24000})
    assert doc is not None
    prompt = llm.complete_json.call_args.kwargs["user_prompt"]
    md = build_markdown(doc)
    assert md.startswith('---\ntitle: "Статья"\nsource_url: "https://example.org/a"')
    if fmt == "full":
        assert "read_original_if" in prompt and "## Коротко\nСуть статьи." in md and "## Идея" not in md
        assert (doc.idea, doc.conclusion) == ("Суть статьи.", "Урок")
    else:
        assert "read_original_if" not in prompt and "## Идея\nИдея\n\n## Вывод\nВывод" in md


@pytest.mark.asyncio
async def test_full_format_rejects_answer_without_key_points() -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = answer(key_points=[])
    assert await generate_doc(llm, ROW, {"format": "full"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tags,expected", [
    (["api", "токены", "кэш"], ["api", "agents"]),
    (["токены"], ["agents", "ai"]),
    ("не список", ["agents", "ai"]),
    (["ai", "agents", "llm"], ["ai", "agents", "llm"]),
])
async def test_full_format_repairs_non_latin_tags(tags: Any, expected: list[str]) -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = answer(tags=tags)
    doc = await generate_doc(llm, ROW, {"format": "full"})
    assert doc is not None and doc.tags == expected


@pytest.mark.asyncio
async def test_whole_article_is_sent_without_cut() -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = answer()
    text = "Начало. " + "x" * 60000 + " КОНЕЦСТАТЬИ"
    doc = await generate_doc(llm, {**ROW, "text": text},
                             {"format": "full", "max_input_chars": 40000, "split_over_chars": 150000})
    assert doc is not None
    assert llm.complete_json.await_count == 1
    assert "КОНЕЦСТАТЬИ" in llm.complete_json.call_args.kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_huge_article_goes_through_part_notes() -> None:
    llm = AsyncMock()
    prompts: list[str] = []

    async def complete_json(**kwargs: Any) -> dict[str, Any]:
        prompts.append(kwargs["user_prompt"])
        if "condensed notes" in kwargs["user_prompt"]:
            return {"notes": [f"заметка {len(prompts)}"]}
        return answer()

    llm.complete_json.side_effect = complete_json
    paragraph = "Абзац текста статьи.\n" * 500  # ~10.5k chars
    text = paragraph * 3 + "ФИНАЛЬНЫЙВЫВОД\n"
    doc = await generate_doc(llm, {**ROW, "text": text},
                             {"format": "full", "max_input_chars": 1000, "split_over_chars": 20000})
    assert doc is not None
    notes_calls = [p for p in prompts if "condensed notes" in p]
    assert len(notes_calls) == 4  # 31.5k chars in parts of 10k, nothing dropped
    assert "ФИНАЛЬНЫЙВЫВОД" in notes_calls[-1]
    final = prompts[-1]
    assert "[Часть 1 из 4]" in final and "[Часть 4 из 4]" in final and "заметка 4" in final


def test_split_parts_is_lossless() -> None:
    from analyzer.knowledge_publisher import split_parts
    text = "a\n" * 30 + "b" * 25 + "\nc\n"
    parts = split_parts(text, 10)
    assert "".join(parts) == text and all(len(part) <= 10 for part in parts)
