"""
Digest Renderer — template-based formatting for Telegram.

Each render_* function takes LLM output (str or dict) and returns:
    (text: str, parse_mode: str)

parse_mode is either "Markdown" (v1) or "MarkdownV2".

Adding a new template: add a new elif branch in render_digest().
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── MarkdownV2 escaping ──────────────────────────────────────────────────────
# These characters MUST be escaped in MarkdownV2 outside of special entities.
_MDV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _esc(text: str) -> str:
    """Escape all MarkdownV2 special characters in a plain text string."""
    for ch in _MDV2_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ── Classic template ─────────────────────────────────────────────────────────

def _render_classic(content: str) -> tuple[str, str]:
    """
    Classic template: LLM already returns the full Telegram Markdown text.
    We just clean up **double-asterisks** and fix the header bolding.
    """
    # Fix LLM-hallucinated GitHub bold (**) to Telegram bold (*)
    content = content.replace("**", "*")

    # Force bold on the main header
    lines = content.splitlines()
    if lines and "🔥 Главное за" in lines[0]:
        if not lines[0].startswith("*"):
            lines[0] = f"*{lines[0]}*"
        # Ensure blank line after header
        if len(lines) > 1 and lines[1].strip() != "":
            lines.insert(1, "")
    content = "\n".join(lines)

    return content, "Markdown"


# ── Spoiler template ─────────────────────────────────────────────────────────

def _html_esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_spoiler(data: dict, item_emoji: str = "🔹", show_summary: bool = True) -> tuple[str, str]:
    """
    Spoiler template: LLM returns JSON {items: [{title, summary, source_url}]}.
    Uses Telegram HTML mode with <blockquote expandable> for collapsible blocks.

    Output format per item (show_summary=True):
        {emoji} <b>Title</b>
        <blockquote expandable>Summary text. <a href="url">источник</a></blockquote>

    Output format (show_summary=False):
        {emoji} <b>Title</b>
        <a href="url">источник</a>
    """
    items = data.get("items", [])
    if not items:
        logger.warning("Spoiler renderer: empty items list from LLM")
        return "", ""

    lines = ["🔥 <b>Главное за последнее время:</b>", ""]

    for item in items:
        title      = _html_esc((item.get("title") or "").strip())
        summary    = _html_esc((item.get("summary") or "").strip())
        source_url = (item.get("source_url") or "").strip()

        if not title:
            continue

        lines.append(f"{item_emoji} <b>{title}</b>")

        if show_summary and summary:
            lines.append(f"<blockquote expandable>{summary}</blockquote>")

        if source_url:
            lines.append(f'<a href="{source_url}">источник</a>')

        lines.append("")  # blank line between items

    # Remove trailing blank line
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines), "HTML"


# ── Public API ───────────────────────────────────────────────────────────────

def render_digest(llm_output, template: str, template_cfg: dict | None = None) -> tuple[str, str]:
    """
    Route LLM output through the correct template renderer.

    Args:
        llm_output:   str (classic) or dict (spoiler, already parsed JSON)
        template:     "classic" | "spoiler"
        template_cfg: optional dict with per-template settings (e.g. item_emoji)

    Returns:
        (text_for_telegram, parse_mode)
        Returns ("", "") on failure — caller should handle gracefully.
    """
    cfg = template_cfg or {}
    try:
        if template == "spoiler":
            if not isinstance(llm_output, dict):
                logger.error(f"Spoiler renderer expected dict, got {type(llm_output)}")
                return "", ""
            item_emoji   = cfg.get("item_emoji", "🔹")
            show_summary = cfg.get("show_summary", True)
            return _render_spoiler(llm_output, item_emoji=item_emoji, show_summary=show_summary)
        else:
            # Default: classic
            if not isinstance(llm_output, str):
                llm_output = str(llm_output)
            return _render_classic(llm_output)
    except Exception as e:
        logger.error(f"Renderer error (template={template}): {e}")
        return "", ""
