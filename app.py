"""
WiFi Impairment Control — Flask backend.
Runs as non-root user 'impair'; delegates tc/ip commands to the root helper via sudo.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, render_template

from impair.validator import ImpairParams, ValidationError, validate_params

app = Flask(__name__)

HELPER_BIN = "/usr/local/sbin/impair-helper"
PROFILES_FILE = Path(__file__).parent / "profiles.json"
CUSTOM_PROFILES_FILE = Path(__file__).parent / "custom_profiles.json"
AP_IF = "wlan0"
IFB_IF = "ifb0"

# In-memory state — intentionally resets to clean on service restart.
_active_params: Optional[ImpairParams] = None
_active_profile: Optional[str] = None


# ---------------------------------------------------------------------------
# Helper invocation
# ---------------------------------------------------------------------------

def _call_helper(args: list[str]) -> tuple[bool, str]:
    cmd = ["sudo", HELPER_BIN] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return False, result.stderr.strip() or "Helper exited non-zero"
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Helper timed out"
    except FileNotFoundError:
        return False, f"Helper not found at {HELPER_BIN} — run setup.sh first"


def _params_to_helper_args(p: ImpairParams) -> list[str]:
    return [
        "apply",
        "--latency-ms", str(p.latency_ms),
        "--jitter-ms", str(p.jitter_ms),
        "--loss-pct", str(p.loss_pct),
        "--dup-pct", str(p.duplicate_pct),
        "--corrupt-pct", str(p.corrupt_pct),
        "--reorder-pct", str(p.reorder_pct),
        "--rate-down-kbps", str(p.rate_down_kbps),
        "--rate-up-kbps", str(p.rate_up_kbps),
    ]


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def _load_profiles() -> list[dict]:
    base = json.loads(PROFILES_FILE.read_text()) if PROFILES_FILE.exists() else []
    custom = json.loads(CUSTOM_PROFILES_FILE.read_text()) if CUSTOM_PROFILES_FILE.exists() else []
    return base + custom


def _read_tc_qdiscs() -> dict:
    """Read raw tc qdisc state for status display. tc show doesn't need root."""
    def _show(dev: str) -> str:
        try:
            r = subprocess.run(
                ["tc", "qdisc", "show", "dev", dev],
                capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    return {AP_IF: _show(AP_IF), IFB_IF: _show(IFB_IF)}


def _read_clients() -> list[dict]:
    """Parse dnsmasq leases file for connected clients."""
    leases_path = Path("/var/lib/misc/dnsmasq.leases")
    clients = []
    if not leases_path.exists():
        return clients
    for line in leases_path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            clients.append({
                "mac": parts[1],
                "ip": parts[2],
                "hostname": parts[3] if parts[3] != "*" else None,
                "expires": int(parts[0]),
            })
    return clients


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/status")
def get_status():
    params_dict = None
    if _active_params:
        params_dict = {
            "latency_ms": _active_params.latency_ms,
            "jitter_ms": _active_params.jitter_ms,
            "loss_pct": _active_params.loss_pct,
            "duplicate_pct": _active_params.duplicate_pct,
            "corrupt_pct": _active_params.corrupt_pct,
            "reorder_pct": _active_params.reorder_pct,
            "rate_down_kbps": _active_params.rate_down_kbps,
            "rate_up_kbps": _active_params.rate_up_kbps,
        }
    return jsonify({
        "ok": True,
        "data": {
            "active_profile": _active_profile,
            "is_clean": _active_params is None or _active_params.is_clean(),
            "summary": _active_params.summary() if _active_params else "clean",
            "params": params_dict,
            "tc_qdiscs": _read_tc_qdiscs(),
        },
    })


@app.get("/profiles")
def get_profiles():
    return jsonify({"ok": True, "data": _load_profiles()})


@app.post("/apply")
def post_apply():
    global _active_params, _active_profile

    body = request.get_json(silent=True) or {}
    try:
        params = validate_params(body)
    except ValidationError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if params.is_clean():
        return _do_clear()

    ok, err = _call_helper(_params_to_helper_args(params))
    if not ok:
        return jsonify({"ok": False, "error": err}), 500

    _active_params = params
    _active_profile = body.get("profile_name")
    return jsonify({"ok": True, "data": {"summary": params.summary()}})


@app.post("/clear")
def post_clear():
    return _do_clear()


def _do_clear():
    global _active_params, _active_profile
    ok, err = _call_helper(["clear"])
    if not ok:
        return jsonify({"ok": False, "error": err}), 500
    _active_params = None
    _active_profile = None
    return jsonify({"ok": True, "data": {"summary": "clean"}})


@app.post("/profiles")
def save_profile():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    label = body.get("label", "").strip()

    if not name or not label:
        return jsonify({"ok": False, "error": "'name' and 'label' are required"}), 400

    if not name.replace("_", "").replace("-", "").isalnum():
        return jsonify({"ok": False, "error": "'name' must be alphanumeric (underscores/hyphens ok)"}), 400

    try:
        params = validate_params(body)
    except ValidationError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    existing = json.loads(CUSTOM_PROFILES_FILE.read_text()) if CUSTOM_PROFILES_FILE.exists() else []
    updated = [p for p in existing if p.get("name") != name]
    updated.append({
        "name": name,
        "label": label,
        "description": body.get("description", ""),
        "latency_ms": params.latency_ms,
        "jitter_ms": params.jitter_ms,
        "loss_pct": params.loss_pct,
        "duplicate_pct": params.duplicate_pct,
        "corrupt_pct": params.corrupt_pct,
        "reorder_pct": params.reorder_pct,
        "rate_down_kbps": params.rate_down_kbps,
        "rate_up_kbps": params.rate_up_kbps,
        "custom": True,
    })
    CUSTOM_PROFILES_FILE.write_text(json.dumps(updated, indent=2))
    return jsonify({"ok": True, "data": {"name": name}}), 201


@app.get("/clients")
def get_clients():
    return jsonify({"ok": True, "data": _read_clients()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
