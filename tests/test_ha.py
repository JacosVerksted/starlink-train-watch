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


def test_minimum_count_setup_schema_and_validation():
    from custom_components.starlink_train_watch.config_flow import schema, valid
    from custom_components.starlink_train_watch.const import DEFAULTS
    assert DEFAULTS['min_satellites'] == 3
    assert schema(DEFAULTS)({})['min_satellites'] == 3
    assert schema(DEFAULTS | {'min_satellites': 10})({})['min_satellites'] == 10
    assert valid(DEFAULTS | {'min_satellites': 100})
    assert valid({k:v for k,v in DEFAULTS.items() if k != 'min_satellites'})
    for bad in (0, 2, -1, 3.5, True, 'invalid'):
        assert not valid(DEFAULTS | {'min_satellites': bad})


def test_setup_and_options_explain_count_no_cap_and_compactness():
    import json
    root = Path(__file__).parents[1]/'custom_components/starlink_train_watch'
    for filename in ('strings.json', 'translations/en.json'):
        text = json.loads((root/filename).read_text())
        for section, step in (('config', 'user'), ('options', 'init')):
            form = text[section]['step'][step]
            assert 'min_satellites' in form['data']
            assert '10' in form['data_description']['min_satellites']
            assert '60' in form['data_description']['max_span_seconds']
            assert 'no fixed maximum' in form['description'].lower()


def test_calendar_lists_satellite_names_and_local_peak_times():
    from datetime import datetime, timedelta, timezone
    from custom_components.starlink_train_watch.calendar import as_event
    now = datetime(2026, 11, 5, 17, 7, tzinfo=timezone.utc)
    candidate = {'id':'synthetic-calendar', 'launch_group':'2026-999',
        'launch_date':'2026-11-04', 'start':now, 'end':now+timedelta(minutes=2),
        'satellite_count':3, 'start_azimuth':263, 'end_azimuth':221, 'max_elevation':28,
        'satellites':[{'name':'STARLINK-TEST-1','norad_id':12345,'peak':now,
                       'max_elevation':28,'peak_azimuth':235}]}
    event = as_event(candidate)
    assert 'STARLINK-TEST-1' in event.description
    assert '12345' in event.description
    assert '2026-11-04' in event.description
    assert '235' in event.description
    assert 'brightness' in event.description.lower()


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
