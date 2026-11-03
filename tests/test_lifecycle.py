"""Load the complete integration in isolated Home Assistant, without live messages."""
from pathlib import Path
from datetime import datetime, timezone
import json
import pytest

@pytest.mark.asyncio
@pytest.mark.parametrize("simulated", [False, True])
async def test_full_config_entry_load_and_unload(tmp_path, monkeypatch, simulated):
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.helpers.storage import Store
    from homeassistant.setup import async_setup_component
    root = Path(__file__).parents[1]
    (tmp_path/'custom_components').symlink_to(root/'custom_components', target_is_directory=True)
    eph_dir = tmp_path/'.storage/starlink_train_watch'
    eph_dir.mkdir(parents=True)
    (eph_dir/'de421.bsp').symlink_to(root/'cache/de421.bsp')
    hass = HomeAssistant(str(tmp_path))
    from homeassistant.helpers import frame
    frame.async_setup(hass)
    hass.config.latitude = 62.0
    hass.config.longitude = 6.0
    hass.config.time_zone = 'Europe/Oslo'
    hass.config_entries = ConfigEntries(hass, {})
    from homeassistant import loader
    loader.async_setup(hass)
    from homeassistant import auth
    from homeassistant.helpers import entity_registry, device_registry, area_registry
    device_registry.async_setup(hass)
    for registry in (area_registry, device_registry, entity_registry):
        await registry.async_load(hass)
    hass.auth = await auth.auth_manager_from_config(hass, [{'type': 'homeassistant'}], [])

    cache = {'fetched_at': datetime.now(timezone.utc).isoformat(),
        'orbits': json.loads((root/'tests/fixtures/recent.json').read_text()),
        'launch_dates': json.loads((root/'tests/fixtures/launch_dates.json').read_text()),
        'missing_launch_groups': []}
    if simulated:
        scenario = json.loads((root/'tests/fixtures/simulated_launch.json').read_text())
        from types import SimpleNamespace
        from custom_components.starlink_train_watch import (
            binary_sensor as binary_module,
            coordinator as coordinator_module,
            entity as entity_module,
        )
        calculation_time = datetime.fromisoformat(scenario['calculation_time'])
        # Predictions and entity expiry must share the synthetic launch clock.
        # Otherwise this test stops working once the fixture's passes have ended.
        for module in (coordinator_module, entity_module, binary_module):
            monkeypatch.setattr(module, 'dt_util', SimpleNamespace(utcnow=lambda:calculation_time))
        cache = {'fetched_at':calculation_time.isoformat(), 'orbits':scenario['records'],
            'launch_dates':scenario['launch_dates'], 'missing_launch_groups':[]}
    await Store(hass, 1, 'starlink_train_watch.data').async_save(cache)
    await async_setup_component(hass, 'network', {})
    await async_setup_component(hass, 'persistent_notification', {})
    result = await hass.config_entries.flow.async_init('starlink_train_watch', context={'source':'user'}, data={
        'min_elevation':25,'max_sun_altitude':-6,'max_age_days':7,'max_span_seconds':180})
    assert result['type'] == 'create_entry'
    entry = result['result']
    await hass.async_block_till_done()
    assert entry.state.value == 'loaded', (str(entry.as_dict()), repr(getattr(entry, 'runtime_data', None)), entry.reason)
    states = [s for s in hass.states.async_all() if s.entity_id.startswith(('sensor.starlink_train_watch_', 'binary_sensor.starlink_train_watch_', 'calendar.starlink_train_watch_'))]
    assert len(states) == 5
    assert all(s.state != 'unavailable' for s in states)
    await hass.async_start()
    if simulated:
        from datetime import timedelta
        from types import SimpleNamespace
        from custom_components.starlink_train_watch import binary_sensor as binary_module, entity as entity_module
        from homeassistant.helpers.template import Template
        from homeassistant.util import yaml
        next_id = 'sensor.starlink_train_watch_next_potential_train'
        visible_id = 'binary_sensor.starlink_train_watch_potential_train_visible'
        assert hass.states.get(next_id).attributes['satellite_count'] == 6
        candidates = entry.runtime_data.data['passes']
        assert len(candidates) == 3
        first = candidates[0]
        assert first['max_elevation'] >= 25
        assert hass.states.get(visible_id).state == 'off'
        # Render the actual blueprint condition/message in isolated HA.
        blueprint = yaml.load_yaml(str(root/'blueprints/automation/starlink_train_watch/notify.yaml'))
        helper = 'input_text.simulation_last_train'
        hass.states.async_set(helper, '')
        variables = {'train_sensor':next_id,'dedupe_helper':helper,'lead':10,'weather_entity':''}
        template = Template(blueprint['conditions'][0]['value_template'], hass)
        reminder_time = first['start']-timedelta(minutes=5)
        variables['now'] = lambda: reminder_time
        assert template.async_render(variables, parse_result=True) is True
        message = Template(blueprint['actions'][0]['variables']['starlink_message'], hass).async_render(variables)
        assert '6 satellites' in message and 'Experimental prediction' in message
        hass.states.async_set(helper, first['id'])
        assert template.async_render(variables, parse_result=True) is False
        variables['now'] = lambda: first['start']-timedelta(minutes=11)
        hass.states.async_set(helper, '')
        assert template.async_render(variables, parse_result=True) is False
        active_time = first['start']+timedelta(seconds=5)
        monkeypatch.setattr(binary_module,'dt_util',SimpleNamespace(utcnow=lambda:active_time))
        monkeypatch.setattr(entity_module,'dt_util',SimpleNamespace(utcnow=lambda:active_time))
        entry.runtime_data.async_set_updated_data(entry.runtime_data.data)
        await hass.async_block_till_done()
        assert hass.states.get(visible_id).state == 'on'
        ended_time = first['end']+timedelta(seconds=5)
        monkeypatch.setattr(binary_module,'dt_util',SimpleNamespace(utcnow=lambda:ended_time))
        monkeypatch.setattr(entity_module,'dt_util',SimpleNamespace(utcnow=lambda:ended_time))
        entry.runtime_data.async_set_updated_data(entry.runtime_data.data)
        await hass.async_block_till_done()
        assert hass.states.get(visible_id).state == 'off'
        assert hass.states.get(next_id).attributes['start'] == candidates[1]['start']
        report = {'warning':'SIMULATION ONLY — no real launch or message',
            'candidate_count':len(candidates), 'member_count':first['satellite_count'],
            'first_start_utc':str(first['start']), 'reminder_message_preview':message,
            'checks':['HA loaded five entities','six synthetic satellites recognised',
                'reminder eligible five minutes before','duplicate reminder suppressed',
                'no early reminder','visible sensor off/on/off','next pass advances after end']}
        (root/'simulation-results').mkdir(exist_ok=True)
        (root/'simulation-results/ha-report.json').write_text(json.dumps(report,indent=2))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop(force=True)
