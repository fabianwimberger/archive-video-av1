"""Pydantic schemas for API requests and responses."""

import json
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversionSettings(BaseModel):
    """Settings for video conversion."""

    crf: int = Field(default=26, ge=0, le=51, description="Constant Rate Factor (0-51)")
    encoder_preset: int = Field(
        default=4, ge=0, le=13, description="Encoding preset (0=slowest, 13=fastest)"
    )
    svt_params: Optional[str] = Field(
        default="tune=0:film-grain=8",
        description="SVT-AV1 parameters (optional, empty for no extra params)",
    )
    audio_bitrate: str = Field(default="96k", description="Audio bitrate")
    skip_crop_detect: bool = Field(
        default=False, description="Skip automatic crop detection"
    )
    max_resolution: int = Field(
        default=1080, description="Maximum output height (720, 1080, 2160)"
    )


class PresetBase(BaseModel):
    """Base preset schema."""

    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = None
    crf: int = Field(..., ge=0, le=51)
    encoder_preset: int = Field(..., ge=0, le=13)
    svt_params: Optional[str] = ""
    audio_bitrate: str = Field(..., pattern=r"^\d+[kK]$")
    skip_crop_detect: bool = False
    max_resolution: int = Field(...)


class PresetCreate(PresetBase):
    """Schema for creating a preset."""

    pass


class PresetUpdate(BaseModel):
    """Schema for updating a preset."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = None
    crf: Optional[int] = Field(default=None, ge=0, le=51)
    encoder_preset: Optional[int] = Field(default=None, ge=0, le=13)
    svt_params: Optional[str] = None
    audio_bitrate: Optional[str] = Field(default=None, pattern=r"^\d+[kK]$")
    skip_crop_detect: Optional[bool] = None
    max_resolution: Optional[int] = None


class PresetResponse(BaseModel):
    """Schema for preset response."""

    id: int
    name: str
    description: Optional[str] = None
    is_builtin: bool
    crf: int
    encoder_preset: int
    svt_params: Optional[str] = None
    audio_bitrate: str
    skip_crop_detect: bool
    max_resolution: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_default: bool = False

    model_config = ConfigDict(from_attributes=True)


class JobCreate(BaseModel):
    """Schema for creating a single job."""

    source_file: str
    preset_id: Optional[int] = None
    settings: Optional[ConversionSettings] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def check_preset_or_settings(self):
        if self.preset_id is None and self.settings is None:
            raise ValueError("At least one of preset_id or settings is required")
        return self


class JobBatchCreate(BaseModel):
    """Schema for creating multiple jobs."""

    files: list[str]
    preset_id: Optional[int] = None
    settings: Optional[ConversionSettings] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def check_preset_or_settings(self):
        if self.preset_id is None and self.settings is None:
            raise ValueError("At least one of preset_id or settings is required")
        return self


class JobResponse(BaseModel):
    """Schema for job response."""

    id: int
    source_file: str
    output_file: str
    preset_id: Optional[int] = None
    preset_name_snapshot: Optional[str] = None
    settings: dict
    notes: Optional[str] = None
    queue_position: Optional[int] = None
    status: str
    progress_percent: float
    eta_seconds: Optional[int] = None
    current_fps: Optional[float] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    log: str
    source_size_bytes: Optional[int] = None
    output_size_bytes: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def model_validate(cls, obj, **kwargs):
        # Convert JSON settings string to dict
        data = {}
        for key in dir(obj):
            if not key.startswith("_"):
                try:
                    data[key] = getattr(obj, key)
                except Exception:
                    pass
        if isinstance(data.get("settings"), str):
            try:
                data["settings"] = (
                    json.loads(data["settings"]) if data["settings"] else {}
                )
            except json.JSONDecodeError:
                data["settings"] = {}
        return cls(**data)


class JobListResponse(BaseModel):
    """Schema for job list response."""

    jobs: list[JobResponse]
    total: int


class JobCreateResponse(BaseModel):
    """Schema for job creation response."""

    job_ids: list[int]


class JobPatchRequest(BaseModel):
    """Schema for patching a job."""

    notes: Optional[str] = None


class JobPositionPatchRequest(BaseModel):
    """Schema for patching job position."""

    absolute: int = Field(..., ge=1, description="Target queue position (1-indexed)")


class QueueStatusResponse(BaseModel):
    """Schema for queue status."""

    paused: bool
    active_job_id: Optional[int] = None
    pending_count: int


class PresetExportDocument(BaseModel):
    """Schema for preset export document."""

    format: str
    version: int
    exported_at: str
    presets: list[dict]


class PresetImportResponse(BaseModel):
    """Schema for preset import response."""

    imported: list[str]
    skipped: list[str]
    renamed: list[dict]
    errors: list[dict]
