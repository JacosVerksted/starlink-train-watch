"""Starlink train viewing opportunities."""
import shutil

from homeassistant.helpers.storage import Store

from .const import DOMAIN, PLATFORMS
from .coordinator import TrainCoordinator

async def async_setup_entry(hass, entry):
    coordinator = TrainCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True

async def _reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    """Delete the orbit cache and the downloaded ephemeris when the integration is removed."""
    await Store(hass, 1, f'{DOMAIN}.data').async_remove()
    directory = hass.config.path('.storage', DOMAIN)
    await hass.async_add_executor_job(lambda: shutil.rmtree(directory, ignore_errors=True))
