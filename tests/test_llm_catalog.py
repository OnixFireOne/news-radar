"""Catalog startup validation and honest cost accounting."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from llm_core import (
    CatalogError, CompletionUsage, ModelPrice, ProviderProfile,
    compute_cost, find_todo_field, load_catalog, resolve_active,
)


@pytest.fixture
def catalog_data():
    return {
        "_note": "Realistic proxy and local profiles; prices are test assumptions.",
        "aiprime.messages": {
            "name": "aiprime.messages", "base_url": "https://proxy.example.com/v1",
            "protocol": "messages", "auth_style": "x-api-key",
            "api_version": "2023-06-01", "max_tokens": 4096,
            "models": {"default": "claude-sonnet-5", "digest": "claude-opus-5"},
            "cost_source": "table", "input_overhead": 1317,
            "price_table": {
                "claude-sonnet-5": {"in_per_million": 3, "out_per_million": 15},
                "claude-opus-5": {"in_per_million": 5, "out_per_million": 25},
            },
            "extra_headers": {}, "models_path": "/models",
        },
        "local": {
            "name": "local", "base_url": "http://llm:5000/v1",
            "protocol": "chat_completions", "auth_style": "none",
            "models": {"default": "local-model"}, "cost_source": "none",
            "gpu_lock": True, "chat_template_kwargs": True, "max_concurrency": 1,
        },
    }


def write_catalog(tmp_path, data):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_catalog_and_key_convention(tmp_path, catalog_data):
    catalog = load_catalog(write_catalog(tmp_path, catalog_data))
    profile = catalog["aiprime.messages"]
    assert profile.api_key_env == "LLM_KEY_AIPRIME"
    assert profile.input_overhead == 1317
    assert profile.models["digest"] == "claude-opus-5"
    active = resolve_active(catalog, ["aiprime.messages", "local"], {"LLM_KEY_AIPRIME": "secret"})
    assert active[0].api_key == "secret"
    assert "secret" not in repr(active[0])
    assert active[1].api_key == ""
    with pytest.raises(TypeError):
        profile.models["default"] = "changed"


@pytest.mark.parametrize("field,value,expected", [
    ("unknown", True, "unknown"), ("models", {}, "models.default"),
    ("max_tokens", None, "max_tokens"), ("protocol", "other", "protocol"),
    ("auth_style", "basic", "auth_style"), ("gpu_lock", 1, "gpu_lock"),
    ("max_tokens", True, "max_tokens"), ("input_overhead", -1, "input_overhead"),
    ("price_table", {}, "price_table"), ("base_url", 7, "base_url"),
    ("extra_headers", {"X-Test": 1}, "extra_headers.X-Test"),
])
def test_invalid_fields_name_profile_and_field(tmp_path, catalog_data, field, value, expected):
    catalog_data["aiprime.messages"][field] = value
    with pytest.raises(CatalogError) as error:
        load_catalog(write_catalog(tmp_path, catalog_data))
    assert "aiprime.messages" in str(error.value)
    assert expected in str(error.value)


def test_missing_required_and_override(tmp_path, catalog_data):
    del catalog_data["aiprime.messages"]["base_url"]
    with pytest.raises(CatalogError, match="base_url"):
        load_catalog(write_catalog(tmp_path, catalog_data))
    catalog_data["aiprime.messages"]["base_url"] = "https://example.com/v1"
    catalog_data["aiprime.messages"]["api_key_env"] = "CUSTOM_KEY"
    catalog = load_catalog(write_catalog(tmp_path, catalog_data))
    assert resolve_active(catalog, ["aiprime.messages"], {"CUSTOM_KEY": "private"})[0].api_key == "private"
    with pytest.raises(CatalogError, match="set CUSTOM_KEY in .env"):
        resolve_active(catalog, ["aiprime.messages"], {"CUSTOM_KEY": "  "})


@pytest.mark.parametrize("names", [[], ["local", "local"], ["missing"], ["local", "aiprime.messages"]])
def test_all_active_names_are_validated(tmp_path, catalog_data, names):
    catalog = load_catalog(write_catalog(tmp_path, catalog_data))
    with pytest.raises(CatalogError) as error:
        resolve_active(catalog, names, {"UNUSED_KEY": "never-print-this"})
    assert "never-print-this" not in str(error.value)
    if names == ["missing"]:
        assert all(name in str(error.value) for name in catalog)


@pytest.mark.parametrize("field,value,expected", [
    ("base_url", "TODO(unverified)", "base_url"),
    ("models", {"default": "TODO(unverified)"}, "models.default"),
    ("extra_headers", {"nested": "TODO(unverified)"}, "extra_headers.nested"),
    ("price_table", {"TODO(unverified)": {"in_per_million": 0, "out_per_million": 0}}, "price_table"),
])
def test_todo_guard(tmp_path, catalog_data, field, value, expected):
    entry = catalog_data["aiprime.messages"]
    entry["cost_source"] = "none"
    entry[field] = value
    entry["_comment"] = "TODO(unverified)"
    catalog = load_catalog(write_catalog(tmp_path, catalog_data))
    assert expected in find_todo_field(catalog["aiprime.messages"])
    with pytest.raises(CatalogError, match=expected):
        resolve_active(catalog, ["local", "aiprime.messages"], {"LLM_KEY_AIPRIME": "do-not-print"})


def test_cost_sources_and_partial_counts():
    profile = ProviderProfile("p", "http://example/v1", "chat_completions", "none",
                              {"default": "m"}, "table", price_table={"m": ModelPrice(3, 15)},
                              input_overhead=1317)
    usage = CompletionUsage(2000, 100, 2100)
    assert compute_cost(profile, "m", usage, None) == pytest.approx(0.0075)
    assert compute_cost(profile, "m", usage, {"completion_tokens": 100}) is None
    assert compute_cost(profile, "m", CompletionUsage(0, 0, 0), None) is None
    assert compute_cost(profile, "missing", usage, None) is None
    assert compute_cost(profile, "m", None, None) is None
    assert compute_cost(replace(profile, cost_source="none"), "m", usage, {"cost": 1}) is None
    provider = replace(profile, cost_source="provider")
    assert compute_cost(provider, "m", None, {"cost": 0.125}) == 0.125
    for value in (True, "1", float("nan"), float("inf"), None):
        assert compute_cost(provider, "m", usage, {"cost": value}) is None


def test_invalid_json_and_empty_catalog(tmp_path):
    path: Path = tmp_path / "providers.json"
    for content in ("broken", "[]", "{}"):
        path.write_text(content)
        with pytest.raises(CatalogError):
            load_catalog(path)
