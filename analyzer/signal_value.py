"""Represent telemetry values with confidence and validity.

SignalValue wraps a raw measurement with metadata about whether
the value is trustworthy, how it was obtained, and why it might
be invalid. Used for settle time, brake split, slip proxies,
thermal means, LLTD proxy, body slip, pressure/carcass means,
and oscillation measures.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SignalValue:
    """A telemetry measurement with confidence and validity metadata."""

    value: float | None
    valid: bool
    confidence: float  # 0.0 - 1.0
    source: str
    invalid_reason: str = ""
    fallback_used: bool = False

    def usable(self) -> bool:
        """Return True if this signal is valid and has a value."""
        return self.valid and self.value is not None

    def value_or(self, default: float) -> float:
        """Return the value if usable, otherwise the default."""
        if self.usable() and self.value is not None:
            return self.value
        return default

    @classmethod
    def missing(cls, source: str = "", reason: str = "not_available") -> SignalValue:
        """Create a SignalValue representing a missing/unavailable metric."""
        return cls(
            value=None,
            valid=False,
            confidence=0.0,
            source=source,
            invalid_reason=reason,
        )

    @classmethod
    def trusted(cls, value: float, source: str, confidence: float = 0.85) -> SignalValue:
        """Create a SignalValue for a trusted direct measurement."""
        return cls(
            value=value,
            valid=True,
            confidence=confidence,
            source=source,
        )

    @classmethod
    def proxy(cls, value: float, source: str, confidence: float = 0.6) -> SignalValue:
        """Create a SignalValue for a proxy/derived measurement."""
        return cls(
            value=value,
            valid=True,
            confidence=confidence,
            source=source,
            fallback_used=True,
        )
