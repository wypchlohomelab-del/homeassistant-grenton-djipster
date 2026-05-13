# Getting Grenton Configuration — Online Discovery

How the integration retrieves the Grenton project configuration from a live
installation, with zero file uploads required from the user.

---

## What we need from the Grenton side

To map Grenton objects to Home Assistant entities, the integration needs:

| Data | Purpose | Source |
|------|---------|--------|
| Project AES key / IV | Encrypts all CLU UDP traffic on port 1234 | OM REST API (PC-side) |
| CLU IP, port, ID | Where each CLU lives on the LAN | OM REST API |
| Widget tree (UI layout, labels) | Maps user-friendly names to Lua object names | OM REST API |
| `om.lua` per CLU | Object inventory: project name ↔ Lua name ↔ type | **CLU TFTP** (this doc) |

Everything except `om.lua` comes from a single HTTP request to Object Manager
(see `references/object-manager-api.md` in the grenton skill). `om.lua`
historically required generating a diagnostic package by hand — that's the part
we now do online.

---

## The breakthrough: bare `req_start_ftp`

The CLU's UDP listener on port 1234 has **two command dispatchers** (discovered
by decompiling `com.grenton.om_1.12.1.2516.jar` + black-box testing):

```
decrypt(packet) starts with "req:"   → evaluate payload as Lua
decrypt(packet) is anything else      → management command handler
```

The management handler recognizes a short list of bare commands including
`req_start_ftp`, `req_stop_ftp`, `req_set_key`, `req_set_clu_ip`,
`req_reload_scripts`, `req_reset`, `req_refresh_modules`, `req_control`,
`req_discovery_clu`. For each, the CLU responds with raw `resp:OK` (also
encrypted with the project key).

The integration's protocol uses `req::{msgid}:{lua}` — which falls under the
first dispatcher. So earlier attempts like `req::aabbcc:req_start_ftp` were
evaluated as Lua (`req_start_ftp` → unknown variable → returned `nil`).

Sending **the bare string** `req_start_ftp` (encrypted with the same project
key the integration already has) bypasses Lua and hits the management handler.

---

## End-to-end recipe

```python
import socket, struct, base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# Step 0 — already have these from the OM API /interface/hash/{pin}/object call
KEY    = base64.b64decode(interface["encryption"]["key"])   # 16 bytes
IV     = base64.b64decode(interface["encryption"]["iv"])    # 16 bytes
CLU_IP = clu["ip"]

def encrypt(pt: bytes) -> bytes:
    pad = 16 - (len(pt) % 16)
    pt += bytes([pad] * pad)                    # PKCS7
    c = Cipher(algorithms.AES(KEY), modes.CBC(IV), backend=default_backend()).encryptor()
    return c.update(pt) + c.finalize()

def decrypt(ct: bytes) -> bytes:
    c = Cipher(algorithms.AES(KEY), modes.CBC(IV), backend=default_backend()).decryptor()
    pt = c.update(ct) + c.finalize()
    return pt[:-pt[-1]]                         # strip PKCS7

# Step 1 — Unlock TFTP server
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(encrypt(b"req_start_ftp"), (CLU_IP, 1234))
raw, _ = s.recvfrom(1024); s.close()
assert decrypt(raw) == b"resp:OK"

# Step 2 — Plain TFTP GET on UDP 69 (RFC 1350, octet mode)
#   filename MUST be lowercase: "a:\om.lua"
ts = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); ts.settimeout(15)
ts.sendto(b"\x00\x01" + b"a:\\om.lua" + b"\x00octet\x00", (CLU_IP, 69))

blocks, expected = [], 1
while True:
    data, addr = ts.recvfrom(516)
    op = struct.unpack("!H", data[:2])[0]
    if op == 5:                                  # ERROR
        raise RuntimeError(data[4:].rstrip(b"\x00").decode())
    if op != 3:                                  # DATA
        continue
    blk = struct.unpack("!H", data[2:4])[0]
    if blk != expected:
        continue
    chunk = data[4:]
    blocks.append(chunk)
    ts.sendto(struct.pack("!HH", 4, blk), (CLU_IP, addr[1]))   # ACK
    if len(chunk) < 512:                         # last block
        break
    expected += 1
ts.close()

om_lua = b"".join(blocks).decode("utf-8")
# Feed to parse_om_lua() from om_lua_parser.py
```

Tested live against firmware 5.12.1-2330 — 144 KB `om.lua` downloaded in
~300 ms.

---

## Gotchas

1. **TFTP filename is case-sensitive.** `a:\om.lua` works; `A:\OM.LUA`,
   `OM.LUA`, `/om.lua` all return immediate `TFTP Error 0: TFTP Server Failure`.
2. **Unlock must precede every TFTP session.** The CLU appears to auto-relock
   after the transfer completes (or shortly after — not measured precisely).
   Send `req_start_ftp` again before each read.
3. **TFTP responses use a new ephemeral port (TID).** On Windows hosts,
   per-application firewall rules may drop responses from this new port even
   though the outbound RRQ went out fine. The unlock response on UDP 1234 works
   regardless because it comes from the same port we sent to. On Linux / inside
   the HA container this isn't an issue.
4. **Path requires `a:` prefix.** Plain `om.lua` triggers `TFTP Server Failure`.
   The `a:` is the CLU's internal filesystem root.
5. **Project key = mobile API key.** They are byte-for-byte identical. The
   `<projectCipherKey>` in `.omp/properties.xml` matches the
   `encryption.key/iv` returned by `/api/v1/interface/hash/{pin}/object`.

---

## What this means for the integration

| Before this discovery | Now |
|-----------------------|-----|
| User exports diagnostic ZIP and uploads it | Integration pulls `om.lua` directly from each CLU at setup |
| User re-uploads after every project change | Integration re-pulls on demand |
| Only the latest `om.lua` available, manual | Always live, automatic |
| `.omp` file parsing as fallback | Still useful for offline / unreachable CLUs |

`fetch_om_lua(clu_ip, encryption, clu_port=1234)` in
[clu_tftp.py](../custom_components/homeassistant_grenton/domain/api/clu_tftp.py)
is **implemented**: it opens a short-lived UDP socket, sends `req_start_ftp`
encrypted with the project key, validates `resp:OK`, then downloads `a:\om.lua`
via a fresh TFTP (RFC 1350) exchange. The TFTP DATA responses come from an
ephemeral source port (the TID), so the receiving socket must be unconnected
(Windows Firewall must allow inbound UDP for the process).

`config_flow.py` calls this for every CLU in the interface at setup time;
failures are non-fatal (fallback to internal Lua names).

---

## How we got here (decompilation trail)

For future-us, the sequence that exposed the protocol:

1. **`com/grenton/om/i/c/g.class`** — TFTP wrapper. Its private `a()` method
   shows the unlock sequence: get an `IConnection` for `Protocol.CLU_UDP`,
   send a `c` request (= `req_start_ftp`), check response, then
   `TFTPClient.receiveFile("a:\\om.lua", ...)`.
2. **`com/grenton/om/i/c/c.class`** — `Request` subclass for `req_start_ftp`.
   `getData()` returns `"req_start_ftp".getBytes()` — just the literal string,
   no header.
3. **`com/grenton/om/i/c/d.class`** — `ExpResponse` for the unlock. Validity
   check: `decryptedBytes.startsWith("resp:OK")`.
4. **`com/grenton/om/model/CLUCommand.class`** — Has two constructors:
   - `(String, boolean)` → payload + suffix, no header (the management form)
   - `(CommandHeader, byte[], CommandSuffix)` → `header:payload:suffix` (the Lua form)
5. **`com/grenton/om/commands/clu/G.class`** (`req_set_key`) — Shows
   management commands can carry binary headers
   (`{32 random}:{16 iv}:req_set_key:{32 new key}`), confirming the management
   handler doesn't require the `req:ip:hex:` prefix.
6. **`com/grenton/om/utils/m.a(byte[], byte[])`** — Factory key derivation:
   ```
   factory_key[i]   = cQ[i]   ^ box_code[i]      for i in 0..7
   factory_key[i+8] = cQ[i+8] ^ box_code[7-i]    for i in 0..7
   ```
   where `cQ = B9AFE387 F919A3D3 A0D79ADE 8E11A116`. The box code lives in
   `<privateKey>` inside `.omp/system.xml` (base64-encoded ASCII). Only useful
   for re-keying a factory-reset CLU — the active key is the project key from
   `properties.xml`.
7. **Empirical test** — Sent `encrypt(b"req_start_ftp")` to CLU:1234 with the
   project key. Got `resp:OK`. Six different framings worked (bare, with `\r\n`,
   with trailing `:`, with a binary header) — the management dispatcher just
   looks for the command keyword.

The `IConnection` implementation isn't in the JAR (it's referenced but not
present in `com.grenton.om_1.12.1.2516.jar`'s extracted contents), so the
exact wire format OM uses for `req_start_ftp` is unknown — but it doesn't
matter because the CLU accepts the simplest possible form: the command name
alone, encrypted with the project key.
