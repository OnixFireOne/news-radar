"""Schemas for JSON-only analyzer tasks."""

from llm_core.transport import JsonSchemaTool

EDITOR_REVIEW_SCHEMA = JsonSchemaTool(
    "submit_editor_review", "Review selected digest articles against candidates",
    {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["ok", "improve"]},
        "summary": {"type": "string"},
        "swaps": {"type": "array", "items": {"type": "object", "properties": {
            "out_id": {"type": "string"}, "in_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["out_id", "in_id", "reason"]}},
        "duplicates": {"type": "array", "items": {"type": "object", "properties": {
            "id_a": {"type": "string"}, "id_b": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["id_a", "id_b", "reason"]}},
        "additions": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["id", "reason"]}},
    }, "required": ["verdict", "summary", "swaps", "duplicates", "additions"]},
)


def _object(strings: tuple[str, ...], arrays: tuple[str, ...] = ()) -> dict[str, object]:
    properties: dict[str, object] = {name: {"type": "string"} for name in strings}
    properties.update({name: {"type": "array", "items": {"type": "string"}} for name in arrays})
    return {"type": "object", "properties": properties, "required": list(properties)}


KNOWLEDGE_FULL_SCHEMA = JsonSchemaTool(
    "submit_knowledge_full", "Submit the full article retelling",
    _object(("title", "tldr", "context", "how", "results", "limitations", "read_original_if"),
            ("key_points", "takeaways", "tags")),
)
KNOWLEDGE_FULL_SCHEMA_V3 = JsonSchemaTool(
    "submit_knowledge_full_v3", "Submit the accessible full article retelling",
    _object(("title", "tldr", "context", "how", "results", "limitations", "read_original_if"),
            ("key_points", "takeaways", "tags", "table")),
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

DIGEST_AI_VALUE_SCHEMA_V3 = JsonSchemaTool(
    "submit_ai_digest_v3", "Submit digest items, the daily lead, highlights and announcement picks",
    {"type": "object", "properties": {
        "items": {"type": "array", "items": _object(("source_id", "title", "takeaway", "summary"))},
        "lead": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "picks": {"type": "array", "items": _object(("source_id", "hook"))},
    }, "required": ["items", "lead", "highlights", "picks"]},
)
