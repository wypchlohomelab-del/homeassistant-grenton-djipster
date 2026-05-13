"""Configuration types for Grenton integration."""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import GrentonCoordinator
    from .domain.devices.base import BaseGrentonDevice


class GrentonConfigEntryData(TypedDict):
    """Type definition for config entry data."""
    key: str                               # base64 AES key from .omp
    iv: str                                # base64 AES iv from .omp
    clus: list[dict[str, Any]]             # [{id, serial_number, name, ip, port}]
    objects: dict[str, dict[str, Any]]     # clu_id -> {lua_name -> {project_name, kind, type_id}}


@dataclass
class RuntimeData:
    """Runtime data stored in config entry."""
    coordinator: "GrentonCoordinator"
    devices: list["BaseGrentonDevice"]


type GrentonConfigEntry = ConfigEntry[RuntimeData]
