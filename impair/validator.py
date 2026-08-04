from dataclasses import dataclass


@dataclass(frozen=True)
class ImpairParams:
    latency_ms: int
    jitter_ms: int
    loss_pct: float
    duplicate_pct: float
    corrupt_pct: float
    reorder_pct: float
    rate_down_kbps: int
    rate_up_kbps: int

    def is_clean(self) -> bool:
        return (
            self.latency_ms == 0
            and self.jitter_ms == 0
            and self.loss_pct == 0.0
            and self.duplicate_pct == 0.0
            and self.corrupt_pct == 0.0
            and self.reorder_pct == 0.0
            and self.rate_down_kbps == 0
            and self.rate_up_kbps == 0
        )

    def summary(self) -> str:
        parts = []
        if self.latency_ms:
            jitter = f" ±{self.jitter_ms}ms" if self.jitter_ms else ""
            parts.append(f"{self.latency_ms}ms latency{jitter}")
        if self.loss_pct:
            parts.append(f"{self.loss_pct}% loss")
        if self.duplicate_pct:
            parts.append(f"{self.duplicate_pct}% dup")
        if self.corrupt_pct:
            parts.append(f"{self.corrupt_pct}% corrupt")
        if self.reorder_pct:
            parts.append(f"{self.reorder_pct}% reorder")
        if self.rate_down_kbps or self.rate_up_kbps:
            down = f"{self.rate_down_kbps}k" if self.rate_down_kbps else "∞"
            up = f"{self.rate_up_kbps}k" if self.rate_up_kbps else "∞"
            parts.append(f"{down}↓ / {up}↑ kbps")
        return " · ".join(parts) if parts else "clean"


class ValidationError(ValueError):
    pass


def validate_params(data: dict) -> ImpairParams:
    """Validate and clamp all impairment parameters from a dict (e.g. request JSON)."""

    def _int(key: str, lo: int, hi: int, default: int = 0) -> int:
        v = data.get(key, default)
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            raise ValidationError(f"'{key}' must be an integer, got {v!r}")

    def _float(key: str, lo: float, hi: float, default: float = 0.0) -> float:
        v = data.get(key, default)
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            raise ValidationError(f"'{key}' must be a number, got {v!r}")

    return ImpairParams(
        latency_ms=_int("latency_ms", 0, 10_000),
        jitter_ms=_int("jitter_ms", 0, 5_000),
        loss_pct=_float("loss_pct", 0.0, 100.0),
        duplicate_pct=_float("duplicate_pct", 0.0, 100.0),
        corrupt_pct=_float("corrupt_pct", 0.0, 100.0),
        reorder_pct=_float("reorder_pct", 0.0, 100.0),
        rate_down_kbps=_int("rate_down_kbps", 0, 1_000_000),
        rate_up_kbps=_int("rate_up_kbps", 0, 1_000_000),
    )
