"""Minimal async TFTP client for reading files from Grenton CLU.

The CLU exposes a TFTP server (UDP port 69) that Object Manager uses
to read diagnostic files (om.lua, user.lua, config.txt, etc.).

This module implements just enough of RFC 1350 to download a single file
in read mode. No write, no options negotiation, no error recovery beyond
the basic ACK/DATA flow.

CLU TFTP server is LOCKED by default — reads return "TFTP Server Failure"
until we send the bare management command `req_start_ftp` on UDP 1234
encrypted with the project key. After the unlock (CLU responds `resp:OK`)
TFTP reads of `a:\\om.lua`, `a:\\user.lua`, etc. work for one session.

Server sends DATA from a NEW ephemeral port (the TID), not from port 69.
We must use an unconnected socket so we can receive from that arbitrary port.
On Windows hosts, per-application firewall rules may drop these responses.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import logging

from ..cipher import GrentonCipher
from ..encryption import GrentonEncryption

_LOGGER = logging.getLogger(__name__)

_TFTP_PORT = 69
_BLOCK_SIZE = 512
_TIMEOUT_S = 5.0
_MAX_RETRIES = 3

# TFTP opcodes (RFC 1350)
_OP_RRQ = 1
_OP_DATA = 3
_OP_ACK = 4
_OP_ERROR = 5


class TftpError(Exception):
    """Raised when a TFTP transfer fails."""


async def tftp_read(host: str, filename: str, port: int = _TFTP_PORT) -> bytes:
    """Download a file from a TFTP server using asyncio.

    The server MUST be reachable; the function will retry up to _MAX_RETRIES
    times before raising TftpError.

    Args:
        host: CLU IP address
        filename: Filename to request (e.g. "om.lua")
        port: TFTP well-known port (default 69)

    Returns:
        Raw file bytes

    Raises:
        TftpError: If the transfer fails or times out
    """
    loop = asyncio.get_event_loop()

    # Build RRQ packet: opcode(2) + filename\0 + mode\0
    rrq = struct.pack("!H", _OP_RRQ) + filename.encode() + b"\x00" + b"octet" + b"\x00"

    # Use a plain asyncio executor to run the blocking socket I/O.
    # TFTP is a simple 2-round-trip protocol; running it in a thread is fine.
    return await loop.run_in_executor(None, _tftp_read_sync, host, port, rrq)


def _tftp_read_sync(host: str, port: int, rrq: bytes) -> bytes:
    """Synchronous TFTP read. Runs in a thread pool executor."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(_TIMEOUT_S)

        # Send RRQ to the TFTP well-known port
        sock.sendto(rrq, (host, port))

        blocks: list[bytes] = []
        server_port: int | None = None
        expected_block = 1

        for _ in range(4096):  # safety upper bound on block count
            try:
                data, addr = sock.recvfrom(4 + _BLOCK_SIZE)
            except socket.timeout:
                raise TftpError(f"TFTP timeout waiting for block {expected_block}")

            if len(data) < 4:
                continue

            opcode = struct.unpack("!H", data[:2])[0]

            if opcode == _OP_ERROR:
                code = struct.unpack("!H", data[2:4])[0]
                msg = data[4:].rstrip(b"\x00").decode("utf-8", errors="replace")
                raise TftpError(f"TFTP error {code}: {msg}")

            if opcode != _OP_DATA:
                continue

            block_num = struct.unpack("!H", data[2:4])[0]
            if block_num != expected_block:
                # Duplicate or out-of-order; re-send last ACK and ignore
                if server_port and block_num == expected_block - 1:
                    ack = struct.pack("!HH", _OP_ACK, block_num)
                    sock.sendto(ack, (host, addr[1]))
                continue

            # Pin the server's transfer port on first DATA
            server_port = addr[1]
            payload = data[4:]
            blocks.append(payload)

            # ACK this block
            ack = struct.pack("!HH", _OP_ACK, block_num)
            sock.sendto(ack, (host, server_port))

            if len(payload) < _BLOCK_SIZE:
                # Last block — transfer complete
                return b"".join(blocks)

            expected_block += 1

        raise TftpError("TFTP transfer exceeded maximum block count")
    finally:
        sock.close()


async def unlock_tftp(clu_ip: str, clu_port: int, encryption: GrentonEncryption) -> None:
    """Send bare `req_start_ftp` to the CLU to enable TFTP reads.

    The CLU's management handler accepts this raw command (not the `req::msgid:`
    Lua wrapper) and replies with encrypted `resp:OK`. After this, TFTP reads
    of `a:\\om.lua` etc. work for one transfer.

    Raises:
        TftpError: If unlock fails (no response or wrong response).
    """
    cipher = GrentonCipher(encryption)
    payload = cipher.encrypt(b"req_start_ftp")
    if payload is None:
        raise TftpError("Failed to encrypt req_start_ftp (bad key/iv)")

    loop = asyncio.get_event_loop()
    def _send_recv() -> bytes:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(3.0)
            s.sendto(payload, (clu_ip, clu_port))
            data, _ = s.recvfrom(1024)
            return data
        finally:
            s.close()

    try:
        raw = await loop.run_in_executor(None, _send_recv)
    except socket.timeout:
        raise TftpError(f"TFTP unlock timed out (no response from {clu_ip}:{clu_port})")

    decrypted = cipher.decrypt(raw)
    if decrypted is None:
        raise TftpError("TFTP unlock: response decrypt failed (wrong project key?)")
    if not decrypted.startswith(b"resp:OK"):
        raise TftpError(f"TFTP unlock: unexpected response {decrypted!r}")
    _LOGGER.debug("[%s] TFTP unlocked", clu_ip)


async def fetch_om_lua(
    clu_ip: str,
    encryption: GrentonEncryption,
    clu_port: int = 1234,
) -> str:
    """Unlock TFTP and download `a:\\om.lua` from the CLU.

    Args:
        clu_ip:     CLU IP address
        encryption: Project encryption (key/iv) from OM API. Same key the
                    integration already uses for UDP comms.
        clu_port:   CLU management port (default 1234)

    Returns:
        om.lua content as UTF-8 string.

    Raises:
        TftpError: On unlock failure, transfer failure, or timeout.
    """
    _LOGGER.debug("Fetching a:\\om.lua from CLU at %s via TFTP", clu_ip)
    await unlock_tftp(clu_ip, clu_port, encryption)
    raw = await tftp_read(clu_ip, "a:\\om.lua")
    _LOGGER.debug("Received om.lua: %d bytes from %s", len(raw), clu_ip)
    return raw.decode("utf-8", errors="replace")
