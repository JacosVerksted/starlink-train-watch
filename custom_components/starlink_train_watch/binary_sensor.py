"""Potential viewing window, not an optical detection."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.util import dt as dt_util
from .entity import TrainEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TrainVisible(entry.runtime_data, entry)])

class TrainVisible(TrainEntity, BinarySensorEntity):
    _attr_name = 'Potential train visible'
    _attr_icon = 'mdi:satellite-variant'
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, 'visible')
    @property
    def is_on(self):
        now = dt_util.utcnow()
        return any(p['start'] <= now < p['end'] for p in self.coordinator.data['passes'])
