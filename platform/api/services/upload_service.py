"""File upload handling and IBT metadata extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status

from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car
from track_model.ibt_parser import IBTFile


CAR_NAME_MAP = {
    "bmw": "bmw",
    "bmw m hybrid v8": "bmw",
    "cadillac": "cadillac",
    "cadillac v-series.r": "cadillac",
    "ferrari": "ferrari",
    "ferrari 499p": "ferrari",
    "porsche": "porsche",
    "porsche 963": "porsche",
    "acura": "acura",
    "acura arx-06": "acura",
}


@dataclass
class UploadMetadata:
    car: str
    track: str
    track_config: str
    wing: float | None
    lap: int | None
    driver_name: str | None


async def stream_to_disk(upload: UploadFile, destination: Path, max_upload_mb: int) -> int:
    """Stream an UploadFile into destination with size guard."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    size_limit = max_upload_mb * 1024 * 1024
    total = 0
    with destination.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > size_limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {max_upload_mb}MB limit",
                )
            out.write(chunk)
    await upload.close()
    return total


def validate_ibt(path: Path) -> IBTFile:
    """Parse IBT file and raise a 400 on failure."""
    try:
        return IBTFile(path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid IBT file: {exc}",
        ) from exc


def normalize_car(car_hint: str | None) -> str | None:
    if not car_hint:
        return None
    key = car_hint.strip().lower()
    mapped = CAR_NAME_MAP.get(key, key)
    try:
        get_car(mapped)
        return mapped
    except Exception:
        return None


def detect_metadata(
    ibt: IBTFile,
    *,
    provided_car: str | None,
    provided_wing: float | None,
    provided_lap: int | None,
) -> UploadMetadata:
    """Derive car/track/wing metadata, honoring explicit request overrides."""
    track_info = ibt.track_info()
    car_info = ibt.car_info()

    setup = CurrentSetup.from_ibt(ibt)
    auto_wing = setup.wing_angle_deg if setup.wing_angle_deg else None

    auto_car = normalize_car(car_info.get("car")) if car_info else None
    car = normalize_car(provided_car) or auto_car
    if not car:
        raise HTTPException(status_code=400, detail="Unable to determine supported car from upload")
    try:
        get_car(car)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UploadMetadata(
        car=car,
        track=track_info.get("track_name", "Unknown Track"),
        track_config=track_info.get("track_config", ""),
        wing=provided_wing if provided_wing is not None else auto_wing,
        lap=provided_lap,
        driver_name=car_info.get("driver") if car_info else None,
    )


def read_optional_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

