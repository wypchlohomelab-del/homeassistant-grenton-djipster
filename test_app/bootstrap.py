"""Import the integration's pure-Python domain layer without triggering HA imports.

The package's `__init__.py` imports `homeassistant.core`, which isn't available
outside HA. We register a stub `homeassistant_grenton` module before importing
any submodule so Python's import machinery skips running `__init__.py`.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_INTEGRATION_PARENT = (Path(__file__).resolve().parent.parent / "custom_components").resolve()
_PACKAGE_DIR = _INTEGRATION_PARENT / "homeassistant_grenton"

if not _PACKAGE_DIR.is_dir():
    raise RuntimeError(f"Integration not found at {_PACKAGE_DIR}")

# 1) Make the parent visible so submodule absolute imports work.
sys.path.insert(0, str(_INTEGRATION_PARENT))

# 2) Insert a stub for the package itself so __init__.py isn't executed.
if "homeassistant_grenton" not in sys.modules:
    stub = types.ModuleType("homeassistant_grenton")
    stub.__path__ = [str(_PACKAGE_DIR)]  # type: ignore[attr-defined]
    sys.modules["homeassistant_grenton"] = stub

# 3) Pre-import the pure modules so users of bootstrap can grab them.
from homeassistant_grenton.domain.encryption import GrentonEncryption       # noqa: E402
from homeassistant_grenton.domain.cipher import GrentonCipher               # noqa: E402
from homeassistant_grenton.domain.clu import GrentonClu                     # noqa: E402
from homeassistant_grenton.domain.action import (                           # noqa: E402
    GrentonAction,
    GrentonActionAttribute,
    GrentonActionMethod,
    GrentonActionScript,
    GrentonActionVariable,
)
from homeassistant_grenton.domain.enums import GrentonActionEventType        # noqa: E402
from homeassistant_grenton.state import (                                   # noqa: E402
    GrentonCluStateAttributeKey,
    GrentonCluStateVariableKey,
)
from homeassistant_grenton.domain.api.object_manager import (               # noqa: E402
    GrentonObjectManagerApi,
    GrentonObjectManagerAuthError,
    GrentonObjectManagerConnectionError,
)
from homeassistant_grenton.domain.api.clu import GrentonCluApi              # noqa: E402
from homeassistant_grenton.domain.api.clu_tftp import fetch_om_lua, TftpError  # noqa: E402
from homeassistant_grenton.domain.api.om_lua_parser import (                # noqa: E402
    OmObject,
    OmObjectKind,
    parse_om_lua,
)

__all__ = [
    "GrentonEncryption",
    "GrentonCipher",
    "GrentonClu",
    "GrentonAction",
    "GrentonActionAttribute",
    "GrentonActionMethod",
    "GrentonActionScript",
    "GrentonActionVariable",
    "GrentonActionEventType",
    "GrentonCluStateAttributeKey",
    "GrentonCluStateVariableKey",
    "GrentonObjectManagerApi",
    "GrentonObjectManagerAuthError",
    "GrentonObjectManagerConnectionError",
    "GrentonCluApi",
    "fetch_om_lua",
    "TftpError",
    "OmObject",
    "OmObjectKind",
    "parse_om_lua",
]
