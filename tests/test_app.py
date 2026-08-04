import json
import pytest
from unittest.mock import patch, MagicMock

import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point profiles file to the real profiles.json in the repo root
    import pathlib
    monkeypatch.setattr(flask_app, "PROFILES_FILE", pathlib.Path("profiles.json"))
    monkeypatch.setattr(flask_app, "CUSTOM_PROFILES_FILE", tmp_path / "custom_profiles.json")
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


def _mock_helper_ok(*_args, **_kwargs):
    return MagicMock(returncode=0, stdout="applied\n", stderr="")


def _mock_helper_fail(*_args, **_kwargs):
    return MagicMock(returncode=1, stdout="", stderr="tc error: some failure")


# ---------------------------------------------------------------------------
# GET /profiles
# ---------------------------------------------------------------------------

def test_get_profiles_returns_list(client):
    r = client.get("/profiles")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert isinstance(data["data"], list)
    names = [p["name"] for p in data["data"]]
    assert "clean" in names
    assert "satellite" in names


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

def test_status_starts_clean(client):
    flask_app._active_params = None
    flask_app._active_profile = None
    r = client.get("/status")
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["is_clean"] is True
    assert d["active_profile"] is None


# ---------------------------------------------------------------------------
# POST /apply
# ---------------------------------------------------------------------------

def test_apply_valid_params(client):
    with patch("subprocess.run", side_effect=_mock_helper_ok):
        r = client.post("/apply", json={
            "latency_ms": 100,
            "jitter_ms": 10,
            "loss_pct": 1.0,
            "rate_down_kbps": 5000,
            "rate_up_kbps": 1000,
        })
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert flask_app._active_params is not None
    assert flask_app._active_params.latency_ms == 100


def test_apply_invalid_type_rejected(client):
    r = client.post("/apply", json={"latency_ms": "not-a-number"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_apply_cleans_when_all_zero(client):
    flask_app._active_params = None  # reset state
    with patch("subprocess.run", side_effect=_mock_helper_ok):
        r = client.post("/apply", json={
            "latency_ms": 0, "jitter_ms": 0,
            "loss_pct": 0, "duplicate_pct": 0,
            "corrupt_pct": 0, "reorder_pct": 0,
            "rate_down_kbps": 0, "rate_up_kbps": 0,
        })
    assert r.status_code == 200
    assert flask_app._active_params is None


def test_apply_helper_failure_returns_500(client):
    with patch("subprocess.run", side_effect=_mock_helper_fail):
        r = client.post("/apply", json={"latency_ms": 100})
    assert r.status_code == 500
    assert r.get_json()["ok"] is False


# ---------------------------------------------------------------------------
# POST /clear
# ---------------------------------------------------------------------------

def test_clear_resets_state(client):
    from impair.validator import ImpairParams
    flask_app._active_params = ImpairParams(100, 10, 1.0, 0.0, 0.0, 0.0, 5000, 1000)
    flask_app._active_profile = "congested_wifi"
    with patch("subprocess.run", side_effect=_mock_helper_ok):
        r = client.post("/clear")
    assert r.status_code == 200
    assert flask_app._active_params is None
    assert flask_app._active_profile is None


# ---------------------------------------------------------------------------
# POST /profiles (save custom)
# ---------------------------------------------------------------------------

def test_save_custom_profile(client):
    with patch("subprocess.run", side_effect=_mock_helper_ok):
        r = client.post("/profiles", json={
            "name": "my_profile",
            "label": "My Test Profile",
            "description": "A test",
            "latency_ms": 50,
            "loss_pct": 0.5,
        })
    assert r.status_code == 201
    d = r.get_json()
    assert d["ok"] is True
    assert d["data"]["name"] == "my_profile"


def test_save_custom_profile_missing_name(client):
    r = client.post("/profiles", json={"label": "No Name"})
    assert r.status_code == 400


def test_save_custom_profile_invalid_name(client):
    r = client.post("/profiles", json={"name": "has spaces!", "label": "Bad"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /clients
# ---------------------------------------------------------------------------

def test_clients_returns_list_when_no_leases(client, tmp_path, monkeypatch):
    # Non-existent leases path → empty list, not an error
    import pathlib
    monkeypatch.setattr(flask_app, "_read_clients", lambda: [])
    r = client.get("/clients")
    assert r.status_code == 200
    assert r.get_json()["data"] == []
