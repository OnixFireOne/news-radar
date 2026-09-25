"""Validated provider data; paths and environment values are supplied by the host."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, NoReturn

from llm_core.client import CompletionUsage, LLMCoreError


class CatalogError(LLMCoreError):
    """Invalid provider catalog or active-provider selection."""


@dataclass(frozen=True)
class ModelPrice:
    in_per_million: float
    out_per_million: float


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    base_url: str
    protocol: str
    auth_style: str
    models: Mapping[str, str]
    cost_source: str
    api_key_env: str = ""
    api_version: str | None = None
    max_tokens: int | None = None
    models_path: str | None = None
    price_table: Mapping[str, ModelPrice] | Literal["TODO(unverified)"] = field(default_factory=dict)
    input_overhead: int = 0
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    gpu_lock: bool = False
    chat_template_kwargs: bool = False
    max_concurrency: int | None = None

    def __post_init__(self) -> None:
        if not self.api_key_env:
            prefix = re.sub(r"[^A-Z0-9]", "_", self.name.split(".", 1)[0].upper())
            object.__setattr__(self, "api_key_env", f"LLM_KEY_{prefix}")
        if not isinstance(self.price_table, str):
            object.__setattr__(self, "price_table", MappingProxyType(dict(self.price_table)))
        for attr in ("models", "extra_headers"):
            object.__setattr__(self, attr, MappingProxyType(dict(getattr(self, attr))))


@dataclass(frozen=True)
class ActiveProvider:
    profile: ProviderProfile
    api_key: str = field(repr=False)


def _fail(name: str, field_name: str, reason: str) -> NoReturn:
    raise CatalogError(f'provider "{name}": {field_name} {reason}')


def _object(value: object, name: str, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail(name, field_name, "must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            _fail(name, field_name, "must have string keys")
        if not key.startswith("_"):
            result[key] = item
    return result


def _string(value: object, name: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(name, field_name, "must be a non-empty string")
    return value


def _validate(name: str, raw: object) -> ProviderProfile:
    obj = _object(raw, name, "profile")
    known = {f.name for f in fields(ProviderProfile)}
    for key in obj:
        if key not in known:
            _fail(name, key, "is an unknown field")
    strings = {key: _string(obj.get(key), name, key) for key in
               ("name", "base_url", "protocol", "auth_style", "cost_source")}
    if strings["name"] != name:
        _fail(name, "name", "must match the catalog key")
    for key, allowed in (("protocol", ("chat_completions", "messages")),
                         ("auth_style", ("bearer", "x-api-key", "none")),
                         ("cost_source", ("provider", "table", "none"))):
        if strings[key] not in allowed:
            _fail(name, key, "must be one of " + ", ".join(allowed))
    optional_strings: dict[str, str | None] = {}
    for key in ("api_version", "models_path"):
        value = obj.get(key)
        optional_strings[key] = None if value is None else _string(value, name, key)
    key_env = _string(obj["api_key_env"], name, "api_key_env") if "api_key_env" in obj else ""
    ints: dict[str, int | None] = {}
    for key in ("max_tokens", "max_concurrency", "input_overhead"):
        value = obj.get(key, 0 if key == "input_overhead" else None)
        if value is None and key != "input_overhead":
            ints[key] = None
        elif type(value) is int and value >= (0 if key == "input_overhead" else 1):
            ints[key] = value
        else:
            _fail(name, key, "must be a non-negative integer" if key == "input_overhead" else "must be a positive integer")
    if strings["protocol"] == "messages" and ints["max_tokens"] is None:
        _fail(name, "max_tokens", "is required for messages")
    bools: dict[str, bool] = {}
    for key in ("gpu_lock", "chat_template_kwargs"):
        value = obj.get(key, False)
        if not isinstance(value, bool):
            _fail(name, key, "must be a boolean")
        bools[key] = value
    models = {key: _string(value, name, f"models.{key}") for key, value in
              _object(obj.get("models"), name, "models").items()}
    if "default" not in models:
        _fail(name, "models.default", "is required")
    headers: dict[str, str] = {}
    for key, value in _object(obj.get("extra_headers", {}), name, "extra_headers").items():
        if not isinstance(value, str):
            _fail(name, f"extra_headers.{key}", "must be a string")
        headers[key] = value
    prices: dict[str, ModelPrice] = {}
    raw_prices = obj.get("price_table", {})
    unverified_prices = raw_prices == "TODO(unverified)"
    for model, value in _object({} if unverified_prices else raw_prices, name, "price_table").items():
        path = f"price_table.{model}"
        entry = _object(value, name, path)
        for key in entry:
            if key not in ("in_per_million", "out_per_million"):
                _fail(name, f"{path}.{key}", "is an unknown field")
        numbers: dict[str, float] = {}
        for key in ("in_per_million", "out_per_million"):
            number = entry.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                _fail(name, f"{path}.{key}", "must be a finite non-negative number")
            try:
                converted = float(number)
            except OverflowError:
                _fail(name, f"{path}.{key}", "must be finite")
            if not math.isfinite(converted) or converted < 0:
                _fail(name, f"{path}.{key}", "must be a finite non-negative number")
            numbers[key] = converted
        prices[model] = ModelPrice(**numbers)
    if strings["cost_source"] == "table" and not unverified_prices:
        for model in models.values():
            if model not in prices:
                _fail(name, "price_table", "must price every model in models")
    return ProviderProfile(
        name=name, base_url=strings["base_url"], protocol=strings["protocol"],
        auth_style=strings["auth_style"], models=models, cost_source=strings["cost_source"],
        api_key_env=key_env, api_version=optional_strings["api_version"],
        models_path=optional_strings["models_path"], max_tokens=ints["max_tokens"],
        max_concurrency=ints["max_concurrency"], input_overhead=ints["input_overhead"] or 0,
        price_table="TODO(unverified)" if unverified_prices else prices, extra_headers=headers, gpu_lock=bools["gpu_lock"],
        chat_template_kwargs=bools["chat_template_kwargs"],
    )


def load_catalog(path: str | Path) -> dict[str, ProviderProfile]:
    """Read only the explicitly supplied catalog and validate every profile."""
    try:
        raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError("providers catalog: file cannot be read as JSON") from exc
    entries = _object(raw, "<catalog>", "root")
    if not entries:
        raise CatalogError("providers catalog: no profiles defined")
    return {name: _validate(name, value) for name, value in entries.items()}


def find_todo_field(profile: ProviderProfile) -> str | None:
    def visit(value: object, path: str) -> str | None:
        if isinstance(value, str) and value == "TODO(unverified)":
            return path
        if is_dataclass(value) and not isinstance(value, type):
            for attr in fields(value):
                found = visit(getattr(value, attr.name), f"{path}.{attr.name}" if path else attr.name)
                if found:
                    return found
        elif isinstance(value, Mapping):
            for key, item in value.items():
                child = f"{path}.{key}"
                found = visit(key, child) or visit(item, child)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                found = visit(item, f"{path}[{index}]")
                if found:
                    return found
        return None
    return visit(profile, "")


def resolve_active(catalog: Mapping[str, ProviderProfile], names: Sequence[str],
                   env: Mapping[str, str]) -> list[ActiveProvider]:
    if not names:
        raise CatalogError("providers: at least one active profile is required")
    active: list[ActiveProvider] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            _fail(name, "name", "is duplicated")
        seen.add(name)
        if name not in catalog:
            _fail(name, "name", "is unknown; available: " + ", ".join(catalog))
        profile = catalog[name]
        todo = find_todo_field(profile)
        if todo:
            _fail(name, todo, "contains TODO(unverified)")
        api_key = "" if profile.auth_style == "none" else env.get(profile.api_key_env, "")
        if profile.auth_style != "none" and not api_key.strip():
            raise CatalogError(f'provider "{name}": set {profile.api_key_env} in .env')
        active.append(ActiveProvider(profile, api_key))
    return active


def compute_cost(profile: ProviderProfile, model: str, usage: CompletionUsage | None,
                 raw_usage: Mapping[str, object] | None) -> float | None:
    if profile.cost_source == "none":
        return None
    if profile.cost_source == "provider":
        cost = raw_usage.get("cost") if raw_usage else None
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            return None
        try:
            return float(cost) if math.isfinite(cost) else None
        except OverflowError:
            return None
    if isinstance(profile.price_table, str):
        _fail(profile.name, "price_table", "contains TODO(unverified)")
    price = profile.price_table.get(model)
    if price is None or usage is None:
        return None
    # Raw counts distinguish missing fields from legacy client's zero defaults.
    if raw_usage is not None:
        pairs = (("prompt_tokens", "completion_tokens"), ("input_tokens", "output_tokens"))
        if not any(all(type(raw_usage.get(key)) is int for key in pair) for pair in pairs):
            return None
    if usage.prompt_tokens < 0 or usage.completion_tokens < 0:
        return None
    if usage.prompt_tokens == 0 and usage.completion_tokens == 0:
        return None
    return (usage.prompt_tokens * price.in_per_million +
            usage.completion_tokens * price.out_per_million) / 1_000_000
