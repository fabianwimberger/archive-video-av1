"""Effective application settings resolved from env, storage and defaults."""

import logging
import os
import socket
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.app_state import AppState
from app.utils.validation import (
    normalize_file_mode,
    normalize_language_list,
    normalize_url,
    normalize_url_list,
)

logger = logging.getLogger(__name__)

KEY_PREFIX = "setting."

WRAPPER_SETTINGS = (
    "AUDIO_TRACK_MODE",
    "SUBTITLE_TRACK_MODE",
    "PREFERRED_AUDIO_LANGUAGES",
    "PREFERRED_SUBTITLE_LANGUAGES",
    "OUTPUT_FILE_MODE",
    "PUID",
    "PGID",
)

RESTART_SETTINGS = (
    "DISTRIBUTED_ENABLED",
    "DISTRIBUTED_NODE_NAME",
    "DISTRIBUTED_PUBLIC_URL",
    "DISTRIBUTED_LEADER_URL",
    "DISTRIBUTED_PEERS",
)


@dataclass(frozen=True)
class SettingDefinition:
    name: str
    default: str
    kind: str
    choices: tuple[str, ...] = ()
    restart_required: bool = False
    read_only: bool = False


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    definition.name: definition
    for definition in (
        SettingDefinition(
            "AUDIO_TRACK_MODE", "preferred", "enum", ("preferred", "all")
        ),
        SettingDefinition(
            "SUBTITLE_TRACK_MODE", "preferred", "enum", ("preferred", "all", "none")
        ),
        SettingDefinition(
            "PREFERRED_AUDIO_LANGUAGES", "ger,deu,de,eng,en", "languages"
        ),
        SettingDefinition(
            "PREFERRED_SUBTITLE_LANGUAGES", "ger,deu,de,eng,en", "languages"
        ),
        SettingDefinition("OUTPUT_FILE_MODE", "0644", "file_mode"),
        SettingDefinition("PUID", "", "id"),
        SettingDefinition("PGID", "", "id"),
        SettingDefinition(
            "LOG_LEVEL", "INFO", "enum", ("DEBUG", "INFO", "WARNING", "ERROR")
        ),
        SettingDefinition("JOB_HISTORY_RETENTION_DAYS", "0", "int"),
        SettingDefinition("JOB_HISTORY_MAX_ROWS", "0", "int"),
        SettingDefinition(
            "DISTRIBUTED_ENABLED", "false", "bool", restart_required=True
        ),
        SettingDefinition("DISTRIBUTED_NODE_NAME", "", "text", restart_required=True),
        SettingDefinition("DISTRIBUTED_PUBLIC_URL", "", "url", restart_required=True),
        SettingDefinition("DISTRIBUTED_LEADER_URL", "", "url", restart_required=True),
        SettingDefinition("DISTRIBUTED_PEERS", "", "url_list", restart_required=True),
        SettingDefinition("DISTRIBUTED_NODE_ID", "", "text", read_only=True),
    )
}


class SettingsLockedError(Exception):
    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(f"Set by the environment: {', '.join(names)}")


class SettingsService:
    async def _load_stored(self, session) -> dict[str, str]:
        result = await session.execute(
            select(AppState).where(AppState.key.like(f"{KEY_PREFIX}%"))
        )
        return {row.key[len(KEY_PREFIX) :]: row.value for row in result.scalars()}

    async def ensure_node_id(self) -> str:
        env_id = os.environ.get("DISTRIBUTED_NODE_ID", "").strip()
        if env_id:
            settings.DISTRIBUTED_NODE_ID = env_id
            return env_id

        async with AsyncSessionLocal() as session:
            key = f"{KEY_PREFIX}DISTRIBUTED_NODE_ID"
            row = await session.get(AppState, key)
            if row is None:
                # The hostname was the default before IDs were stored, so
                # existing nodes keep their identity across the upgrade.
                node_id = socket.gethostname()
                session.add(AppState(key=key, value=node_id))
                await session.commit()
            else:
                node_id = row.value

        settings.DISTRIBUTED_NODE_ID = node_id
        return node_id

    def _resolve_raw(
        self,
        definition: SettingDefinition,
        stored: dict[str, str],
        node_id: str,
    ) -> tuple[str, str]:
        if definition.name in os.environ:
            return os.environ[definition.name], "env"
        if definition.name in stored:
            return stored[definition.name], "stored"
        if definition.name in ("DISTRIBUTED_NODE_ID", "DISTRIBUTED_NODE_NAME"):
            return node_id, "default"
        return definition.default, "default"

    def _wire_value(self, definition: SettingDefinition, raw: str) -> Any:
        if definition.kind == "bool":
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        if definition.kind == "int":
            try:
                return int(raw)
            except ValueError:
                return 0
        if definition.kind == "id":
            return int(raw) if raw else ""
        if definition.kind == "url_list":
            return ",".join(
                part.strip().rstrip("/") for part in raw.split(",") if part.strip()
            )
        return raw

    def _active_value(self, name: str) -> Any:
        if name == "DISTRIBUTED_ENABLED":
            return bool(settings.DISTRIBUTED_ENABLED)
        if name == "DISTRIBUTED_NODE_NAME":
            return settings.DISTRIBUTED_NODE_NAME
        if name == "DISTRIBUTED_PUBLIC_URL":
            return settings.DISTRIBUTED_PUBLIC_URL
        if name == "DISTRIBUTED_LEADER_URL":
            return settings.DISTRIBUTED_LEADER_URL
        if name == "DISTRIBUTED_PEERS":
            return ",".join(settings.DISTRIBUTED_PEERS)
        return None

    def _restart_required(
        self, definition: SettingDefinition, raw: str, source: str
    ) -> bool:
        if not definition.restart_required or source == "env":
            return False
        return self._wire_value(definition, raw) != self._active_value(definition.name)

    async def get_all(self) -> dict[str, dict[str, Any]]:
        node_id = await self.ensure_node_id()
        async with AsyncSessionLocal() as session:
            stored = await self._load_stored(session)

        result = {}
        for definition in SETTING_DEFINITIONS.values():
            raw, source = self._resolve_raw(definition, stored, node_id)
            result[definition.name] = {
                "value": self._wire_value(definition, raw),
                "source": source,
                "restart_required": self._restart_required(definition, raw, source),
            }
        return result

    def _normalize(self, name: str, value: Any) -> str:
        definition = SETTING_DEFINITIONS[name]
        if definition.read_only:
            raise ValueError(f"{name} is read-only")

        if definition.kind == "id":
            if value is None or value == "":
                return ""
            number = int(value)
            if not 0 <= number <= 65535:
                raise ValueError(f"{name} must be between 0 and 65535")
            return str(number)

        if value is None:
            raise ValueError(f"{name} requires a value")

        if definition.kind == "bool":
            if isinstance(value, bool):
                return "true" if value else "false"
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return "true"
            if text in {"0", "false", "no", "off"}:
                return "false"
            raise ValueError(f"{name} must be true or false")

        text = str(value).strip()

        if definition.kind == "enum":
            if text not in definition.choices:
                raise ValueError(
                    f"{name} must be one of {', '.join(definition.choices)}"
                )
            return text

        if definition.kind == "int":
            number = int(text)
            if number < 0:
                raise ValueError(f"{name} must be 0 or greater")
            return str(number)

        if definition.kind == "languages":
            return normalize_language_list(text)

        if definition.kind == "file_mode":
            return normalize_file_mode(text)

        if definition.kind == "url":
            return normalize_url(text, allow_empty=True)

        if definition.kind == "url_list":
            return normalize_url_list(text)

        if not text:
            raise ValueError(f"{name} requires a value")
        return text

    async def update(self, changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
        unknown = [name for name in changes if name not in SETTING_DEFINITIONS]
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(unknown)}")

        locked = [name for name in changes if name in os.environ]
        if locked:
            raise SettingsLockedError(locked)

        normalized = {
            name: self._normalize(name, value) for name, value in changes.items()
        }

        async with AsyncSessionLocal() as session:
            for name, value in normalized.items():
                key = f"{KEY_PREFIX}{name}"
                row = await session.get(AppState, key)
                if row is None:
                    session.add(AppState(key=key, value=value))
                else:
                    row.value = value
            await session.commit()

        if "LOG_LEVEL" in normalized:
            self._apply_log_level(normalized["LOG_LEVEL"])
        return await self.get_all()

    def _apply_log_level(self, level_name: str) -> None:
        settings.LOG_LEVEL = level_name
        from app.main import configure_logging

        configure_logging(level_name)

    async def apply_startup(self) -> None:
        values = await self.get_all()
        settings.LOG_LEVEL = values["LOG_LEVEL"]["value"]
        settings.DISTRIBUTED_ENABLED = values["DISTRIBUTED_ENABLED"]["value"]
        settings.DISTRIBUTED_NODE_ID = values["DISTRIBUTED_NODE_ID"]["value"]
        settings.DISTRIBUTED_NODE_NAME = values["DISTRIBUTED_NODE_NAME"]["value"]
        settings.DISTRIBUTED_PUBLIC_URL = values["DISTRIBUTED_PUBLIC_URL"]["value"]
        settings.DISTRIBUTED_LEADER_URL = values["DISTRIBUTED_LEADER_URL"]["value"]
        settings.DISTRIBUTED_PEERS = [
            peer for peer in values["DISTRIBUTED_PEERS"]["value"].split(",") if peer
        ]

        from app.main import configure_logging

        configure_logging()

    async def wrapper_environment(self) -> dict[str, str]:
        values = await self.get_all()
        return {
            name: "" if values[name]["value"] == "" else str(values[name]["value"])
            for name in WRAPPER_SETTINGS
        }


settings_service = SettingsService()
