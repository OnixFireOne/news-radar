"""Registration and legacy routing for the pipeline bricks."""

from __future__ import annotations

from typing import Any

import pytest

import analyzer.pipeline.analyzers  # Register analysis bricks.
import analyzer.pipeline.hooks  # Register hooks.
import analyzer.pipeline.selectors  # Register selectors.
import analyzer.pipeline.extras  # Register extras.
import analyzer.pipeline.writers  # Register writers.
from analyzer.pipeline.legacy import resolve_legacy
from analyzer.pipeline.registry import ANALYZERS, HOOKS, SELECTORS, EXTRAS, WRITERS, Registry


def test_unknown_name_lists_registered_names() -> None:
    registry: Registry[int] = Registry()
    registry.register("first")(1)
    registry.register("second")(2)
    with pytest.raises(KeyError, match="first, second"):
        registry.get("missing")


@pytest.mark.parametrize("profile,template,router,expected_analyzer,expected_selector,expected_extras", [
    ("crypto", "classic", False, "crypto", "tiers", ()),
    ("crypto", "spoiler", True, "crypto", "tiers", ()),
    ("crypto", "ai_value", False, "crypto", "quotas", ("knowledge",)),
    ("ai_value", "classic", False, "crypto", "tiers", ()),
    ("ai_value", "spoiler", True, "ai_value", "tiers", ()),
    ("ai_value", "ai_value", True, "ai_value", "quotas", ("knowledge",)),
])
def test_legacy_resolution(
    profile: str, template: str, router: bool, expected_analyzer: str,
    expected_selector: str, expected_extras: tuple[str, ...],
) -> None:
    cfg: dict[str, Any] = {"analysis_profile": profile, "digest_template": template}
    spec = resolve_legacy(cfg, router)
    assert spec.name == profile
    assert spec.analyzer == expected_analyzer
    assert spec.hooks == ("embeddings", "alerts", "subscriptions")
    assert spec.select == expected_selector
    assert spec.template == template
    assert spec.extras == expected_extras
    ANALYZERS.get(spec.analyzer)
    SELECTORS.get(spec.select)
    WRITERS.get(spec.template)
    for name in spec.hooks:
        HOOKS.get(name)
    for name in spec.extras:
        EXTRAS.get(name)
