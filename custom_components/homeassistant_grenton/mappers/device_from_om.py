"""Map OmObject entries (from om.lua) to Home Assistant domain devices.

Attribute names and execute-method indices are sourced from the OM
device-interfaces XML files (authoritative) and cross-checked against the
test_app/features.py reference table.
"""
from __future__ import annotations

from typing import Any

from ..coordinator import GrentonCoordinator
from ..domain.api.om_lua_parser import OmObjectKind
from ..domain.devices.base import BaseGrentonDevice
from ..domain.devices.contact_sensor import GrentonDeviceContactSensor
from ..domain.devices.dimmer_v2 import GrentonDeviceDimmerV2
from ..domain.devices.led import GrentonDeviceLed
from ..domain.devices.on_off import GrentonDeviceOnOff
from ..domain.devices.roller_shutter_v3 import GrentonDeviceRollerShutterV3
from ..domain.devices.value_v2 import GrentonDeviceValueV2
from ..domain.entities.binary_sensor import GrentonEntityBinarySensor
from ..domain.entities.bistable_switch import GrentonEntityBistableSwitch
from ..domain.entities.dimmer import GrentonEntityDimmer
from ..domain.entities.roller_shutter_v3 import GrentonEntityRollerShutterV3
from ..domain.entities.value import GrentonEntityValue
from ..domain.state_object import GrentonAttributeValueObject
from ..domain.action import GrentonActionMethod
from ..domain.enums import GrentonActionEventType, GrentonUnit


# ── helpers ──────────────────────────────────────────────────────────────────

def _attr(clu_id: str, obj: str, name: str) -> GrentonAttributeValueObject:
    return GrentonAttributeValueObject(clu_id=clu_id, object_name=obj, index=name)


def _exec(clu_id: str, obj: str, idx: str, val: str = "") -> GrentonActionMethod:
    return GrentonActionMethod(
        clu_id=clu_id,
        object_name=obj,
        event=GrentonActionEventType.CLICK,
        value=val,
        index=idx,
    )


# ── per-type factory functions ────────────────────────────────────────────────

def _make_switch(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceOnOff:
    # DOUT: execute(0, "1"|"0") = SetValue
    device = GrentonDeviceOnOff(type="DOUT", id=dev_id, entities=[], name=name)
    entity = GrentonEntityBistableSwitch(
        coordinator=coordinator,
        id=f"{dev_id}_0",
        label=None,
        unit=GrentonUnit.UNKNOWN,
        state_object=_attr(clu_id, lua, "Value"),
        action_on=_exec(clu_id, lua, "0", "1"),
        action_off=_exec(clu_id, lua, "0", "0"),
        device_info=device.device_info,
    )
    device.entities = [entity]
    return device


def _make_dimmer(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceDimmerV2:
    # DIMMER: Value range 0.0-1.0; execute(3)=SwitchOn, execute(4)=SwitchOff, execute(0,val)=SetValue
    device = GrentonDeviceDimmerV2(type="DIMMER", id=dev_id, entities=[], name=name)
    entity = GrentonEntityDimmer(
        coordinator=coordinator,
        id=f"{dev_id}_0",
        label=None,
        min=0.0,
        max=1.0,
        precision=2,
        state_object=_attr(clu_id, lua, "Value"),
        action_on=_exec(clu_id, lua, "3"),
        action_off=_exec(clu_id, lua, "4"),
        action_set_value=_exec(clu_id, lua, "0"),
        device_info=device.device_info,
    )
    device.entities = [entity]
    return device


def _make_led_rgb(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceLed:
    # LED_RGB: expose R/G/B as separate dimmer entities (0-255 per channel).
    #   read: RedValue(3), GreenValue(4), BlueValue(5)
    #   write: SetRedValue=execute(3), SetGreenValue=execute(4), SetBlueValue=execute(5)
    device = GrentonDeviceLed(type="LED_RGB", id=dev_id, entities=[], name=name)
    channels = [
        ("Red",   "RedValue",   "3"),
        ("Green", "GreenValue", "4"),
        ("Blue",  "BlueValue",  "5"),
    ]
    entities = []
    for label, attr, method in channels:
        entities.append(GrentonEntityDimmer(
            coordinator=coordinator,
            id=f"{dev_id}_{label.lower()}",
            label=label,
            min=0,
            max=255,
            precision=0,
            state_object=_attr(clu_id, lua, attr),
            action_on=_exec(clu_id, lua, method, "255"),
            action_off=_exec(clu_id, lua, method, "0"),
            action_set_value=_exec(clu_id, lua, method),
            device_info=device.device_info,
        ))
    device.entities = entities
    return device


def _make_led_rgbw(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceLed:
    # LED_RGBW: expose R/G/B/W as separate dimmer entities (0-255 per channel).
    #   read: RedValue(3), GreenValue(4), BlueValue(5), WhiteValue(15)
    #   write: SetRedValue=execute(3), SetGreenValue=execute(4),
    #          SetBlueValue=execute(5), SetWhiteValue=execute(12)
    #   NOTE: WhiteValue read attr index=15 but write method index=12
    device = GrentonDeviceLed(type="LED_RGBW", id=dev_id, entities=[], name=name)
    channels = [
        ("Red",   "RedValue",   "3"),
        ("Green", "GreenValue", "4"),
        ("Blue",  "BlueValue",  "5"),
        ("White", "WhiteValue", "12"),
    ]
    entities = []
    for label, attr, method in channels:
        entities.append(GrentonEntityDimmer(
            coordinator=coordinator,
            id=f"{dev_id}_{label.lower()}",
            label=label,
            min=0,
            max=255,
            precision=0,
            state_object=_attr(clu_id, lua, attr),
            action_on=_exec(clu_id, lua, method, "255"),
            action_off=_exec(clu_id, lua, method, "0"),
            action_set_value=_exec(clu_id, lua, method),
            device_info=device.device_info,
        ))
    device.entities = entities
    return device


def _make_led_channel(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceDimmerV2:
    # LED_CHANNEL: Value range 0-255; execute(1)=SwitchOn, execute(2)=SwitchOff, execute(0,v)=SetValue
    device = GrentonDeviceDimmerV2(type="LED_CHANNEL", id=dev_id, entities=[], name=name)
    entity = GrentonEntityDimmer(
        coordinator=coordinator,
        id=f"{dev_id}_0",
        label=None,
        min=0,
        max=255,
        precision=0,
        state_object=_attr(clu_id, lua, "Value"),
        action_on=_exec(clu_id, lua, "1"),
        action_off=_exec(clu_id, lua, "2"),
        action_set_value=_exec(clu_id, lua, "0"),
        device_info=device.device_info,
    )
    device.entities = [entity]
    return device


def _make_roller_shutter(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceRollerShutterV3:
    # ROLLER_SHUTTER: State(0), Position(7)=0-100, LamelPosition(8)=0-90
    #   execute(10,pos)=SetPosition, execute(9,tilt)=SetLamelPosition, execute(3)=Stop
    device = GrentonDeviceRollerShutterV3(type="ROLLER_SHUTTER", id=dev_id, entities=[], name=name)
    entity = GrentonEntityRollerShutterV3(
        coordinator=coordinator,
        id=dev_id,
        label=None,
        cover_state=_attr(clu_id, lua, "State"),
        cover_position=_attr(clu_id, lua, "Position"),
        set_position=_exec(clu_id, lua, "10"),
        cover_tilt_position=_attr(clu_id, lua, "LamelPosition"),
        set_tilt_position=_exec(clu_id, lua, "9"),
        stop=_exec(clu_id, lua, "3"),
        device_info=device.device_info,
    )
    device.entities = [entity]
    return device


def _make_din(
    clu_id: str, lua: str, name: str, dev_id: str,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceContactSensor:
    # DIN: Value(0)=0/1 digital input state
    device = GrentonDeviceContactSensor(type="DIN", id=dev_id, entities=[], name=name)
    entity = GrentonEntityBinarySensor(
        coordinator=coordinator,
        id=f"{dev_id}_0",
        label=None,
        reversed=False,
        state_object=_attr(clu_id, lua, "Value"),
        device_info=device.device_info,
    )
    device.entities = [entity]
    return device


# type_id -> (type_name)  for sensors — device class and unit are user-configurable
_SENSOR_TYPE_NAMES: dict[int, str] = {
    12: "ANALOG_IN",
    20: "TEMPERATURE_SENSOR",
    22: "HUMIDITY_SENSOR",
    34: "PRESSURE_SENSOR",
    36: "AIR_VOC_SENSOR",
    37: "SOUND_SENSOR",
    41: "AIR_CO2_SENSOR",
    42: "LIGHT_SENSOR_LUX",
}


def _make_sensor(
    clu_id: str, lua: str, name: str, dev_id: str,
    type_id: int,
    coordinator: GrentonCoordinator,
) -> GrentonDeviceValueV2:
    type_name = _SENSOR_TYPE_NAMES.get(type_id, f"TYPE_{type_id}")
    device = GrentonDeviceValueV2(type=type_name, id=dev_id, entities=[], name=name)
    entity = GrentonEntityValue(
        coordinator=coordinator,
        id=f"{dev_id}_0",
        label=None,
        state_object=_attr(clu_id, lua, "Value"),
        device_info=device.device_info,
    )
    device.entities = [entity]
    return device


# ── main mapper ───────────────────────────────────────────────────────────────

class DeviceFromOmMapper:
    """Create Home Assistant devices from om.lua object inventory."""

    @staticmethod
    def from_objects(
        objects_per_clu: dict[str, dict[str, Any]],
        coordinator: GrentonCoordinator,
    ) -> list[BaseGrentonDevice]:
        devices: list[BaseGrentonDevice] = []
        for clu_id, objects in objects_per_clu.items():
            for lua_name, obj_data in objects.items():
                type_id: int | None = obj_data.get("type_id")
                project_name: str = obj_data.get("project_name") or lua_name
                kind: str = obj_data.get("kind", "")

                if kind != OmObjectKind.IO.value:
                    continue

                dev_id = f"{clu_id}_{lua_name}"
                device = DeviceFromOmMapper._map(
                    clu_id, lua_name, project_name, dev_id, type_id, coordinator
                )
                if device is not None:
                    devices.append(device)
        return devices

    @staticmethod
    def _map(
        clu_id: str,
        lua_name: str,
        project_name: str,
        dev_id: str,
        type_id: int | None,
        coordinator: GrentonCoordinator,
    ) -> BaseGrentonDevice | None:
        if type_id == 4:
            return _make_switch(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id == 5:
            return _make_dimmer(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id == 9:
            return _make_led_rgb(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id == 11:
            return _make_led_rgbw(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id == 49:
            return _make_led_channel(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id == 24:
            return _make_roller_shutter(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id == 3:
            return _make_din(clu_id, lua_name, project_name, dev_id, coordinator)
        if type_id in _SENSOR_TYPE_NAMES:
            return _make_sensor(clu_id, lua_name, project_name, dev_id, type_id, coordinator)
        return None
