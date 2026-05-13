from abc import ABC
from dataclasses import dataclass, field

from homeassistant.helpers.device_registry import DeviceInfo

from ..entities.base import BaseGrentonEntity

_MODEL_DISPLAY_NAMES: dict[str, str] = {
    "DIMMER_V2": "Dimmer V2",
    "LED": "LED",
    "ROLLER_SHUTTER": "Roller Shutter",
    "ROLLER_SHUTTER_V3": "Roller Shutter V3",
    "ON_OFF": "On/Off Switch",
    "ON_OFF_DOUBLE": "On/Off Double Switch",
    "SLIDER": "Slider",
    "CAMERA": "Camera",
    "CONTACT_SENSOR": "Contact Sensor",
    "CONTACT_SENSOR_DOUBLE": "Contact Sensor Double",
    "MULTISENSOR": "Multisensor",
    "VALUE_V2": "Value",
    "VALUE_DOUBLE": "Value Double",
}


@dataclass
class BaseGrentonDevice(ABC):
    type: str
    id: str
    entities: list[BaseGrentonEntity]
    name: str | None = field(default=None)

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant device info."""
        return DeviceInfo(
            identifiers={("grenton", self.id)},
            name=self.name,
            manufacturer="Grenton",
            model=_MODEL_DISPLAY_NAMES.get(self.type, self.type),
        )