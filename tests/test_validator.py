import pytest
from impair.validator import validate_params, ValidationError, ImpairParams


def test_defaults_are_zero():
    p = validate_params({})
    assert p.latency_ms == 0
    assert p.loss_pct == 0.0
    assert p.is_clean()


def test_valid_params_round_trip():
    p = validate_params({
        "latency_ms": 100,
        "jitter_ms": 10,
        "loss_pct": 2.5,
        "duplicate_pct": 0.1,
        "corrupt_pct": 0.2,
        "reorder_pct": 5.0,
        "rate_down_kbps": 5000,
        "rate_up_kbps": 1000,
    })
    assert p.latency_ms == 100
    assert p.jitter_ms == 10
    assert p.loss_pct == 2.5
    assert p.rate_down_kbps == 5000
    assert not p.is_clean()


def test_values_clamped_to_max():
    p = validate_params({"latency_ms": 99999, "loss_pct": 200.0, "rate_down_kbps": 999_999_999})
    assert p.latency_ms == 10_000
    assert p.loss_pct == 100.0
    assert p.rate_down_kbps == 1_000_000


def test_values_clamped_to_min():
    p = validate_params({"latency_ms": -50, "loss_pct": -5.0})
    assert p.latency_ms == 0
    assert p.loss_pct == 0.0


def test_invalid_type_raises():
    with pytest.raises(ValidationError):
        validate_params({"latency_ms": "not-a-number"})

    with pytest.raises(ValidationError):
        validate_params({"loss_pct": "bad"})


def test_is_clean_true_only_when_all_zero():
    assert ImpairParams(0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0).is_clean()
    assert not ImpairParams(1, 0, 0.0, 0.0, 0.0, 0.0, 0, 0).is_clean()
    assert not ImpairParams(0, 0, 0.1, 0.0, 0.0, 0.0, 0, 0).is_clean()


def test_summary_clean():
    p = validate_params({})
    assert p.summary() == "clean"


def test_summary_active():
    p = validate_params({"latency_ms": 100, "loss_pct": 2.0, "rate_down_kbps": 5000})
    s = p.summary()
    assert "100ms" in s
    assert "2.0% loss" in s
    assert "5000k" in s


def test_immutable():
    p = validate_params({"latency_ms": 50})
    with pytest.raises(Exception):
        p.latency_ms = 100  # type: ignore[misc]
