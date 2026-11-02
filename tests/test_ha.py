"""HA UI behavior; no household events or messages are generated."""
import importlib
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

@pytest.mark.asyncio
async def test_config_flow_defaults_to_home_and_validates_limits(tmp_path):
    from homeassistant.core import HomeAssistant
    hass = HomeAssistant(str(tmp_path))
    from homeassistant.config_entries import ConfigEntries
    hass.config_entries = ConfigEntries(hass, {})
    module = importlib.import_module('custom_components.starlink_train_watch.config_flow')
    flow = module.TrainConfigFlow()
    flow.hass = hass
    flow.context = {'source': 'user'}
    flow.handler = 'starlink_train_watch'
    form = await flow.async_step_user()
    assert form['type'] == 'form'
    bad = await flow.async_step_user({'min_elevation': 100, 'max_sun_altitude': -6, 'max_age_days': 7, 'max_span_seconds': 180})
    assert bad['errors']['base'] == 'invalid_options'
    good = await flow.async_step_user({'min_elevation': 25, 'max_sun_altitude': -6, 'max_age_days': 7, 'max_span_seconds': 180})
    assert good['type'] == 'create_entry'
    assert good['data']['min_elevation'] == 25
    await hass.async_stop()


def test_entities_distinguish_no_pass_from_feed_failure():
    from datetime import timedelta
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from homeassistant.core import is_callback
    from custom_components.starlink_train_watch.entity import TrainEntity
    assert is_callback(TrainEntity._tick), 'Timers must write state on the HA event loop'
    sensor = importlib.import_module('custom_components.starlink_train_watch.sensor')
    binary = importlib.import_module('custom_components.starlink_train_watch.binary_sensor')
    calendar = importlib.import_module('custom_components.starlink_train_watch.calendar')
    now = dt_util.utcnow()
    candidate = {'id': 'test-only', 'launch_group': '2026-230', 'start': now-timedelta(seconds=10),
        'end': now+timedelta(minutes=2), 'max_elevation': 45, 'satellite_count': 3,
        'start_azimuth': 10, 'end_azimuth': 100, 'confidence': 'experimental'}
    c = SimpleNamespace(data={'passes': [candidate], 'status': 'ready', 'fetched_at': now},
        last_update_success=True, config_entry=None)
    entry = SimpleNamespace(entry_id='test', runtime_data=c)
    assert sensor.TrainNextPass(c, entry).native_value == candidate['start']
    assert binary.TrainVisible(c, entry).is_on is True
    assert calendar.TrainCalendar(c, entry).event.start == candidate['start']
    c.data['passes'] = []
    c.data['status'] = 'no_candidates'
    assert sensor.TrainNextPass(c, entry).native_value is None
    assert sensor.TrainStatus(c, entry).native_value == 'no_candidates'
    assert binary.TrainVisible(c, entry).is_on is False
    c.last_update_success = False
    assert sensor.TrainNextPass(c, entry).available is False
