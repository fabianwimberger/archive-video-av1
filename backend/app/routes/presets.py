"""Preset management API endpoints."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.preset import Preset
from app.models.app_state import AppState
from app.models.schemas import (
    PresetCreate,
    PresetUpdate,
    PresetResponse,
    PresetImportResponse,
)
from app.utils.validation import validate_conversion_settings, validate_preset_name

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_default_preset_id(db: AsyncSession) -> int:
    result = await db.execute(
        select(AppState).where(AppState.key == "default_preset_id")
    )
    row = result.scalar_one_or_none()
    return int(row.value) if row else 1


async def _set_default_preset_id(db: AsyncSession, preset_id: int) -> None:
    row = await db.get(AppState, "default_preset_id")
    if row:
        row.value = str(preset_id)  # type: ignore[assignment]
    else:
        db.add(AppState(key="default_preset_id", value=str(preset_id)))
    await db.commit()


async def _check_name_collision(
    db: AsyncSession, name: str, exclude_id: Optional[int] = None
) -> bool:
    query = select(Preset).where(func.lower(Preset.name) == func.lower(name))
    if exclude_id is not None:
        query = query.where(Preset.id != exclude_id)
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


def _preset_to_response(preset: Preset, default_id: int) -> PresetResponse:
    data = {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "is_builtin": preset.is_builtin,
        "crf": preset.crf,
        "encoder_preset": preset.encoder_preset,
        "svt_params": preset.svt_params,
        "audio_bitrate": preset.audio_bitrate,
        "skip_crop_detect": preset.skip_crop_detect,
        "max_resolution": preset.max_resolution,
        "created_at": preset.created_at,
        "updated_at": preset.updated_at,
        "is_default": preset.id == default_id,
    }
    return PresetResponse(**data)  # type: ignore


@router.get("", response_model=list[PresetResponse])
async def list_presets(db: AsyncSession = Depends(get_db)):
    """List all presets."""
    default_id = await _get_default_preset_id(db)
    result = await db.execute(select(Preset).order_by(Preset.name))
    presets = result.scalars().all()
    return [_preset_to_response(p, default_id) for p in presets]


@router.post("", response_model=PresetResponse, status_code=201)
async def create_preset(data: PresetCreate, db: AsyncSession = Depends(get_db)):
    """Create a new preset."""
    if await _check_name_collision(db, data.name):
        raise HTTPException(status_code=409, detail="Preset name already exists")

    settings_dict = data.model_dump()
    validate_preset_name(settings_dict["name"])
    validate_conversion_settings(settings_dict)

    preset = Preset(**settings_dict)
    db.add(preset)
    await db.commit()
    await db.refresh(preset)

    default_id = await _get_default_preset_id(db)
    return _preset_to_response(preset, default_id)


@router.patch("/{preset_id}", response_model=PresetResponse)
async def update_preset(
    preset_id: int, data: PresetUpdate, db: AsyncSession = Depends(get_db)
):
    """Update a preset."""
    preset = await db.get(Preset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if preset.is_builtin:
        raise HTTPException(status_code=409, detail="Cannot modify built-in preset")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "name" in updates:
        validate_preset_name(updates["name"])
        if await _check_name_collision(db, updates["name"], exclude_id=preset_id):
            raise HTTPException(status_code=409, detail="Preset name already exists")

    # Merge current settings with updates for validation
    current = {
        "crf": preset.crf,
        "encoder_preset": preset.encoder_preset,
        "svt_params": preset.svt_params,
        "audio_bitrate": preset.audio_bitrate,
        "skip_crop_detect": preset.skip_crop_detect,
        "max_resolution": preset.max_resolution,
    }
    current.update(updates)
    validate_conversion_settings(current)

    for key, value in updates.items():
        setattr(preset, key, value)
    preset.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

    await db.commit()
    await db.refresh(preset)

    default_id = await _get_default_preset_id(db)
    return _preset_to_response(preset, default_id)


@router.delete("/{preset_id}", status_code=204)
async def delete_preset(preset_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a preset."""
    preset = await db.get(Preset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if preset.is_builtin:
        raise HTTPException(status_code=409, detail="Cannot delete built-in preset")

    default_id = await _get_default_preset_id(db)
    if default_id == preset_id:
        # Reset to Default built-in
        default_result = await db.execute(
            select(Preset).where(Preset.name == "Default")
        )
        default_preset = default_result.scalar_one_or_none()
        new_default_id = default_preset.id if default_preset else 1
        await _set_default_preset_id(db, new_default_id)  # type: ignore

    await db.delete(preset)
    await db.commit()


@router.post("/{preset_id}/duplicate", response_model=PresetResponse)
async def duplicate_preset(preset_id: int, db: AsyncSession = Depends(get_db)):
    """Duplicate a preset."""
    preset = await db.get(Preset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    base_name = f"{preset.name} (copy)"
    name = base_name
    counter = 2
    while await _check_name_collision(db, name):
        name = f"{preset.name} (copy {counter})"
        counter += 1

    new_preset = Preset(
        name=name,
        description=preset.description,
        is_builtin=False,
        crf=preset.crf,
        encoder_preset=preset.encoder_preset,
        svt_params=preset.svt_params,
        audio_bitrate=preset.audio_bitrate,
        skip_crop_detect=preset.skip_crop_detect,
        max_resolution=preset.max_resolution,
    )
    db.add(new_preset)
    await db.commit()
    await db.refresh(new_preset)

    default_id = await _get_default_preset_id(db)
    return _preset_to_response(new_preset, default_id)


@router.post("/{preset_id}/set-default", response_model=PresetResponse)
async def set_default_preset(preset_id: int, db: AsyncSession = Depends(get_db)):
    """Set a preset as the default."""
    preset = await db.get(Preset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    await _set_default_preset_id(db, preset_id)
    return _preset_to_response(preset, preset_id)


@router.get("/export")
async def export_all_presets(db: AsyncSession = Depends(get_db)):
    """Export all user presets as JSON."""
    result = await db.execute(
        select(Preset).where(Preset.is_builtin.is_(False)).order_by(Preset.name)
    )
    presets = result.scalars().all()

    doc = {
        "format": "archive-video-av1.presets",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "presets": [
            {
                "name": p.name,
                "description": p.description,
                "crf": p.crf,
                "encoder_preset": p.encoder_preset,
                "svt_params": p.svt_params,
                "audio_bitrate": p.audio_bitrate,
                "skip_crop_detect": p.skip_crop_detect,
                "max_resolution": p.max_resolution,
            }
            for p in presets
        ],
    }

    return doc


@router.get("/{preset_id}/export")
async def export_preset(preset_id: int, db: AsyncSession = Depends(get_db)):
    """Export a single preset as JSON."""
    preset = await db.get(Preset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    doc = {
        "format": "archive-video-av1.presets",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "presets": [
            {
                "name": preset.name,
                "description": preset.description,
                "crf": preset.crf,
                "encoder_preset": preset.encoder_preset,
                "svt_params": preset.svt_params,
                "audio_bitrate": preset.audio_bitrate,
                "skip_crop_detect": preset.skip_crop_detect,
                "max_resolution": preset.max_resolution,
            }
        ],
    }

    return doc


@router.post("/import", response_model=PresetImportResponse)
async def import_presets(
    on_conflict: str = Query(..., pattern="^(skip|rename|overwrite)$"),
    file: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Import presets from uploaded JSON file."""

    if file is None:
        # Allow calling without the typed default by using File() marker
        raise HTTPException(status_code=400, detail="Missing uploaded file")

    try:
        content = await file.read()
        doc = json.loads(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if doc.get("format") != "archive-video-av1.presets":
        raise HTTPException(status_code=400, detail="Invalid format identifier")
    if doc.get("version") != 1:
        raise HTTPException(status_code=400, detail="Unsupported version")

    imported = []
    skipped = []
    renamed = []
    errors = []

    for entry in doc.get("presets", []):
        name = entry.get("name", "")
        try:
            validate_preset_name(name)
            validate_conversion_settings(entry)
        except ValueError as e:
            errors.append({"entry": name or "(unnamed)", "reason": str(e)})
            continue

        collision = await _check_name_collision(db, name)
        target_name = name

        if collision:
            if on_conflict == "skip":
                skipped.append(name)
                continue
            elif on_conflict == "rename":
                base = f"{name} (imported)"
                target_name = base
                counter = 2
                while await _check_name_collision(db, target_name):
                    target_name = f"{name} (imported {counter})"
                    counter += 1
                renamed.append({"from": name, "to": target_name})
            elif on_conflict == "overwrite":
                # Can only overwrite user presets
                existing_result = await db.execute(
                    select(Preset).where(func.lower(Preset.name) == func.lower(name))
                )
                existing = existing_result.scalar_one_or_none()
                if existing and existing.is_builtin:
                    errors.append(
                        {"entry": name, "reason": "Cannot overwrite built-in preset"}
                    )
                    continue
                if existing:
                    existing.description = entry.get("description")
                    existing.crf = entry["crf"]
                    existing.encoder_preset = entry["encoder_preset"]
                    existing.svt_params = entry.get("svt_params", "")
                    existing.audio_bitrate = entry["audio_bitrate"]
                    existing.skip_crop_detect = entry.get("skip_crop_detect", False)
                    existing.max_resolution = entry["max_resolution"]
                    existing.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                    imported.append(target_name)
                    continue

        preset = Preset(
            name=target_name,
            description=entry.get("description"),
            is_builtin=False,
            crf=entry["crf"],
            encoder_preset=entry["encoder_preset"],
            svt_params=entry.get("svt_params", ""),
            audio_bitrate=entry["audio_bitrate"],
            skip_crop_detect=entry.get("skip_crop_detect", False),
            max_resolution=entry["max_resolution"],
        )
        db.add(preset)
        imported.append(target_name)

    await db.commit()

    return {
        "imported": imported,
        "skipped": skipped,
        "renamed": renamed,
        "errors": errors,
    }
