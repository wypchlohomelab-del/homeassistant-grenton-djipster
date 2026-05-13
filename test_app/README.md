# Grenton Workflow Tester

Standalone web app that exercises the full Grenton integration workflow without
needing Home Assistant. Uses the **same `domain/` layer** as the HA integration
in [`custom_components/homeassistant_grenton/`](../custom_components/homeassistant_grenton/) —
no code duplication.

What it validates end-to-end:
1. **Connect** — two paths:
   - Object Manager REST API (OM must be running): fetch encryption keys, CLU list, widgets
   - Upload a `.omp` project file: extract keys + CLU addresses from the ZIP (no OM needed)
2. **Download `om.lua`** from each CLU live over TFTP (after `req_start_ftp` unlock)
3. **Parse `om.lua`** → list of project objects with Lua names + types
4. **Subscribe** to selected object attributes via CLU `clientRegister`
5. **Receive live updates** through CLU `clientReport` notifications (WebSocket → UI)
6. **Execute methods** to switch values (relay on/off, dimmer set, RGBW channels, etc.)

## Run

```powershell
cd test_app
pip install -r requirements.txt
python server.py
```

Open <http://127.0.0.1:8765> in a browser. To connect you need either:
- **OM API path**: Object Manager running on your PC (port 9998) + the PIN from
  OM → Project → Send Interface to Mobile Device dialog
- **`.omp` path**: Export your project from Object Manager (File → Export → `.omp`);
  the file contains the encryption keys and CLU IP addresses.  
  `om.lua` is still fetched live from the CLU via TFTP — no stale cache.

Either way the CLU(s) must be reachable on your LAN.

On Windows, allow `python.exe` through Windows Firewall so the CLU's TFTP DATA
packets (sent from an ephemeral port) aren't dropped.

## Architecture

```
test_app/
├── bootstrap.py          # registers a stub homeassistant_grenton package so
│                         # we can import its pure-Python submodules without
│                         # triggering HA imports in __init__.py
├── server.py             # FastAPI: REST endpoints + WebSocket /ws
├── features.py           # per-type read/write attribute + method map
├── omp_parser.py         # extracts keys + CLU list from .omp ZIP
└── static/index.html     # vanilla JS single-page UI
```

The `Session` singleton in `server.py` caches:
- `encryption`     — AES key/iv from `/api/v1/interface/hash/{pin}/object`
- `clus`           — dict of CLU id → `GrentonClu` dataclass
- `om_lua`         — parsed `OmObject` map per CLU id
- `subs`           — open `GrentonCluApi` connections + their subscription keys
- `ws_clients`     — active WebSocket connections for state push

Every `clientReport` notification from any CLU is fanned out to all WS clients.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/connect` | `{om_url, pin}` → connects to OM, returns CLU list + widgets |
| POST | `/api/connect-omp` | multipart file upload → parse `.omp`, return same shape |
| POST | `/api/clus/{id}/om` | Unlock TFTP, download `a:\om.lua`, return parsed objects |
| POST | `/api/subscribe` | `{clu_id, keys:[{kind,name,object_name?}]}` → registers state subscriptions |
| POST | `/api/execute` | `{clu_id, object_name, method, value}` → fires `obj:execute(method, value)` |
| WS | `/ws` | Server pushes `{type:"state", clu_id, values:[...]}` on every report |

## Reuse from the integration

`bootstrap.py` exposes the relevant pure modules:
- `GrentonObjectManagerApi` — OM REST client
- `GrentonCluApi` — encrypted UDP CLU client (ping / register / execute)
- `GrentonCipher` / `GrentonEncryption` — AES-128-CBC + PKCS7
- `fetch_om_lua()` — `req_start_ftp` + RFC 1350 TFTP read
- `parse_om_lua()` — extracts project name ↔ Lua name mapping + type ids
- `GrentonAction*` — action dataclasses (Method / Attribute / Variable / Script)

These are byte-identical to what HA loads — changes in `custom_components/`
are picked up automatically (no `pip install -e`, no copies).

## Known gotchas

- **Windows Firewall** may silently drop TFTP DATA replies (different ephemeral
  port than the outbound RRQ to UDP 69). Add an inbound rule for `python.exe` —
  see [`architecture/getting_grenton_config.md`](../architecture/getting_grenton_config.md).
- **CLU attribute index defaults to `0`** when the integration can't infer it
  from a widget — works for DOUT (relay state), wrong for dimmers (use `3`)
  and roller shutters (use `0` for position). Edit per object as needed.
- **One subscription at a time per CLU.** Calling `/api/subscribe` again with a
  different key set replaces the previous registration on that CLU.
