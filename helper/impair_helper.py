#!/usr/bin/env python3
"""
Root-privilege impairment helper.
Installed to /usr/local/sbin/impair-helper, invoked via sudo by the Flask app.

Usage:
  impair-helper apply --latency-ms N --jitter-ms N --loss-pct F ... --rate-down-kbps N --rate-up-kbps N
  impair-helper clear
"""

import argparse
import subprocess
import sys

AP_IF = "wlan0"
IFB_IF = "ifb0"

LATENCY_MAX = 10_000
JITTER_MAX = 5_000
PCT_MAX = 100.0
RATE_MAX = 1_000_000


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _run_ok(cmd: list[str]) -> None:
    """Run, ignoring non-zero exit (used for delete commands that may no-op)."""
    subprocess.run(cmd, capture_output=True)


def clear() -> None:
    _run_ok(["tc", "qdisc", "del", "dev", AP_IF, "root"])
    _run_ok(["tc", "qdisc", "del", "dev", AP_IF, "ingress"])
    _run_ok(["tc", "qdisc", "del", "dev", IFB_IF, "root"])
    _run_ok(["ip", "link", "set", IFB_IF, "down"])


def _build_netem_args(
    latency_ms: int,
    jitter_ms: int,
    loss_pct: float,
    dup_pct: float,
    corrupt_pct: float,
    reorder_pct: float,
) -> list[str]:
    args: list[str] = []

    if latency_ms > 0:
        args += ["delay", f"{latency_ms}ms"]
        if jitter_ms > 0:
            args += [f"{jitter_ms}ms", "distribution", "normal"]
    elif jitter_ms > 0:
        # jitter without base delay is not meaningful; treat as 0 delay + jitter
        args += ["delay", f"{jitter_ms}ms", f"{jitter_ms}ms", "distribution", "normal"]

    if loss_pct > 0:
        args += ["loss", f"{loss_pct:.4f}%"]

    if dup_pct > 0:
        args += ["duplicate", f"{dup_pct:.4f}%"]

    if corrupt_pct > 0:
        args += ["corrupt", f"{corrupt_pct:.4f}%"]

    if reorder_pct > 0 and latency_ms > 0:
        # reorder requires a base delay to be observable
        args += ["reorder", f"{reorder_pct:.4f}%", "25%"]

    return args


def _htb_burst(rate_kbps: int) -> str:
    # ~100ms worth of data, minimum 15kbit
    return f"{max(15, rate_kbps // 10)}kbit"


def _apply_side(
    dev: str,
    latency_ms: int,
    jitter_ms: int,
    loss_pct: float,
    dup_pct: float,
    corrupt_pct: float,
    reorder_pct: float,
    rate_kbps: int,
) -> None:
    """Apply netem + optional HTB rate limit on dev egress."""
    netem_args = _build_netem_args(latency_ms, jitter_ms, loss_pct, dup_pct, corrupt_pct, reorder_pct)
    has_netem = bool(netem_args)
    has_rate = rate_kbps > 0

    if not has_netem and not has_rate:
        return

    if has_rate:
        burst = _htb_burst(rate_kbps)
        _run(["tc", "qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "default", "10"])
        _run([
            "tc", "class", "add", "dev", dev,
            "parent", "1:", "classid", "1:10",
            "htb", "rate", f"{rate_kbps}kbit", "burst", burst,
        ])
        if has_netem:
            _run(["tc", "qdisc", "add", "dev", dev, "parent", "1:10", "handle", "10:", "netem"] + netem_args)
    else:
        _run(["tc", "qdisc", "add", "dev", dev, "root", "netem"] + netem_args)


def apply_impairment(
    latency_ms: int,
    jitter_ms: int,
    loss_pct: float,
    dup_pct: float,
    corrupt_pct: float,
    reorder_pct: float,
    rate_down_kbps: int,
    rate_up_kbps: int,
) -> None:
    clear()

    # Egress: Pi → test devices (download direction for clients)
    _apply_side(AP_IF, latency_ms, jitter_ms, loss_pct, dup_pct, corrupt_pct, reorder_pct, rate_down_kbps)

    # Ingress: test devices → internet (upload direction for clients), via ifb redirect
    netem_args = _build_netem_args(latency_ms, jitter_ms, loss_pct, dup_pct, corrupt_pct, reorder_pct)
    if netem_args or rate_up_kbps > 0:
        _run(["modprobe", "ifb"])
        # modprobe's numifbs= only takes effect the first time the module is
        # loaded — if it was already loaded (by the OS or a prior run) with
        # numifbs=0, the device is never created. Create it explicitly instead.
        _run_ok(["ip", "link", "add", IFB_IF, "type", "ifb"])
        _run(["ip", "link", "set", IFB_IF, "up"])
        _run(["tc", "qdisc", "add", "dev", AP_IF, "ingress"])
        _run([
            "tc", "filter", "add", "dev", AP_IF, "parent", "ffff:",
            "protocol", "ip", "u32", "match", "u32", "0", "0",
            "action", "mirred", "egress", "redirect", "dev", IFB_IF,
        ])
        _apply_side(IFB_IF, latency_ms, jitter_ms, loss_pct, dup_pct, corrupt_pct, reorder_pct, rate_up_kbps)


def _clamp_int(v: int, lo: int, hi: int, name: str) -> int:
    clamped = max(lo, min(hi, v))
    if clamped != v:
        print(f"warning: {name}={v} clamped to {clamped}", file=sys.stderr)
    return clamped


def _clamp_float(v: float, lo: float, hi: float, name: str) -> float:
    clamped = max(lo, min(hi, v))
    if clamped != v:
        print(f"warning: {name}={v} clamped to {clamped}", file=sys.stderr)
    return clamped


def main() -> None:
    parser = argparse.ArgumentParser(description="Impairment helper (root only)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("clear")

    ap = sub.add_parser("apply")
    ap.add_argument("--latency-ms", type=int, default=0)
    ap.add_argument("--jitter-ms", type=int, default=0)
    ap.add_argument("--loss-pct", type=float, default=0.0)
    ap.add_argument("--dup-pct", type=float, default=0.0)
    ap.add_argument("--corrupt-pct", type=float, default=0.0)
    ap.add_argument("--reorder-pct", type=float, default=0.0)
    ap.add_argument("--rate-down-kbps", type=int, default=0)
    ap.add_argument("--rate-up-kbps", type=int, default=0)

    args = parser.parse_args()

    if args.command == "clear":
        clear()
        print("cleared")
    elif args.command == "apply":
        apply_impairment(
            latency_ms=_clamp_int(args.latency_ms, 0, LATENCY_MAX, "latency-ms"),
            jitter_ms=_clamp_int(args.jitter_ms, 0, JITTER_MAX, "jitter-ms"),
            loss_pct=_clamp_float(args.loss_pct, 0.0, PCT_MAX, "loss-pct"),
            dup_pct=_clamp_float(args.dup_pct, 0.0, PCT_MAX, "dup-pct"),
            corrupt_pct=_clamp_float(args.corrupt_pct, 0.0, PCT_MAX, "corrupt-pct"),
            reorder_pct=_clamp_float(args.reorder_pct, 0.0, PCT_MAX, "reorder-pct"),
            rate_down_kbps=_clamp_int(args.rate_down_kbps, 0, RATE_MAX, "rate-down-kbps"),
            rate_up_kbps=_clamp_int(args.rate_up_kbps, 0, RATE_MAX, "rate-up-kbps"),
        )
        print("applied")


if __name__ == "__main__":
    main()
