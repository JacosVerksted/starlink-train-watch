"""Read-only calendar of experimental viewing opportunities."""
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from .entity import TrainEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TrainCalendar(entry.runtime_data, entry)])


def as_event(item):
    return CalendarEvent(start=item['start'], end=item['end'], uid=item['id'],
        summary=f"Possible Starlink train ({item['satellite_count']} satellites)",
        description=f"Launch {item['launch_group']}. Look from {item['start_azimuth']}° to {item['end_azimuth']}°; max elevation {item['max_elevation']}°. Experimental geometric prediction; brightness and weather not guaranteed.")

class TrainCalendar(TrainEntity, CalendarEntity):
    _attr_name = 'Viewing opportunities'
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, 'calendar')
    @property
    def event(self):
        return as_event(self.pending[0]) if self.pending else None
    async def async_get_events(self, hass, start_date, end_date):
        return [as_event(p) for p in self.coordinator.data['passes'] if p['end'] > start_date and p['start'] < end_date]
