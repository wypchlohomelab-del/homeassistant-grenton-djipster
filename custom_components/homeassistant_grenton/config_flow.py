from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN
from .domain.api.omp_parser import OmpProject, parse_omp
from .domain.api.clu_tftp import fetch_om_lua, TftpError
from .domain.api.om_lua_parser import parse_om_lua
from .domain.encryption import GrentonEncryption
from .options_flow import GrentonOptionsFlow

_LOGGER = logging.getLogger(__name__)

STEP_OMP_SCHEMA = vol.Schema({
    vol.Required("omp_file"): selector.FileSelector(
        selector.FileSelectorConfig(accept=".omp")
    )
})


async def _fetch_objects(
    project: OmpProject,
    encryption: GrentonEncryption,
) -> dict[str, dict[str, Any]]:
    """Download om.lua from each CLU via TFTP and return per-CLU object maps.

    Non-fatal: CLUs that don't respond produce an empty object dict.
    """
    objects_per_clu: dict[str, dict[str, Any]] = {}
    for clu in project.clus:
        objects_per_clu[clu.id] = {}
        if not clu.ip:
            continue
        try:
            content = await fetch_om_lua(clu.ip, encryption, clu_port=clu.port)
            om_objects = parse_om_lua(content)
            objects_per_clu[clu.id] = {
                name: {
                    "project_name": obj.project_name,
                    "kind": obj.kind.value,
                    "type_id": obj.type_id,
                }
                for name, obj in om_objects.items()
            }
            _LOGGER.debug(
                "Fetched om.lua from CLU %s: %d objects discovered",
                clu.id, len(objects_per_clu[clu.id]),
            )
        except TftpError as e:
            _LOGGER.warning(
                "Could not fetch om.lua from CLU %s via TFTP "
                "(no objects will be mapped for this CLU): %s",
                clu.id, e,
            )
    return objects_per_clu


class GrentonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GrentonOptionsFlow:
        return GrentonOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            file_id = user_input["omp_file"]
            try:
                omp_bytes = await self.hass.async_add_executor_job(
                    _read_uploaded_file, self.hass, file_id
                )
                project = parse_omp(omp_bytes)
            except ValueError as e:
                _LOGGER.warning("Invalid .omp file: %s", e)
                errors["base"] = "invalid_omp"
            except Exception as e:
                _LOGGER.exception("Unexpected error parsing .omp: %s", e)
                errors["base"] = "unknown"
            else:
                if not project.clus:
                    errors["base"] = "no_clus"
                else:
                    return await self._create_entry(project)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_OMP_SCHEMA,
            errors=errors,
        )

    async def _create_entry(self, project: OmpProject) -> ConfigFlowResult:
        # Use the first CLU serial as the stable unique ID.
        unique_id = project.clus[0].serial_number
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        encryption = GrentonEncryption(key=project.key, iv=project.iv)
        objects_per_clu = await _fetch_objects(project, encryption)

        total_objects = sum(len(v) for v in objects_per_clu.values())
        _LOGGER.debug("Discovered %d objects across %d CLU(s)", total_objects, len(project.clus))

        clus = [
            {
                "id": c.id,
                "serial_number": c.serial_number,
                "name": c.name,
                "ip": c.ip,
                "port": c.port,
            }
            for c in project.clus
        ]

        title = project.clus[0].name or project.clus[0].ip or "Grenton"

        return self.async_create_entry(
            title=title,
            data={
                "key": project.key,
                "iv": project.iv,
                "clus": clus,
                "objects": objects_per_clu,
            },
        )


def _read_uploaded_file(hass: Any, file_id: str) -> bytes:
    """Read an uploaded file from the HA file upload store (runs in executor)."""
    with process_uploaded_file(hass, file_id) as file_path:
        return file_path.read_bytes()
