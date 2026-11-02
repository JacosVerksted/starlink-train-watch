"""Next potential train and diagnostic feed status."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from .entity import TrainEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TrainNextPass(entry.runtime_data, entry), TrainStatus(entry.runtime_data, entry), TrainDataTime(entry.runtime_data, entry)])

class TrainNextPass(TrainEntity, SensorEntity):
    _attr_name = 'Next potential train'
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, 'next_train')
    @property
    def native_value(self):
        return self.pending[0]['start'] if self.pending else None
    @property
    def extra_state_attributes(self):
        return dict(self.pending[0]) if self.pending else {'note': 'No candidate in the prediction window; this is not a visibility guarantee.'}

class TrainStatus(TrainEntity, SensorEntity):
    _attr_name = 'Prediction status'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, 'status')
    @property
    def native_value(self):
        return self.coordinator.data['status']
    @property
    def extra_state_attributes(self):
        return {k:v for k,v in self.coordinator.data.items() if k != 'passes'} | {'candidate_count': len(self.pending)}

class TrainDataTime(TrainEntity, SensorEntity):
    _attr_name = 'Orbital data downloaded'
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, 'data_time')
    @property
    def native_value(self):
        return self.coordinator.data['fetched_at']
