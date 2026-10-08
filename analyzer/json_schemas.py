"""Schemas for JSON-only analyzer tasks."""

from llm_core.transport import JsonSchemaTool


def _object(strings: tuple[str, ...], arrays: tuple[str, ...] = ()) -> dict[str, object]:
    properties: dict[str, object] = {name: {"type": "string"} for name in strings}
    properties.update({name: {"type": "array", "items": {"type": "string"}} for name in arrays})
    return {"type": "object", "properties": properties, "required": list(properties)}


KNOWLEDGE_FULL_SCHEMA = JsonSchemaTool(
    "submit_knowledge_full", "Submit the full article retelling",
    _object(("title", "tldr", "context", "how", "results", "limitations", "read_original_if"),
            ("key_points", "takeaways", "tags")),
)
KNOWLEDGE_BRIEF_SCHEMA = JsonSchemaTool(
    "submit_knowledge_brief", "Submit the brief article retelling",
    _object(("title", "idea", "conclusion"), ("tags",)),
)
KNOWLEDGE_CHUNK_SCHEMA = JsonSchemaTool(
    "submit_knowledge_notes", "Submit notes for one article part", _object((), ("notes",)),
)
DIGEST_AI_VALUE_SCHEMA = JsonSchemaTool(
    "submit_ai_digest", "Submit one digest item per article",
    {"type": "object", "properties": {"items": {"type": "array", "items":
        _object(("source_id", "title", "takeaway", "summary"))}}, "required": ["items"]},
)

DIGEST_AI_VALUE_SCHEMA_V2 = JsonSchemaTool(
    "submit_ai_digest_v2", "Submit digest items, the daily lead and highlights",
    {"type": "object", "properties": {
        "items": {"type": "array", "items": _object(("source_id", "title", "takeaway", "summary"))},
        "lead": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
    }, "required": ["items", "lead", "highlights"]},
)
