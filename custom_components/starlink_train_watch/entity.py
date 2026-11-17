"""Shared entity metadata and boundary-driven updates."""
from datetime import datetime, timedelta, timezone
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.util import dt as dt_util
from .const import DOMAIN


class TrainEntity(CoordinatorEntity):
    """Base entity.

    Entities whose state depends on the clock (``_time_bound``) schedule a single
    timer for the next train start/end instead of writing state every 30 seconds.
    ``pending`` is computed once per state write (on a coordinator update or a
    boundary), not on every property access.
    """
    _attr_has_entity_name = True
    _time_bound = False
    _pending = None
    _now = None
    _unsub_boundary = None

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator)
        self._attr_unique_id = f'{entry.entry_id}_{key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
            name='Starlink Train Watch', entry_type=DeviceEntryType.SERVICE)

    def _compute(self):
        now = dt_util.utcnow()
        return now, [p for p in self.coordinator.data['passes'] if p['end'] > now]

    def _refresh(self):
        self._now, self._pending = self._compute()

    @property
    def pending(self):
        return self._pending if self._pending is not None else self._compute()[1]

    def _cancel_boundary(self):
        if self._unsub_boundary:
            self._unsub_boundary()
            self._unsub_boundary = None

    def _schedule(self):
        self._cancel_boundary()
        now = self._now
        edges = [p['start'] if p['start'] > now else p['end'] for p in self._pending]
        if edges:
            # Never schedule closer than one second to the real clock.
            due = max(min(edges), datetime.now(timezone.utc)) + timedelta(seconds=1)
            self._unsub_boundary = async_track_point_in_utc_time(self.hass, self._boundary, due)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._refresh()
        if self._time_bound:
            self._schedule()
        self.async_on_remove(self._cancel_boundary)

    @callback
    def _handle_coordinator_update(self):
        self._refresh()
        if self._time_bound:
            self._schedule()
        super()._handle_coordinator_update()

    @callback
    def _boundary(self, now):
        self._unsub_boundary = None
        self._refresh()
        self._schedule()
        self.async_write_ha_state()
