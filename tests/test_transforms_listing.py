"""Tests for GET /v1/transforms (DESIGN §4, cycle T7).

The listing serializes the registry with both JSON Schemas but never leaks the internal
Jinja ``template`` source or the Python ``validators``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tts import app as app_module
from tts.app import app
from tts.config import Settings
from tts.transforms import register_all

_ENTRY_FIELDS = {
    "name",
    "version",
    "model",
    "input_budget",
    "over_budget",
    "options_schema",
    "output_schema",
}


@pytest.fixture
def prod_registry(monkeypatch):
    """Register the production transforms (no echo, no auth)."""
    prod = Settings(env="prod")
    monkeypatch.setattr(app_module.app.state, "settings", prod)
    register_all(prod)
    return prod


def test_listing_returns_transforms_array(prod_registry):
    client = TestClient(app)
    resp = client.get("/v1/transforms")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"transforms"}
    assert isinstance(body["transforms"], list)
    assert body["transforms"], "expected at least one registered transform"


def test_each_entry_has_exactly_the_spec_fields(prod_registry):
    client = TestClient(app)
    entries = client.get("/v1/transforms").json()["transforms"]
    for entry in entries:
        assert set(entry) == _ENTRY_FIELDS
        assert isinstance(entry["options_schema"], dict)
        assert isinstance(entry["output_schema"], dict)
        # Internal fields never leak.
        assert "template" not in entry
        assert "validators" not in entry


def test_known_transform_present_with_binding(prod_registry):
    client = TestClient(app)
    entries = client.get("/v1/transforms").json()["transforms"]
    by_name = {e["name"]: e for e in entries}
    assert "image-prompt" in by_name
    assert by_name["image-prompt"]["model"] == "qwen3.5:9b"
    # A real output_schema is carried through, not an empty stub.
    assert by_name["image-prompt"]["output_schema"].get("properties")


def test_listing_sorted_by_name(prod_registry):
    client = TestClient(app)
    names = [e["name"] for e in client.get("/v1/transforms").json()["transforms"]]
    assert names == sorted(names)
