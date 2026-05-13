"""Per-type feature definitions for Grenton objects.

Sourced from OM's device-interfaces XML files. For each type the map lists:
- `read`:    attribute index → (name, range)   — values returned by clientReport
- `write`:   method index    → (name, param hint) — actions you can fire
- `kind`:    semantic group (used by the UI to pick a control widget)

Only types we want to expose in the tester are listed. Unknown types fall
back to a generic "read attr 0 / write method 0 with text value" control.

Note: LED_RGB has an asymmetry — feature 15 (WhiteValue) is written via
method 12 (SetWhiteValue). The data structure handles this via separate
read/write index tables.
"""

from __future__ import annotations

from typing import Any

# kind values used by the frontend:
#   "switch"     — binary on/off, single attribute
#   "dimmer"     — single 0-N analog value
#   "rgbw"       — four independent channels (R/G/B/W) with separate read/write indices
#   "cover"      — roller shutter position + tilt
#   "sensor"     — read-only multi-attribute
#   "generic"    — fallback: numeric text input + Send

FEATURES: dict[str, dict[str, Any]] = {
    # ── outputs ──────────────────────────────────────────────────────────────
    "DOUT": {
        "kind": "switch",
        "read":  {0: ("Value", "0-1")},
        "write": {0: ("SetValue", "0|1")},
    },
    "DIMMER": {
        "kind": "dimmer",
        "read":  {0: ("Value", "0-1"), 1: ("RampTime", "ms")},
        "write": {0: ("SetValue", "0.0-1.0"), 3: ("SwitchOn", ""), 4: ("SwitchOff", "")},
    },
    "LED_RGB": {
        "kind": "rgbw",
        "read": {
            0:  ("Value",       "0-1"),
            1:  ("Hue",         "0-360"),
            2:  ("Saturation",  "0-1"),
            3:  ("RedValue",    "0-255"),
            4:  ("GreenValue",  "0-255"),
            5:  ("BlueValue",   "0-255"),
            6:  ("RGB",         "#RRGGBB"),
            15: ("WhiteValue",  "0-255"),
        },
        # ⚠ feature 15 (read) maps to method 12 (write) — separate index spaces
        "write": {
            0:  ("SetValue",         "0.0-1.0"),
            1:  ("SetHue",           "0-360"),
            2:  ("SetSaturation",    "0.0-1.0"),
            3:  ("SetRedValue",      "0-255"),
            4:  ("SetGreenValue",    "0-255"),
            5:  ("SetBlueValue",     "0-255"),
            6:  ("SetRGBvalue",      "#RRGGBB"),
            9:  ("SwitchOn",         ""),
            10: ("SwitchOff",        ""),
            11: ("Switch",           ""),
            12: ("SetWhiteValue",    "0-255"),
        },
        # Mapping a read channel → its corresponding write method (for the UI).
        "channel_map": {3: 3, 4: 4, 5: 5, 15: 12},
    },
    "LED_RGBW": {
        "kind": "rgbw",
        "read": {
            0:  ("Value", "0-1"), 1: ("Hue", "0-360"), 2: ("Saturation", "0-1"),
            3:  ("RedValue", "0-255"), 4: ("GreenValue", "0-255"),
            5:  ("BlueValue", "0-255"), 6: ("RGB", "#RRGGBB"),
            15: ("WhiteValue", "0-255"),
        },
        "write": {
            0:  ("SetValue", "0.0-1.0"), 1: ("SetHue", "0-360"),
            2:  ("SetSaturation", "0.0-1.0"),
            3:  ("SetRedValue", "0-255"), 4: ("SetGreenValue", "0-255"),
            5:  ("SetBlueValue", "0-255"), 12: ("SetWhiteValue", "0-255"),
        },
        "channel_map": {3: 3, 4: 4, 5: 5, 15: 12},
    },
    "LED_CHANNEL": {
        # The newer per-channel LED model (LED_RGBW_DIN fv03) — each channel is a separate object.
        "kind": "dimmer",
        "read":  {0: ("Value", "0-255")},
        "write": {0: ("SetValue", "0-255"), 1: ("SwitchOn", ""), 2: ("SwitchOff", "")},
    },

    # ── covers ───────────────────────────────────────────────────────────────
    # Authoritative mapping from module_2_0_ROLLER_SHUTTER_DIN_fv03_02.xml (class 24)
    # Confirmed against om.lua set() calls: set(9,30000)=MaxTime, set(14)=BlindsUpMaxTime, etc.
    "ROLLER_SHUTTER": {
        "kind": "cover",
        "read": {
            0: ("State",         ""),       # 0=Stop 1=Up 2=Down 3=Blocked 4=Calibration
            7: ("Position",      "0-100"),  # % open (0=closed, 100=open)
            8: ("LamelPosition", "0-90"),   # tilt degrees (0=open, 90=closed)
        },
        "write": {
            0:  ("MoveUp",            "0"),    # time ms, 0 = use MaxTime
            1:  ("MoveDown",          "0"),    # time ms, 0 = use MaxTime
            3:  ("Stop",              ""),
            9:  ("SetLamelPosition",  "0-90"),
            10: ("SetPosition",       "0-100"),
        },
        # State is read-only; Position maps to SetPosition (method 10); Tilt → SetLamelPosition (9)
        "channel_map": {0: -1, 7: 10, 8: 9},
        # Action buttons: MoveUp(0)/MoveDown(0) fully open/close; Stop
        "actions": [(0, "Open", "0"), (1, "Close", "0"), (3, "Stop", "")],
    },

    # ── sensors ──────────────────────────────────────────────────────────────
    "DIN": {
        "kind": "sensor",
        "read":  {0: ("Value", "0-1")},
        "write": {},
    },
    "TEMPERATURE_SENSOR": {
        "kind": "sensor",
        "read":  {0: ("Value", "°C")},
        "write": {},
    },
    "HUMIDITY_SENSOR": {
        "kind": "sensor",
        "read":  {0: ("Value", "%")},
        "write": {},
    },
    "PRESSURE_SENSOR": {
        "kind": "sensor",
        "read":  {0: ("Value", "hPa")},
        "write": {},
    },
    "LIGHT_SENSOR_LUX": {
        "kind": "sensor",
        "read":  {0: ("Value", "lx")},
        "write": {},
    },
    "AIR_CO2_SENSOR": {
        "kind": "sensor",
        "read":  {0: ("Value", "ppm")},
        "write": {},
    },
    "AIR_VOC_SENSOR": {
        "kind": "sensor",
        "read":  {0: ("Value", "")},
        "write": {},
    },
    "SOUND_SENSOR": {
        "kind": "sensor",
        "read":  {0: ("Value", "")},
        "write": {},
    },
    "ANALOG_IN": {
        "kind": "sensor",
        "read":  {0: ("Value", "raw")},
        "write": {},
    },
    "ANALOG_OUT": {
        "kind": "dimmer",
        "read":  {0: ("Value", "0-10V")},
        "write": {0: ("SetValue", "0-10")},
    },

    # ── virtual ──────────────────────────────────────────────────────────────
    "TIMER": {
        "kind": "switch",
        "read":  {0: ("Time", "ms"), 1: ("Mode", ""), 2: ("State", "0-1"), 3: ("RemainingTime", "ms")},
        "write": {0: ("Start", ""), 1: ("Stop", ""), 2: ("SetTime", "ms"),
                  3: ("StartCountDownTimer", "ms")},
    },
    # Authoritative mapping from object_thermostat_v2.xml (class 18, version 2).
    # set()-methods need call_type="set" in 3rd tuple element; execute()-methods are default.
    "THERMOSTAT": {
        "kind": "thermostat",
        "read": {
            3:  ("PointValue",  "°C"),   # manual setpoint (writable via set)
            6:  ("State",       ""),     # 0=inactive, 1=active
            7:  ("CtrlDir",     ""),     # 0=Normal/heat, 1=Reverse/cool
            8:  ("Mode",        ""),     # 0=Manual,1=Holiday,2=Auto,3=HeatUp
            12: ("TargetTemp",  "°C"),   # effective target (read-only)
            13: ("ControlOut",  ""),     # heating output value (read-only)
            14: ("CurrentTemp", "°C"),   # sensor temperature (read-only)
        },
        "write": {
            3: ("SetPointValue", "10.0-35.0", "set"),
            6: ("SetState",      "0|1",       "set"),
            7: ("SetCtrlDir",    "0|1",       "set"),
            8: ("SetMode",       "0-3",       "set"),
        },
        "channel_map": {3: 3, 6: 6, 7: 7, 8: 8, 12: -1, 13: -1, 14: -1},
        "actions": [
            (0, "Start",        ""),
            (1, "Stop",         ""),
            (7, "AutoModeOn",   ""),
            (8, "AutoModeOff",  ""),
        ],
    },
    "PRESENCE": {
        "kind": "sensor",
        "read":  {0: ("Value", "0-1")},
        "write": {},
    },
    "SCHEDULER": {
        "kind": "switch",
        "read":  {0: ("State", "")},
        "write": {0: ("Start", ""), 1: ("Stop", "")},
    },
}


def get_features(type_name: str) -> dict[str, Any]:
    """Return the feature definition for a type, or a generic fallback."""
    return FEATURES.get(type_name, {
        "kind": "generic",
        "read":  {0: ("Value", "?")},
        "write": {0: ("Execute", "?")},
    })
