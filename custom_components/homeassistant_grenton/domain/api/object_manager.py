"""API client for Grenton Object Manager."""

import logging
from typing import Any
import aiohttp
import json

_LOGGER = logging.getLogger(__name__)


class GrentonObjectManagerApi:
    """Handles communication with Grenton Object Manager API."""
    
    def __init__(self, base_url: str):
        """Initialize the Object Manager API client.
        
        Args:
            base_url: Base URL of the Grenton Object Manager (e.g., 'http://192.168.1.100:9998')
        """
        self.base_url = base_url.rstrip('/')
        self.session: aiohttp.ClientSession | None = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb): # type: ignore
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def fetch_mobile_interface(self, pin: str) -> dict[str, Any]:
        """Fetch mobile interface data from Grenton Object Manager.

        Args:
            pin: PIN code for authentication (the token shown in OM's push dialog)

        Returns:
            Parsed mobile interface data as a dictionary

        Raises:
            GrentonObjectManagerAuthError: If authentication fails (wrong PIN)
            GrentonObjectManagerConnectionError: If connection fails
            GrentonObjectManagerDataError: If data is invalid
        """
        if not self.session:
            raise RuntimeError("API client not initialized. Use async context manager.")

        # /object returns JSON directly — no ZIP extraction needed
        url = f"{self.base_url}/api/v1/interface/hash/{pin.strip()}/object"
        _LOGGER.debug("Fetching OM interface: %s", url)

        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    try:
                        return json.loads(await response.text())
                    except json.JSONDecodeError as e:
                        raise GrentonObjectManagerDataError(f"Invalid JSON from OM: {e}")
                body_preview = (await response.text())[:200]
                if response.status in (400, 401):
                    raise GrentonObjectManagerAuthError(
                        f"Authentication failed (HTTP {response.status}): Invalid PIN — "
                        f"check OM's 'Send to Mobile Device' dialog for the current token "
                        f"(URL tried: {url})"
                    )
                raise GrentonObjectManagerConnectionError(
                    f"OM returned HTTP {response.status} for {url} — body: {body_preview!r}"
                )
        except aiohttp.ClientError as e:
            raise GrentonObjectManagerConnectionError(
                f"Connection error to {url}: {type(e).__name__}: {e}"
            )


class GrentonObjectManagerAuthError(Exception):
    """Raised when authentication with Grenton Object Manager fails."""
    pass


class GrentonObjectManagerConnectionError(Exception):
    """Raised when connection to Grenton Object Manager fails."""
    pass


class GrentonObjectManagerDataError(Exception):
    """Raised when interface data from Grenton Object Manager is invalid."""
    pass
