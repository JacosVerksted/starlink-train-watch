"""Shared entity metadata and time-bound updates."""
from datetime import timedelta
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.util import dt as dt_util
from .const import DOMAIN


class TrainEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator)
        self._attr_unique_id = f'{entry.entry_id}_{key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
            name='Starlink Train Watch', entry_type=DeviceEntryType.SERVICE)

    @property
    def pending(self):
        return [p for p in self.coordinator.data['passes'] if p['end'] > dt_util.utcnow()]

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(seconds=30)))

    @callback
    def _tick(self, now):
        self.async_write_ha_state()
