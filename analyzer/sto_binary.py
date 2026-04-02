"""Generic binary STO container decode helpers.

This module only understands the outer version-3 container layout:

- 16-byte little-endian header (4 uint32 words)
- 40-byte opaque envelope
- two payload chunks split at the boundary described by the header
- optional trailer bytes after the payload

The inner setup blob is still opaque. We preserve it and surface any
UTF-16LE note text that appears after the binary payload so higher layers
can debug provider-specific files and attach car-specific oracles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import struct

_HEADER_WORD_COUNT = 4
_HEADER_SIZE = _HEADER_WORD_COUNT * 4
_ENVELOPE_SIZE = 40
_MIN_UTF16_ASCII_CHARS = 6

_CAR_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(acura|arx06|arxgtp)", re.IGNORECASE), "acuraarx06gtp"),
    (re.compile(r"(bmw|bmwlmdh)", re.IGNORECASE), "bmwlmdh"),
    (re.compile(r"(cadillac|vseries)", re.IGNORECASE), "cadillacvseriesr"),
    (re.compile(r"(ferrari|499p)", re.IGNORECASE), "ferrari499p"),
    (re.compile(r"(porsche|963)", re.IGNORECASE), "porsche963"),
)


def _hex_preview(data: bytes, limit: int = 16) -> str:
    sample = data[:limit]
    return " ".join(f"{byte:02x}" for byte in sample)


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _scan_utf16le_ascii_runs(data: bytes, min_chars: int = _MIN_UTF16_ASCII_CHARS) -> list[tuple[int, int, str]]:
    """Find ASCII-heavy UTF-16LE note blocks inside arbitrary bytes."""
    hits: list[tuple[int, int, str]] = []
    index = 0
    size = len(data)
    while index + 1 < size:
        chars: list[str] = []
        start = index
        cursor = index
        while cursor + 1 < size:
            lo = data[cursor]
            hi = data[cursor + 1]
            if hi == 0 and 32 <= lo <= 126:
                chars.append(chr(lo))
                cursor += 2
                continue
            if hi == 0 and lo in (9, 10, 13):
                chars.append(" ")
                cursor += 2
                continue
            break
        if len(chars) >= min_chars:
            text = _normalize_spaces("".join(chars))
            hits.append((start, cursor - start, text))
            index = cursor
        else:
            index += 1
    return hits


def _infer_car_id(name_hint: str, notes_text: str) -> str:
    haystacks = [name_hint, notes_text]
    for haystack in haystacks:
        for pattern, car_id in _CAR_HINTS:
            if pattern.search(haystack):
                return car_id
    return ""


def _infer_provider_name(name_hint: str, notes_text: str) -> str:
    haystack = f"{name_hint} {notes_text}".lower()
    if "p1doks" in haystack or "jaden munoz" in haystack:
        return "p1doks"
    if "virtual racing school" in haystack or "michele costantini" in haystack or name_hint.lower().startswith("vrs_"):
        return "vrs"
    if "apex racing academy" in haystack or "owen caryl" in haystack or name_hint.lower().startswith("ara_"):
        return "ara"
    if "grid-and-go" in haystack or "daniel sivi-szabo" in haystack:
        return "grid-and-go"
    return ""


@dataclass(frozen=True)
class RawStoEntry:
    """Debug-friendly description of a preserved raw block inside the STO."""

    name: str
    kind: str
    offset: int
    length: int
    text: str | None = None
    preview_hex: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "offset": self.offset,
            "length": self.length,
            "text": self.text,
            "preview_hex": self.preview_hex,
        }


@dataclass
class DecodedSto:
    """Outer-container decode of a binary STO file."""

    source_path: Path
    version: int
    header_words: tuple[int, int, int, int]
    sha256: str
    car_id: str
    provider_name: str
    notes_text: str
    raw_entries: list[RawStoEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    envelope_bytes: bytes = field(default=b"", repr=False)
    payload_a: bytes = field(default=b"", repr=False)
    payload_b: bytes = field(default=b"", repr=False)
    payload: bytes = field(default=b"", repr=False)
    setup_blob: bytes = field(default=b"", repr=False)
    trailer_bytes: bytes = field(default=b"", repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "version": self.version,
            "header_words": list(self.header_words),
            "sha256": self.sha256,
            "car_id": self.car_id,
            "provider_name": self.provider_name,
            "notes_text": self.notes_text,
            "payload_a_length": len(self.payload_a),
            "payload_b_length": len(self.payload_b),
            "payload_length": len(self.payload),
            "setup_blob_length": len(self.setup_blob),
            "trailer_length": len(self.trailer_bytes),
            "envelope_preview_hex": _hex_preview(self.envelope_bytes),
            "payload_a_preview_hex": _hex_preview(self.payload_a),
            "payload_b_preview_hex": _hex_preview(self.payload_b),
            "setup_blob_preview_hex": _hex_preview(self.setup_blob),
            "trailer_preview_hex": _hex_preview(self.trailer_bytes),
            "raw_entries": [entry.to_dict() for entry in self.raw_entries],
            "warnings": list(self.warnings),
        }


def decode_sto(path: str | Path) -> DecodedSto:
    """Decode a version-3 binary STO container.

    The inner setup blob is preserved as opaque bytes. Higher layers can map
    known file hashes or future reverse-engineered decode tables onto the
    preserved blob.
    """

    source_path = Path(path)
    data = source_path.read_bytes()
    if len(data) < _HEADER_SIZE + _ENVELOPE_SIZE:
        raise ValueError(f"STO file is too small to contain a v3 header: {source_path}")

    header_words = struct.unpack("<4I", data[:_HEADER_SIZE])
    version, expected_container_size, payload_a_len, payload_b_len = header_words
    if version != 3:
        raise ValueError(f"Unsupported STO version {version} in {source_path}")

    warnings: list[str] = []
    if expected_container_size != payload_a_len + payload_b_len + _ENVELOPE_SIZE:
        warnings.append(
            "Header word 1 does not match payload_a + payload_b + envelope; preserving raw bytes anyway."
        )

    envelope_start = _HEADER_SIZE
    envelope_end = envelope_start + _ENVELOPE_SIZE
    payload_start = envelope_end
    payload_a_end = min(len(data), payload_start + payload_a_len)
    payload_b_end = min(len(data), payload_a_end + payload_b_len)

    if payload_a_end - payload_start != payload_a_len:
        warnings.append("File ended before the full payload_a segment was available.")
    if payload_b_end - payload_a_end != payload_b_len:
        warnings.append("File ended before the full payload_b segment was available.")

    envelope_bytes = data[envelope_start:envelope_end]
    payload_a = data[payload_start:payload_a_end]
    payload_b = data[payload_a_end:payload_b_end]
    payload = payload_a + payload_b
    trailer_bytes = data[payload_b_end:]

    text_hits = _scan_utf16le_ascii_runs(payload)
    notes_text = " ".join(text for _, _, text in text_hits).strip()
    setup_blob_end = text_hits[0][0] if text_hits else len(payload)
    setup_blob = payload[:setup_blob_end]

    raw_entries: list[RawStoEntry] = [
        RawStoEntry(
            name="envelope",
            kind="envelope",
            offset=envelope_start,
            length=len(envelope_bytes),
            preview_hex=_hex_preview(envelope_bytes),
        ),
        RawStoEntry(
            name="payload_a",
            kind="payload_segment",
            offset=payload_start,
            length=len(payload_a),
            preview_hex=_hex_preview(payload_a),
        ),
        RawStoEntry(
            name="payload_b",
            kind="payload_segment",
            offset=payload_a_end,
            length=len(payload_b),
            preview_hex=_hex_preview(payload_b),
        ),
        RawStoEntry(
            name="setup_blob",
            kind="opaque_setup_blob",
            offset=payload_start,
            length=len(setup_blob),
            preview_hex=_hex_preview(setup_blob),
        ),
    ]
    for index, (local_offset, hit_length, text) in enumerate(text_hits):
        raw_entries.append(
            RawStoEntry(
                name=f"utf16_note_{index}",
                kind="utf16_text",
                offset=payload_start + local_offset,
                length=hit_length,
                text=text,
                preview_hex=_hex_preview(payload[local_offset:local_offset + hit_length]),
            )
        )
    if trailer_bytes:
        raw_entries.append(
            RawStoEntry(
                name="trailer",
                kind="trailer",
                offset=payload_b_end,
                length=len(trailer_bytes),
                preview_hex=_hex_preview(trailer_bytes),
            )
        )

    sha256 = hashlib.sha256(data).hexdigest().upper()
    car_id = _infer_car_id(source_path.stem, notes_text)
    provider_name = _infer_provider_name(source_path.stem, notes_text)
    if not car_id:
        warnings.append("Car id could not be inferred from filename or note text.")
    if not provider_name:
        warnings.append("Provider name could not be inferred from filename or note text.")

    return DecodedSto(
        source_path=source_path,
        version=version,
        header_words=header_words,
        sha256=sha256,
        car_id=car_id,
        provider_name=provider_name,
        notes_text=notes_text,
        raw_entries=raw_entries,
        warnings=warnings,
        envelope_bytes=envelope_bytes,
        payload_a=payload_a,
        payload_b=payload_b,
        payload=payload,
        setup_blob=setup_blob,
        trailer_bytes=trailer_bytes,
    )
