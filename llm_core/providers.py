"""In-memory registry of models known to be available on the configured proxy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """Metadata about one model exposed by the proxy."""

    id: str
    context_length: int | None = None
    supports_json_schema: bool = False


class ModelRegistry:
    """Keyed lookup of ModelSpec, populated by the host app (e.g. from list_models)."""

    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}

    def register(self, spec: ModelSpec) -> None:
        self._models[spec.id] = spec

    def get(self, model_id: str) -> ModelSpec | None:
        return self._models.get(model_id)

    def all(self) -> list[ModelSpec]:
        return list(self._models.values())
