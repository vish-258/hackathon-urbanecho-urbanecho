from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Threshold(Input):
    threshold_value: float = Field(strict=True, ge=-200, le=200)
    threshold_type: Literal["dbfs_rms", "spl_z_leq"]
    interval_seconds: int = Field(strict=True, ge=1, le=60)
    weighting: Literal["none", "Z"] | None = None
    channel_policy: Literal["mono"] = "mono"
    recovery_count: int = Field(default=3, strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def compatible_method(self):
        expected = "none" if self.threshold_type == "dbfs_rms" else "Z"
        if self.weighting is None:
            self.weighting = expected
        if self.weighting != expected:
            raise ValueError(f"{self.threshold_type} requires weighting={expected}")
        minimum, maximum = (-200, 0) if self.threshold_type == "dbfs_rms" else (-100, 200)
        if not minimum <= self.threshold_value <= maximum:
            raise ValueError(f"{self.threshold_type} supports values from {minimum} to {maximum}")
        return self


class ThresholdUpdate(Threshold):
    expected_revision: int = Field(strict=True, ge=0)
    effective_at: AwareDatetime | None = None


class LocationCreate(Threshold):
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(strict=True, ge=-90, le=90)
    longitude: float = Field(strict=True, ge=-180, le=180)
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone, e.g. Asia/Kolkata")
        return value


class LocationUpdate(Input):
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    latitude: float | None = Field(default=None, strict=True, ge=-90, le=90)
    longitude: float | None = Field(default=None, strict=True, ge=-180, le=180)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        return LocationCreate.valid_timezone(value) if value is not None else value

    @model_validator(mode="after")
    def valid_update(self):
        changed = self.model_fields_set - {"expected_version"}
        if not changed:
            raise ValueError("At least one location field is required")
        for key in changed:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        if self.name is not None and not self.name.strip():
            raise ValueError("name must contain non-whitespace characters")
        return self


class Calibration(Input):
    method: Literal["spl_z_leq"]
    weighting: Literal["Z"] = "Z"
    channel_policy: Literal["mono"] = "mono"
    status: Literal["valid", "failed"] = "valid"
    version: str = Field(min_length=1, max_length=100)
    offset_db: float = Field(strict=True, ge=-200, le=200)
    sample_rate: int = Field(strict=True, ge=8000, le=192000)
    pcm_bits: Literal[16, 24] | None = None
    calibrated_at: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def valid_period(self):
        if self.valid_until <= self.calibrated_at:
            raise ValueError("valid_until must follow calibrated_at")
        return self


class DeviceCreate(Input):
    id: UUID | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    location_id: UUID
    microphone_model: str = Field(default="INMP441", min_length=1, max_length=100)
    calibration: Calibration | None = None

    @field_validator("external_id")
    @classmethod
    def external_identity_not_uuid(cls, value):
        # Compact UUIDs remain an unambiguous existing transport identity.
        if value is not None and len(value) == 32 and all(char in "0123456789abcdefABCDEF" for char in value):
            raise ValueError("32 hexadecimal digits are reserved for registered UUID identities")
        return value


class DeviceUpdate(Input):
    expected_revision: int = Field(strict=True, ge=1)
    location_id: UUID | None = None
    enabled: bool | None = Field(default=None, strict=True)
    calibration: Calibration | None = None

    @model_validator(mode="after")
    def valid_update(self):
        if not self.model_fields_set - {"expected_revision"}:
            raise ValueError("At least one configuration field is required")
        for key in ("location_id", "enabled"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class UploadMetadata(Input):
    device_id: UUID
    chunk_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    captured_at: AwareDatetime
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    sequence: int = Field(strict=True, ge=0, le=9223372036854775807)
    capture_interval_ms: int | None = Field(default=None, strict=True, ge=1, le=3600000)
