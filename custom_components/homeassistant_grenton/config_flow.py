from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN
from .domain.api.omp_parser import OmpProject, OmpInterface, parse_omp, build_interface_dict
from .domain.api.clu_tftp import fetch_om_lua, TftpError
from .domain.api.om_lua_parser import parse_om_lua, build_project_name_map
from .domain.encryption import GrentonEncryption
from .options_flow import GrentonOptionsFlow

_LOGGER = logging.getLogger(__name__)

STEP_OMP_SCHEMA = vol.Schema({
    vol.Required("omp_file"): selector.FileSelector(
        selector.FileSelectorConfig(accept=".omp")
    )
})


async def _fetch_object_names(
    interface_data: dict[str, Any],
    encryption: GrentonEncryption,
) -> dict[str, str]:
    """Download om.lua from each CLU via TFTP and build lua_name -> project_name map.

    Non-fatal: CLUs that don't respond fall back to Lua internal names.
    """
    per_clu: dict[str, dict] = {}
    for clu in interface_data.get("clus", []):
        clu_ip = clu.get("ip")
        clu_port = clu.get("port", 1234)
        clu_name = clu.get("name") or clu_ip
        if not clu_ip:
            continue
        try:
            content = await fetch_om_lua(clu_ip, encryption, clu_port=clu_port)
            per_clu[clu_name] = parse_om_lua(content)
            _LOGGER.debug(
                "Fetched om.lua from CLU %s: %d objects mapped",
                clu_name, len(per_clu[clu_name]),
            )
        except TftpError as e:
            _LOGGER.warning(
                "Could not fetch om.lua from CLU %s via TFTP "
                "(object names will use internal Lua identifiers): %s",
                clu_name, e,
            )
    return build_project_name_map(per_clu)


class GrentonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._project: OmpProject | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GrentonOptionsFlow:
        return GrentonOptionsFlow()

    # ── Step 1: upload .omp file ──────────────────────────────────────────────

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
                if not project.interfaces:
                    errors["base"] = "no_interfaces"
                elif len(project.interfaces) == 1:
                    self._project = project
                    return await self._create_entry(project.interfaces[0])
                else:
                    self._project = project
                    return await self.async_step_pick_interface()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_OMP_SCHEMA,
            errors=errors,
        )

    # ── Step 2 (conditional): pick interface when .omp has multiple ───────────

    async def async_step_pick_interface(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        project = self._project
        if project is None:
            return self.async_abort(reason="no_project")

        errors: dict[str, str] = {}

        if user_input is not None:
            node_id = user_input["interface_node_id"]
            chosen = next((i for i in project.interfaces if i.node_id == node_id), None)
            if chosen is None:
                errors["base"] = "unknown"
            else:
                return await self._create_entry(chosen)

        options = [
            selector.SelectOptionDict(
                value=iface.node_id,
                label=f"{iface.label} ({iface.widget_count} widgets, {iface.page_count} pages)",
            )
            for iface in sorted(project.interfaces, key=lambda i: -i.widget_count)
        ]

        return self.async_show_form(
            step_id="pick_interface",
            data_schema=vol.Schema({
                vol.Required("interface_node_id"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                )
            }),
            errors=errors,
        )

    # ── Common: TFTP fetch + create entry ────────────────────────────────────

    async def _create_entry(self, interface: OmpInterface) -> ConfigFlowResult:
        project = self._project
        if project is None:
            return self.async_abort(reason="no_project")

        interface_data = build_interface_dict(project, interface)

        project_uuid = interface.project_uuid or interface.node_id
        await self.async_set_unique_id(project_uuid)
        self._abort_if_unique_id_configured()

        encryption = GrentonEncryption(key=project.key, iv=project.iv)
        object_names = await _fetch_object_names(interface_data, encryption)
        _LOGGER.debug("Object name map: %d entries total", len(object_names))

        return self.async_create_entry(
            title=interface.label or project_uuid,
            data={
                "interface": interface_data,
                "object_names": object_names,
            },
        )


def _read_uploaded_file(hass: Any, file_id: str) -> bytes:
    """Read an uploaded file from the HA file upload store (runs in executor)."""
    with process_uploaded_file(hass, file_id) as file_path:
        return file_path.read_bytes()
