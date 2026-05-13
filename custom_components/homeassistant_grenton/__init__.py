import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

from .integration_config import GrentonConfigEntry, GrentonConfigEntryData, RuntimeData
from .coordinator import GrentonCoordinator
from .mappers.device_from_om import DeviceFromOmMapper

from .domain.encryption import GrentonEncryption
from .domain.clu import GrentonClu

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.LIGHT,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.COVER,
    Platform.CAMERA,
]


async def async_setup_entry(hass: HomeAssistant, config_entry: GrentonConfigEntry) -> bool:
    _LOGGER.debug("Initializing Home Assistant Grenton integration")

    config_data: GrentonConfigEntryData = config_entry.data  # type: ignore

    encryption = GrentonEncryption(key=config_data["key"], iv=config_data["iv"])
    clus = [
        GrentonClu(
            id=c["id"],
            serial_number=c["serial_number"],
            name=c["name"],
            ip=c["ip"],
            port=c["port"],
        )
        for c in config_data["clus"]
    ]

    coordinator = GrentonCoordinator(hass, config_entry, clus, encryption)

    _LOGGER.debug("Loaded %d CLU(s)", len(clus))
    for clu in clus:
        _LOGGER.debug("  CLU %s (%s) at %s:%d", clu.name, clu.serial_number, clu.ip, clu.port)

    devices = DeviceFromOmMapper.from_objects(config_data["objects"], coordinator)

    _LOGGER.debug("Mapped %d device(s) from om.lua inventory", len(devices))
    for device in devices:
        _LOGGER.debug(
            "  Device %s (%s) with %d entity(ies)",
            device.type, device.id, len(device.entities),
        )

    config_entry.runtime_data = RuntimeData(coordinator=coordinator, devices=devices)

    await coordinator.async_setup()
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    coordinator: GrentonCoordinator = config_entry.runtime_data.coordinator

    await coordinator.async_shutdown()
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
