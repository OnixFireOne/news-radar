"""Map the existing settings onto named pipeline bricks."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from analyzer.pipeline.context import CategorySpec

logger = logging.getLogger("analyzer.analyzer")


def resolve_legacy(
    cfg: Mapping[str, Any], has_router: bool, *, warn_on_fallback: bool = True,
) -> CategorySpec:
    profile = cfg.get("analysis_profile", "crypto")
    template = cfg.get("digest_template", "classic")
    analyzer = "ai_value" if profile == "ai_value" and has_router else "crypto"
    if profile == "ai_value" and not has_router and warn_on_fallback:
        logger.warning("ai_value requires a catalog router; using crypto for this cycle")
    return CategorySpec(
        name=str(profile), analyzer=analyzer, hooks=("alerts", "subscriptions"),
        select="quotas" if template == "ai_value" else "tiers",
        template=str(template), extras=("knowledge",) if template == "ai_value" else (),
    )
