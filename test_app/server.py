"""Test app server for Grenton workflow validation.

Wires the integration's domain layer to a small REST + WebSocket API:
  - POST /api/connect        -> fetch OM interface (encryption keys, CLU list, widgets)
  - POST /api/clus/{id}/om   -> unlock TFTP + download + parse om.lua
  - POST /api/subscribe      -> register state keys with a CLU (clientRegister)
  - POST /api/execute        -> execute a method/attribute write on a CLU
  - WS   /ws                 -> live state updates pushed from CLU clientReport
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import bootstrap as g
from bootstrap import (
    GrentonClu,
    GrentonCluApi,
    GrentonCluStateAttributeKey,
    GrentonCluStateVariableKey,
    GrentonEncryption,
    GrentonObjectManagerApi,
    GrentonObjectManagerAuthError,
    OmObject,
    OmObjectKind,
    TftpError,
    fetch_om_lua,
    parse_om_lua,
)
from features import FEATURES, get_features
from omp_parser import parse_omp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOG = logging.getLogger("test_app")


# ── Session state ─────────────────────────────────────────────────────────────

@dataclass
class CluSubscription:
    """One CLU's open UDP connection + the keys registered for clientReport."""
    api: GrentonCluApi
    keys: list[Any] = field(default_factory=list)   # subscription order matters
    last_values: list[Any] = field(default_factory=list)


@dataclass
class Session:
    encryption: GrentonEncryption | None = None
    interface: dict[str, Any] | None = None            # raw mobile-interface JSON (from OM or .omp)
    clus: dict[str, GrentonClu] = field(default_factory=dict)   # by CLU id
    om_lua: dict[str, dict[str, OmObject]] = field(default_factory=dict)  # by CLU id — parsed
    subs: dict[str, CluSubscription] = field(default_factory=dict)        # by CLU id
    ws_clients: set[WebSocket] = field(default_factory=set)
    source: str = "none"                              # "om" | "omp" | "none"

    async def close(self) -> None:
        for sub in self.subs.values():
            try:
                await sub.api.disconnect()
            except Exception:
                pass
        self.subs.clear()


SESSION = Session()


# ── Request / response models ─────────────────────────────────────────────────

class ConnectReq(BaseModel):
    om_url: str           # e.g., http://192.168.5.193:9998
    pin: str              # token from OM "Send to Mobile" dialog


class SubscribeReq(BaseModel):
    clu_id: str
    # Either "variable" with `name`, or "attribute" with `object_name` + `name`
    keys: list[dict[str, str]]


class ExecuteReq(BaseModel):
    clu_id: str
    object_name: str      # Lua name, e.g. "DOU8306"
    method: str           # method index ("0", "1", ...)
    value: str            # value as string (CLU expects string; cast on its side)
    call_type: str = "execute"   # "execute" → obj:execute(idx,"val"); "set" → obj:set(idx,"val")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_connected() -> None:
    if SESSION.encryption is None:
        raise HTTPException(status_code=400, detail="Not connected — call /api/connect first")


async def _broadcast(payload: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for ws in SESSION.ws_clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        SESSION.ws_clients.discard(ws)


def _decode_keys(raw: list[dict[str, str]]) -> list[Any]:
    out: list[Any] = []
    for k in raw:
        kind = k.get("kind")
        if kind == "variable":
            out.append(GrentonCluStateVariableKey(name=k["name"]))
        elif kind == "attribute":
            out.append(GrentonCluStateAttributeKey(object_name=k["object_name"], name=k["name"]))
        else:
            raise HTTPException(400, f"Unknown key kind: {kind}")
    return out


async def _ping_all() -> None:
    """Send keep-alive pings to all open CLU connections."""
    for clu_id, sub in SESSION.subs.items():
        try:
            await sub.api.ping()
        except Exception as e:
            _LOG.warning("Ping failed for %s: %s", clu_id, e)


async def _ensure_clu_api(clu_id: str) -> CluSubscription:
    """Get-or-create a CLU subscription (opens UDP socket on first call)."""
    if clu_id in SESSION.subs:
        return SESSION.subs[clu_id]
    clu = SESSION.clus.get(clu_id)
    if not clu:
        raise HTTPException(404, f"CLU {clu_id} not in current interface")
    if SESSION.encryption is None:
        raise HTTPException(400, "No encryption — reconnect")

    api = GrentonCluApi(clu, SESSION.encryption)
    if not await api.connect():
        raise HTTPException(502, f"Failed to open UDP socket to CLU {clu.ip}:{clu.port}")
    sub = CluSubscription(api=api)
    SESSION.subs[clu_id] = sub

    # Wire the clientReport notifications to our WebSocket broadcast.
    async def _on_report(values: list[Any]) -> None:
        sub.last_values = values
        # Pair values with their keys for the frontend.
        pairs = []
        for key, val in zip(sub.keys, values):
            if isinstance(key, GrentonCluStateVariableKey):
                pairs.append({"kind": "variable", "name": key.name, "value": val})
            else:
                pairs.append({
                    "kind": "attribute",
                    "object_name": key.object_name,
                    "name": key.name,
                    "value": val,
                })
        await _broadcast({"type": "state", "clu_id": clu_id, "values": pairs})

    api.protocol.subscription_callback = _on_report   # type: ignore[union-attr]
    return sub


# ── Endpoints ────────────────────────────────────────────────────────────────

async def _ping_loop() -> None:
    """Periodically ping all open CLU connections to keep sessions alive."""
    while True:
        await asyncio.sleep(5)
        await _ping_all()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(_ping_loop())
    try:
        yield
    finally:
        task.cancel()
        await SESSION.close()


app = FastAPI(title="Grenton Workflow Tester", lifespan=_lifespan)


@app.post("/api/connect")
async def connect(req: ConnectReq):
    """Fetch the OM mobile interface and cache encryption + CLU list."""
    await SESSION.close()
    SESSION.om_lua.clear()
    SESSION.clus.clear()

    om_url = req.om_url.strip().rstrip("/")
    pin = req.pin.strip().replace(" ", "")    # OM dialog shows "892 146" — drop the space
    if not om_url:
        raise HTTPException(400, "OM URL is required")
    if not pin:
        raise HTTPException(400, "PIN is required — get it from OM → Project → Send Interface to Mobile Device")
    if not pin.isdigit():
        raise HTTPException(400, f"PIN must be numeric (got {pin!r})")
    _LOG.info("Connecting to OM url=%r pin=%r", om_url, pin)

    async with GrentonObjectManagerApi(om_url) as om:
        try:
            interface = await om.fetch_mobile_interface(pin)
        except GrentonObjectManagerAuthError as e:
            raise HTTPException(401, str(e))
        except Exception as e:
            raise HTTPException(502, f"OM fetch failed: {e}")

    SESSION.interface = interface
    SESSION.encryption = GrentonEncryption(
        key=interface["encryption"]["key"],
        iv=interface["encryption"]["iv"],
    )
    SESSION.source = "om"
    for clu in interface.get("clus", []):
        SESSION.clus[clu["id"]] = GrentonClu(
            id=clu["id"],
            serial_number=clu["serialNumber"],
            name=clu["name"],
            ip=clu["ip"],
            port=clu["port"],
        )

    _LOG.info("Connected to OM — %d CLUs, %d pages",
              len(SESSION.clus), len(interface.get("pages", [])))
    return _connect_response()


def _connect_response() -> dict[str, Any]:
    """Common payload returned by both /api/connect and /api/connect-omp."""
    interface = SESSION.interface or {}
    return {
        "source": SESSION.source,
        "clus": [
            {"id": c.id, "name": c.name, "ip": c.ip, "port": c.port, "serial": c.serial_number}
            for c in SESSION.clus.values()
        ],
        "page_count": len(interface.get("pages", [])),
        "widgets": _flatten_widgets(interface),
    }


@app.post("/api/connect-omp")
async def connect_omp(
    file: UploadFile = File(..., description="A .omp project file"),
    interface_node_id: str | None = None,
):
    """Bootstrap from a Grenton .omp project file — no OM running needed.

    Extracts:
      - encryption key/iv (properties.xml)
      - CLU list with IPs (system.xml)
      - mobile interface widgets (nodes/N/data.json — best by widget count if no override)
      - cached om.lua per CLU (diagnostics/*_W_*_om.lua)
    """
    await SESSION.close()
    SESSION.om_lua.clear()
    SESSION.clus.clear()

    blob = await file.read()
    try:
        project = parse_omp(blob)
    except Exception as e:
        raise HTTPException(400, f"Invalid .omp file: {type(e).__name__}: {e}")

    # Pick the interface — caller may override by node_id; otherwise pick the largest.
    chosen = None
    if interface_node_id:
        chosen = next((i for i in project.interfaces if i.node_id == interface_node_id), None)
        if not chosen:
            raise HTTPException(400, f"No interface with node_id {interface_node_id!r}")
    else:
        chosen = project.best_interface()
    if not chosen:
        raise HTTPException(400, "No mobile interfaces found in .omp")

    # Shape the chosen interface to match the mobile API's `pages`/`encryption`/`clus` JSON
    # the rest of the server already understands. (We use OmpProject for keys + CLUs because
    # nodes/data.json doesn't carry those fields.)
    SESSION.encryption = GrentonEncryption(key=project.key, iv=project.iv)
    SESSION.source = "omp"
    for c in project.clus:
        SESSION.clus[c.id] = GrentonClu(
            id=c.id,
            serial_number=c.serial_number,
            name=c.name,
            ip=c.ip,
            port=c.port,
        )
    # Per user preference: do NOT preload cached om.lua from the .omp — always
    # fetch fresh from each CLU via TFTP on demand (see /api/clus/{id}/om).
    SESSION.interface = chosen.data    # for widget flattening

    _LOG.info(
        ".omp loaded — %d CLUs, interface=%r (%d widgets across %d pages); "
        "%d other interfaces available",
        len(SESSION.clus),
        chosen.label, chosen.widget_count, chosen.page_count,
        len(project.interfaces) - 1,
    )

    payload = _connect_response()
    # Also include the list of available interfaces so the UI can let the user switch.
    payload["interfaces"] = [
        {
            "node_id": i.node_id, "uuid": i.project_uuid, "label": i.label,
            "page_count": i.page_count, "widget_count": i.widget_count,
            "active": i.node_id == chosen.node_id,
        }
        for i in sorted(project.interfaces, key=lambda x: -x.widget_count)
    ]
    return payload


def _flatten_widgets(interface: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a simple list of interactive widgets across all pages."""
    out: list[dict[str, Any]] = []
    for page in interface.get("pages", []):
        page_name = page.get("name", "")
        for w in page.get("widgets", []):
            entry = {
                "page": page_name,
                "type": w.get("type"),
                "id": w.get("id"),
                "label": w.get("label", ""),
            }
            # Best-effort extraction of the primary object reference
            obj = w.get("object")
            if isinstance(obj, dict):
                val = obj.get("value", {})
                entry.update({
                    "clu_id": val.get("cluId"),
                    "object_name": val.get("objectName"),
                    "index": val.get("index"),
                    "call_type": val.get("callType"),
                })
            elif w.get("components"):
                comps = w["components"]
                if comps:
                    first = comps[0]
                    state = first.get("state") or {}
                    entry.update({
                        "clu_id": state.get("cluId"),
                        "object_name": state.get("objectName"),
                        "index": state.get("index"),
                        "call_type": state.get("callType"),
                        "components": len(comps),
                    })
            out.append(entry)
    return out


@app.post("/api/clus/{clu_id}/om")
async def download_om_lua(clu_id: str):
    """Send req_start_ftp, TFTP-download a:\\om.lua, parse it."""
    _ensure_connected()
    clu = SESSION.clus.get(clu_id)
    if not clu:
        raise HTTPException(404, f"CLU {clu_id} not in current interface")
    try:
        text = await fetch_om_lua(clu.ip, SESSION.encryption, clu_port=clu.port)  # type: ignore[arg-type]
    except TftpError as e:
        raise HTTPException(502, f"TFTP failure: {e}")
    parsed = parse_om_lua(text)
    SESSION.om_lua[clu_id] = parsed
    _LOG.info("Downloaded om.lua from %s — %d objects", clu.name, len(parsed))
    return {
        "clu_id": clu_id,
        "byte_count": len(text),
        "objects": [
            {
                "lua_name": o.lua_name,
                "project_name": o.project_name,
                "kind": o.kind.value,
                "type_id": o.type_id,
                "type_name": o.type_name,
                "features": get_features(o.type_name),
            }
            for o in parsed.values()
        ],
        # Bundle the full feature catalog too, so the UI can look up types it
        # hasn't seen via per-object features (e.g., for filtering / docs).
        "feature_catalog": FEATURES,
    }


@app.post("/api/subscribe")
async def subscribe(req: SubscribeReq):
    """Register state keys with a CLU. Subsequent value changes arrive via /ws."""
    _ensure_connected()
    keys = _decode_keys(req.keys)
    if not keys:
        raise HTTPException(400, "Empty key list")

    sub = await _ensure_clu_api(req.clu_id)
    sub.keys = keys
    values = await sub.api.register_component_states(keys)
    if values is None:
        raise HTTPException(502, "clientRegister returned no values")
    sub.last_values = values

    # Echo initial values back through WS so UI shows current state immediately.
    await _broadcast({
        "type": "state",
        "clu_id": req.clu_id,
        "values": [
            _key_to_dict(k, v) for k, v in zip(keys, values)
        ],
    })
    return {"clu_id": req.clu_id, "registered": len(keys)}


def _key_to_dict(key: Any, value: Any) -> dict[str, Any]:
    if isinstance(key, GrentonCluStateVariableKey):
        return {"kind": "variable", "name": key.name, "value": value}
    return {
        "kind": "attribute",
        "object_name": key.object_name,
        "name": key.name,
        "value": value,
    }


@app.post("/api/execute")
async def execute(req: ExecuteReq):
    """Execute a method on a CLU object (e.g. DOU8306:execute(0,"1") → switch on)."""
    _ensure_connected()
    sub = await _ensure_clu_api(req.clu_id)

    if req.call_type == "set":
        action = g.GrentonActionAttribute(
            clu_id=req.clu_id,
            object_name=req.object_name,
            index=req.method,
            event=g.GrentonActionEventType.ON,
            value=req.value,
        )
    else:
        action = g.GrentonActionMethod(
            clu_id=req.clu_id,
            object_name=req.object_name,
            index=req.method,
            event=g.GrentonActionEventType.ON,
            value=req.value,
        )
    resp = await sub.api.execute_action_raw(action)
    if resp is None:
        raise HTTPException(502, "Execute failed (timeout / no response from CLU)")
    # Extract the payload portion from "resp:{ip}:{msgid}:{payload}"
    parts = resp.split(":", 3)
    payload = parts[3] if len(parts) == 4 else resp
    _LOG.info("execute %s:%s(%s) → %r", req.object_name, req.method, req.value, payload)
    return {"ok": True, "clu_response": payload}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    SESSION.ws_clients.add(ws)
    _LOG.info("WS client connected; total=%d", len(SESSION.ws_clients))
    try:
        # Replay last known values on connect
        for clu_id, sub in SESSION.subs.items():
            if sub.last_values:
                await ws.send_json({
                    "type": "state",
                    "clu_id": clu_id,
                    "values": [_key_to_dict(k, v) for k, v in zip(sub.keys, sub.last_values)],
                })
        while True:
            # Keep the socket alive; ignore client messages for now.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        SESSION.ws_clients.discard(ws)
        _LOG.info("WS client disconnected; total=%d", len(SESSION.ws_clients))


# ── Static frontend ──────────────────────────────────────────────────────────

import os
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/favicon.ico")
async def favicon():
    # Silence the browser's automatic /favicon.ico request — we have no icon.
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
