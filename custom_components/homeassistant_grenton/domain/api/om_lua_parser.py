"""Parser for Grenton CLU om.lua files.

om.lua is deployed to each CLU by Object Manager and contains the full
object inventory with the mapping between internal Lua names (e.g. DOU8306)
and human-readable project names (e.g. Pietro_Pralnia_Gniazda_Zelazko).

The mapping is encoded in structured comments immediately after each object
definition:
    DOU8306 = OBJECT:new(4, mm_330005854, 0, "DOU8306")
    -- NAME_IO Pietro_Pralnia_Gniazda_Zelazko=DOU8306

Comment prefixes:
    NAME_CLU        CLU and GATE devices
    NAME_PERIPHERY  Virtual objects (Timer, Thermostat, Scheduler, Presence, ...)
    NAME_IO         Physical I/O channels (DIN, DOUT, ROL, DIM, POW, ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_NAME_PATTERN = re.compile(
    r"--\s*NAME_(?P<kind>CLU|PERIPHERY|IO)\s+(?P<project>[^=\s]+)=(?P<lua>[^\s]+)"
)
_OBJECT_DEF_PATTERN = re.compile(
    r"^(?P<lua>\w+)\s*=\s*OBJECT:new\((?P<type_id>\d+)"
)
_USER_VAR_PATTERN = re.compile(
    r"^(?P<lua>\w+)\s*=\s*.+--\s*(?P<dtype>number|string|boolean)"
)


class OmObjectKind(str, Enum):
    CLU = "CLU"
    PERIPHERY = "PERIPHERY"
    IO = "IO"
    USER_VAR = "USER_VAR"


# Grenton type codes found in OBJECT:new(type_id, ...)
#
# Authoritative mappings (verified against OM device-interfaces/*.xml):
#   3, 4, 5, 7, 9, 12, 13, 20, 21, 22, 24, 27, 29, 32, 33, 34, 35, 36, 37, 38,
#   41, 42, 49
# Heuristic mappings (observed in real projects, no XML source):
#   1 (CLU_REMOTE refs), 2 (MODULE wrappers mm_*), 6 (TIMER), 16 (SCHEDULER),
#   17 (CALENDAR), 18 (THERMOSTAT), 19 (panel button), 23 (MULTISENSOR root),
#   28 (DALI_GROUP?), 30 (panel LED / SATEL — clashes with class 30 RS232),
#   31 (MODBUS), 39 (SUNRISE_SUNSET), 40 (PRESENCE), 43 (EVENT_SCHEDULER),
#   44 (STORAGE)
_TYPE_ID_NAMES: dict[int, str] = {
    0: "CLU_MAIN",
    1: "CLU_REMOTE",
    2: "MODULE",
    3: "DIN",
    4: "DOUT",
    5: "DIMMER",
    6: "TIMER",
    7: "BUTTON",
    8: "BUTTON_ANALOG",
    9: "LED_RGB",
    10: "LED_RGB_LEGACY",
    11: "LED_RGBW",
    12: "ANALOG_IN",
    13: "ANALOG_OUT",
    14: "GSM",
    15: "STATISTICS",
    16: "SCHEDULER",
    17: "CALENDAR",
    18: "THERMOSTAT",
    19: "PANEL_BUTTON",
    20: "TEMPERATURE_SENSOR",
    21: "PANEL_LIGHT_SENSOR",
    22: "HUMIDITY_SENSOR",
    23: "MULTISENSOR",
    24: "ROLLER_SHUTTER",
    25: "ZWAVE_MODULE",
    26: "PUSH_NOTIFICATION",
    27: "DALI_MASTER",
    28: "DALI_GROUP",
    29: "PANEL",
    30: "PANEL_LED",
    31: "MODBUS_CLIENT",
    32: "DALI_GEAR",
    33: "RS232_CONTROLLER",
    34: "PRESSURE_SENSOR",
    35: "DALI_GEAR_DT8",
    36: "AIR_VOC_SENSOR",
    37: "SOUND_SENSOR",
    38: "IR_CONTROLLER",
    39: "SUNRISE_SUNSET",
    40: "PRESENCE",
    41: "AIR_CO2_SENSOR",
    42: "LIGHT_SENSOR_LUX",
    43: "EVENT_SCHEDULER",
    44: "STORAGE",
    49: "LED_CHANNEL",
}


@dataclass
class OmObject:
    lua_name: str
    project_name: str
    kind: OmObjectKind
    type_id: int | None = None

    @property
    def type_name(self) -> str:
        if self.type_id is None:
            return "UNKNOWN"
        return _TYPE_ID_NAMES.get(self.type_id, f"TYPE_{self.type_id}")


def parse_om_lua(content: str) -> dict[str, OmObject]:
    """Parse an om.lua file and return lua_name -> OmObject mapping.

    Args:
        content: Raw text content of om.lua

    Returns:
        Dict keyed by Lua internal name (e.g. "DOU8306") mapping to OmObject
        with project name, kind, and type id.
    """
    result: dict[str, OmObject] = {}
    lines = content.splitlines()

    # Pass 1: collect type_id for each lua name from OBJECT:new() calls
    type_ids: dict[str, int] = {}
    for line in lines:
        m = _OBJECT_DEF_PATTERN.match(line.strip())
        if m:
            type_ids[m.group("lua")] = int(m.group("type_id"))

    # Pass 2: collect NAME_ comment mappings
    for line in lines:
        m = _NAME_PATTERN.search(line)
        if m:
            lua_name = m.group("lua")
            project_name = m.group("project")
            kind = OmObjectKind(m.group("kind"))
            obj = OmObject(
                lua_name=lua_name,
                project_name=project_name,
                kind=kind,
                type_id=type_ids.get(lua_name),
            )
            result[lua_name] = obj

    return result


def parse_om_lua_zip(zip_bytes: bytes) -> dict[str, dict[str, OmObject]]:
    """Parse all om.lua files from a Grenton diagnostic package ZIP.

    Args:
        zip_bytes: Raw bytes of the diagnostic package ZIP file

    Returns:
        Dict keyed by CLU folder name (e.g. "CLU856739") mapping to the
        lua_name -> OmObject dict for that CLU.
    """
    import io
    import zipfile

    result: dict[str, dict[str, OmObject]] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for entry in z.namelist():
            parts = entry.split("/")
            if len(parts) == 2 and parts[1] == "om.lua":
                clu_folder = parts[0]
                with z.open(entry) as f:
                    content = f.read().decode("utf-8", errors="replace")
                result[clu_folder] = parse_om_lua(content)
    return result


def parse_om_lua_omp(zip_bytes: bytes) -> dict[str, dict[str, OmObject]]:
    """Parse the latest om.lua for each CLU from a Grenton .omp project file.

    The .omp file is a ZIP archive that caches all diagnostic runs. Each entry
    follows the naming convention:
        diagnostics/{timestamp}_W_{serial}_om.lua

    This function picks the latest (alphabetically last, since timestamps sort
    chronologically) write entry for each CLU serial number.

    Args:
        zip_bytes: Raw bytes of the .omp project ZIP file

    Returns:
        Dict keyed by CLU serial string (e.g. "221003453") mapping to the
        lua_name -> OmObject dict for that CLU.
    """
    import io
    import zipfile
    import re

    _OMP_OM_LUA = re.compile(r"diagnostics/(\d{8}_\d{6}_\d+)_W_(\d+)_om\.lua$")

    # Collect the latest entry per CLU serial
    latest: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for entry in z.namelist():
            m = _OMP_OM_LUA.match(entry)
            if m:
                timestamp, serial = m.group(1), m.group(2)
                if serial not in latest or timestamp > latest[serial].split("_W_")[0].split("/")[-1]:
                    latest[serial] = entry

        result: dict[str, dict[str, OmObject]] = {}
        for serial, entry in latest.items():
            with z.open(entry) as f:
                content = f.read().decode("utf-8", errors="replace")
            result[serial] = parse_om_lua(content)

    return result


def build_project_name_map(
    per_clu: dict[str, dict[str, OmObject]],
) -> dict[str, str]:
    """Flatten all CLU mappings into a single lua_name -> project_name dict.

    When the same lua name appears in multiple CLUs (which shouldn't happen
    since CLU numeric suffixes are unique), the last one wins.
    """
    flat: dict[str, str] = {}
    for objects in per_clu.values():
        for lua_name, obj in objects.items():
            flat[lua_name] = obj.project_name
    return flat
