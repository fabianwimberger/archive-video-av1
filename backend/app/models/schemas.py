"""Pydantic schemas for API requests and responses."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ConversionSettings(BaseModel):
    """Settings for video conversion."""
    crf: int = Field(default=26, ge=0, le=51, description="Constant Rate Factor (0-51)")
    preset: int = Field(default=4, ge=0, le=13, description="Encoding preset (0=slowest, 13=fastest)")
    svt_params: str = Field(default="tune=0:film-grain=8", description="SVT-AV1 parameters")
    audio_bitrate: str = Field(default="96k", description="Audio bitrate")
    skip_crop_detect: bool = Field(default=False, description="Skip automatic crop detection")


class JobCreate(BaseModel):
    """Schema for creating a single job."""
    source_file: str
    mode: str = Field(default="default", pattern="^(default|animated|grainy)$")
    settings: Optional[ConversionSettings] = None


class JobBatchCreate(BaseModel):
    """Schema for creating multiple jobs."""
    files: list[str]
    mode: str = Field(default="default", pattern="^(default|animated|grainy)$")
    settings: Optional[ConversionSettings] = None


class JobResponse(BaseModel):
    """Schema for job response."""
    id: int
    source_file: str
    output_file: str
    mode: str
    settings: str
    status: str
    progress_percent: float
    eta_seconds: Optional[int]
    current_fps: Optional[float]
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    log: str

    model_config = ConfigDict(from_attributes=True)


class JobListResponse(BaseModel):
    """Schema for job list response."""
    jobs: list[JobResponse]
    total: int


class JobCreateResponse(BaseModel):
    """Schema for job creation response."""
    job_ids: list[int]
