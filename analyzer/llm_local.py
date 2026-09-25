"""Local llama.cpp/Qwen thinking payload; lock state stays in llm_client for compatibility."""


def thinking_payload(enabled: bool, disable_thinking: bool) -> dict[str, object]:
    """Send the local template option only when the active endpoint supports it."""
    if enabled and disable_thinking:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}
