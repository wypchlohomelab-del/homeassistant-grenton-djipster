"""Parse a Grenton .omp project file (ZIP) into structured data.

What gets extracted:
  - encryption key/iv      from properties.xml  <keyBytes> / <ivBytes>
  - CLU list (id/name/ip)  from system.xml      <clu> blocks
  - mobile interfaces      from nodes/N/data.json (one entry per "Send to Mobile" target)

om.lua is intentionally NOT extracted from the .omp diagnostics cache — the integration
always pulls a fresh copy from each CLU via TFTP (see clu_tftp.fetch_om_lua).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any


_KEY_RE = re.compile(r"<keyBytes[^>]*>([^<]+)</keyBytes>")
_IV_RE  = re.compile(r"<ivBytes[^>]*>([^<]+)</ivBytes>")
_CLU_RE = re.compile(
    r"<clu\s+id=\"(?P<id>\d+)\">"
    r".*?<name>(?P<name>[^<]+)</name>"
    r".*?<ipAddress>(?P<ip>[\d.]+)</ipAddress>"
    r".*?<nameOnCLU>(?P<lua_id>CLU\d+)</nameOnCLU>"
    r".*?<serialNumber>(?P<serial_hex>[0-9a-fA-F]+)</serialNumber>",
    re.DOTALL,
)


@dataclass
class OmpCluInfo:
    id: str            # Lua identifier, e.g. "CLU856739"
    serial_number: str # decimal, e.g. "221003453"
    serial_hex: str    # hex form from system.xml
    name: str          # display name, e.g. "CLU" or "GateHTTP"
    ip: str
    port: int = 1234


@dataclass
class OmpInterface:
    """One mobile interface (entry in nodes/N/data.json)."""
    node_id: str        # the N in nodes/N/data.json
    project_uuid: str   # the `id` field inside the JSON
    label: str          # e.g. "Z6AGłówny"
    page_count: int
    widget_count: int
    data: dict[str, Any]  # full JSON — same shape as OM API response


@dataclass
class OmpProject:
    key: str              # base64 AES key
    iv: str               # base64 AES iv
    clus: list[OmpCluInfo] = field(default_factory=list)
    interfaces: list[OmpInterface] = field(default_factory=list)

    def best_interface(self) -> OmpInterface | None:
        """Return the interface with the most widgets (usually the main one)."""
        if not self.interfaces:
            return None
        return max(self.interfaces, key=lambda i: i.widget_count)


def parse_omp(data: bytes) -> OmpProject:
    """Parse a .omp file's bytes into an OmpProject.

    Raises:
        ValueError: If the file is not a valid .omp archive or required entries
                    are missing / malformed.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        # 1) Encryption keys from properties.xml
        try:
            props = z.read("properties.xml").decode("utf-8")
        except KeyError:
            raise ValueError("properties.xml not found — not a valid .omp file")
        key_m = _KEY_RE.search(props)
        iv_m  = _IV_RE.search(props)
        if not key_m or not iv_m:
            raise ValueError("Encryption keys not found in properties.xml")
        project = OmpProject(key=key_m.group(1), iv=iv_m.group(1))

        # 2) CLU list from system.xml
        try:
            sys_xml = z.read("system.xml").decode("utf-8")
        except KeyError:
            raise ValueError("system.xml not found — not a valid .omp file")

        seen: set[str] = set()
        for m in _CLU_RE.finditer(sys_xml):
            lua_id = m.group("lua_id")
            if lua_id in seen:
                continue
            seen.add(lua_id)
            serial_hex = m.group("serial_hex")
            try:
                serial_dec = str(int(serial_hex, 16))
            except ValueError:
                serial_dec = serial_hex
            project.clus.append(OmpCluInfo(
                id=lua_id,
                serial_number=serial_dec,
                serial_hex=serial_hex,
                name=m.group("name"),
                ip=m.group("ip"),
            ))

        # 3) Mobile interfaces from nodes/N/data.json
        for entry in z.namelist():
            mn = re.fullmatch(r"nodes/(\d+)/data\.json", entry)
            if not mn:
                continue
            try:
                data_json = json.loads(z.read(entry).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            pages = data_json.get("pages", [])
            widgets = sum(len(p.get("widgets", [])) for p in pages)
            project.interfaces.append(OmpInterface(
                node_id=mn.group(1),
                project_uuid=data_json.get("id", ""),
                label=data_json.get("label") or data_json.get("name") or f"node_{mn.group(1)}",
                page_count=len(pages),
                widget_count=widgets,
                data=data_json,
            ))

    return project


def build_interface_dict(project: OmpProject, interface: OmpInterface) -> dict[str, Any]:
    """Merge project-level data with interface data into the shape GrentonMobileInterfaceDto expects.

    The OM API response combines encryption + CLU list + interface JSON in one payload.
    When loading from .omp those three come from separate files, so we reassemble them here.
    """
    return {
        # Provide defaults for fields that may be absent in older .omp data.json files.
        "version": 1,
        "name": interface.label,
        "icon": "",
        "theme": "DARK",
        "pushNotifications": [],
        # Raw interface data (pages, id, and any other fields from data.json).
        **interface.data,
        # Always override encryption and CLUs with the authoritative project-level values.
        "encryption": {"key": project.key, "iv": project.iv},
        "clus": [
            {
                "id": clu.id,
                "serialNumber": clu.serial_number,
                "name": clu.name,
                "ip": clu.ip,
                "port": clu.port,
                "connectionType": "LOCAL",
            }
            for clu in project.clus
        ],
    }
