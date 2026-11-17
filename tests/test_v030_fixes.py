"""Regression tests for the v0.3.0 fixes (see CHANGELOG.md)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import voluptuous as vol

from custom_components.starlink_train_watch import engine
from custom_components.starlink_train_watch.client import OrbitClient

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)


def sample(n, seconds, az=45, start_az=None, base=NOW):
    return {'satellite': n, 'peak': base + timedelta(seconds=seconds),
            'start': base + timedelta(seconds=seconds - 20),
            'end': base + timedelta(seconds=seconds + 20), 'azimuth': az,
            'max_elevation': 40, 'start_azimuth': az - 20 if start_az is None else start_az,
            'end_azimuth': az + 20}


# --- 1 + 2: train IDs -------------------------------------------------------

def test_train_id_survives_peak_shift_across_old_slot_boundary():
    base = datetime(2026, 8, 18, 17, 9, 55, tzinfo=timezone.utc)  # 7 s before a :x0:00 slot edge
    first = engine.cluster_passes('2026-230', [sample(n, n * 10, base=base) for n in (1, 2, 3)])
    shifted_base = base + timedelta(seconds=7)
    second = engine.cluster_passes('2026-230', [sample(n, n * 10, base=shifted_base) for n in (1, 2, 3)],
                                   previous=first)
    assert [t['id'] for t in second] == [t['id'] for t in first]


def test_train_id_changes_for_a_later_pass_of_the_same_launch():
    first = engine.cluster_passes('2026-230', [sample(n, n * 10) for n in (1, 2, 3)])
    later = NOW + timedelta(minutes=95)
    second = engine.cluster_passes('2026-230', [sample(n, n * 10, base=later) for n in (1, 2, 3)],
                                   previous=first)
    assert second[0]['id'] != first[0]['id']


def test_two_clusters_in_one_old_slot_get_distinct_ids():
    passes = [sample(n, n * 10, az=45) for n in (1, 2, 3)]
    passes += [sample(n, 60 + n * 10, az=200) for n in (4, 5, 6)]
    result = engine.cluster_passes('2026-230', passes, max_span_seconds=300)
    assert len(result) == 2
    assert len({t['id'] for t in result}) == 2


# --- 6: field consistency -----------------------------------------------------

def test_start_azimuth_comes_from_the_member_with_the_earliest_start():
    early_start = sample(1, 60, start_az=111)   # peaks later but starts first
    early_start['start'] = NOW - timedelta(seconds=100)
    passes = [sample(2, 0, start_az=222), sample(3, 10, start_az=233), early_start]
    train = engine.cluster_passes('2026-230', passes)[0]
    assert train['start'] == NOW - timedelta(seconds=100)
    assert train['start_azimuth'] == 111


# --- 7: launch date is a whole UTC day ------------------------------------------

def test_launch_window_covers_whole_utc_day():
    records = [{'OBJECT_NAME': f'STARLINK-{i}', 'OBJECT_ID': f'2026-230{c}'} for i, c in enumerate('ABC')]
    dates = {'2026-230': '2026-11-01'}
    inside = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)   # 7.8 days after 00:00 UTC
    outside = datetime(2026, 11, 9, 1, 0, tzinfo=timezone.utc)
    assert engine.recent_groups(records, dates, inside, 7)
    assert not engine.recent_groups(records, dates, outside, 7)
    assert not engine.recent_groups(records, dates, datetime(2026, 10, 31, 23, tzinfo=timezone.utc), 7)


# --- 13: recent_groups honours min_satellites ----------------------------------

def test_recent_groups_skips_groups_that_can_never_qualify():
    records = [{'OBJECT_NAME': f'STARLINK-{i}', 'OBJECT_ID': f'2026-230{c}'} for i, c in enumerate('ABCD')]
    now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
    assert engine.recent_groups(records, {'2026-230': '2026-11-01'}, now, 7, min_satellites=4)
    assert not engine.recent_groups(records, {'2026-230': '2026-11-01'}, now, 7, min_satellites=5)


# --- 3 + 4: client retries and dirty flag -----------------------------------------

ORBITS = [{'OBJECT_NAME': 'STARLINK-1', 'OBJECT_ID': '2026-230A', 'NORAD_CAT_ID': 100001,
           'EPOCH': '2026-11-02T00:00:00', 'MEAN_MOTION': 15}]


class FakeClient(OrbitClient):
    def __init__(self, cache, satcat_ok=True):
        super().__init__(None, cache)
        self.urls = []
        self.satcat_ok = satcat_ok

    async def _json(self, url):
        self.urls.append(url)
        if 'satcat' not in url:
            raise AssertionError('GP feed must not be downloaded')
        if not self.satcat_ok:
            raise ValueError('SATCAT unavailable')
        return [{'OBJECT_ID': '2026-230A', 'LAUNCH_DATE': '2026-11-01'}]


def fresh_cache(now, **extra):
    return {'fetched_at': now.isoformat(), 'orbits': ORBITS, 'launch_dates': {},
            'missing_launch_groups': ['2026-230']} | extra


@pytest.mark.asyncio
async def test_missing_launch_date_is_filled_from_satcat_without_redownloading_feed():
    now = datetime.now(timezone.utc)
    client = FakeClient(fresh_cache(now))
    cache = await client.snapshot(now)
    assert cache['launch_dates'] == {'2026-230': '2026-11-01'}
    assert cache['missing_launch_groups'] == []
    assert len(client.urls) == 1 and 'satcat' in client.urls[0]
    assert client.dirty is True


@pytest.mark.asyncio
async def test_satcat_failure_sets_short_cooldown_then_retries():
    now = datetime.now(timezone.utc)
    client = FakeClient(fresh_cache(now), satcat_ok=False)
    cache = await client.snapshot(now)
    assert cache['missing_launch_groups'] == ['2026-230']
    assert len(client.urls) == 1
    await client.snapshot(now + timedelta(minutes=5))
    assert len(client.urls) == 1, 'cooldown must suppress immediate retries'
    client.satcat_ok = True
    cache = await client.snapshot(now + timedelta(minutes=50))
    assert len(client.urls) == 2
    assert cache['launch_dates'] == {'2026-230': '2026-11-01'}


@pytest.mark.asyncio
async def test_complete_fresh_cache_is_not_dirty():
    now = datetime.now(timezone.utc)
    client = FakeClient(fresh_cache(now, launch_dates={'2026-230': '2026-11-01'}, missing_launch_groups=[]))
    await client.snapshot(now)
    assert client.urls == []
    assert client.dirty is False


# --- 4: coordinator persistence ---------------------------------------------------

def make_coordinator(client, monkeypatch):
    from custom_components.starlink_train_watch import coordinator as module

    async def run(func, *args):
        return func(*args)

    saved = []

    class FakeStore:
        async def async_save(self, data):
            saved.append(data)

    monkeypatch.setattr(module, 'predict', lambda *a, **k: {
        'passes': [], 'stale_satellites': 0, 'invalid_satellites': 0})
    c = object.__new__(module.TrainCoordinator)
    c.hass = SimpleNamespace(async_add_executor_job=run,
                             config=SimpleNamespace(latitude=1, longitude=1, elevation=1))
    c.entry = SimpleNamespace(data={}, options={})
    c.store, c.client, c.data = FakeStore(), client, None
    c.ephemeris = Path('unused.bsp')
    async def no_ephemeris():
        return None
    c._ephemeris = no_ephemeris
    return c, saved


@pytest.mark.asyncio
async def test_unchanged_cache_is_not_rewritten_on_every_update(monkeypatch):
    now = datetime.now(timezone.utc)
    client = FakeClient(fresh_cache(now, launch_dates={'2026-230': '2026-11-01'}, missing_launch_groups=[]))
    c, saved = make_coordinator(client, monkeypatch)
    await c._async_update_data()
    await c._async_update_data()
    assert len(saved) == 0
    client.cache['launch_dates'] = {}
    client.cache['missing_launch_groups'] = ['2026-230']
    await c._async_update_data()   # fills the launch date -> one save
    await c._async_update_data()
    assert len(saved) == 1


# --- 5: ephemeris handling ----------------------------------------------------------

class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, chunks):
        self.content = FakeContent(chunks)

    def raise_for_status(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def ephemeris_coordinator(tmp_path, monkeypatch, chunks):
    from custom_components.starlink_train_watch import coordinator as module

    async def run(func, *args):
        return func(*args)

    monkeypatch.setattr(module, 'async_get_clientsession',
                        lambda hass: SimpleNamespace(get=lambda url, **kw: FakeResponse(chunks)))
    c = object.__new__(module.TrainCoordinator)
    c.hass = SimpleNamespace(async_add_executor_job=run)
    c.ephemeris = tmp_path / 'sub' / 'de421.bsp'
    return c


@pytest.mark.asyncio
async def test_ephemeris_size_limit_applies_to_every_chunk(tmp_path, monkeypatch):
    # Old code did content.read(20_000_001) first, which these fakes do not even offer.
    c = ephemeris_coordinator(tmp_path, monkeypatch, [b'DAF/SPK' + b'0' * 12_000_000] * 2)
    with pytest.raises(ValueError):
        await c._ephemeris()
    assert not c.ephemeris.exists()


@pytest.mark.asyncio
async def test_corrupt_ephemeris_file_is_replaced(tmp_path, monkeypatch):
    good = b'DAF/SPK' + b'0' * 1_500_000
    c = ephemeris_coordinator(tmp_path, monkeypatch, [good])
    c.ephemeris.parent.mkdir()
    c.ephemeris.write_bytes(b'garbage')
    await c._ephemeris()
    assert c.ephemeris.read_bytes() == good


@pytest.mark.asyncio
async def test_unloadable_ephemeris_is_deleted_so_next_update_redownloads(tmp_path, monkeypatch):
    from homeassistant.helpers.update_coordinator import UpdateFailed
    now = datetime.now(timezone.utc)
    client = FakeClient(fresh_cache(now, launch_dates={'2026-230': '2026-11-01'}, missing_launch_groups=[]))
    c, _ = make_coordinator(client, monkeypatch)
    from custom_components.starlink_train_watch import coordinator as module

    def broken(*args, **kwargs):
        raise engine.EphemerisError('cannot load')
    monkeypatch.setattr(module, 'predict', broken)
    c.ephemeris = tmp_path / 'de421.bsp'
    c.ephemeris.write_bytes(b'DAF/SPK' + b'0' * 1_500_000)
    with pytest.raises(UpdateFailed):
        await c._async_update_data()
    assert not c.ephemeris.exists()


def test_engine_reports_unreadable_ephemeris_as_ephemeris_error(tmp_path):
    scenario = json.loads((ROOT / 'tests/fixtures/simulated_launch.json').read_text())
    bad = tmp_path / 'de421.bsp'
    bad.write_bytes(b'not an ephemeris' * 100)
    now = datetime.fromisoformat(scenario['calculation_time'])
    with pytest.raises(engine.EphemerisError):
        engine.predict(scenario['records'], scenario['launch_dates'], now, 62.0, 6.0, 10, bad)


# --- 8: removal cleanup ------------------------------------------------------------

@pytest.mark.asyncio
async def test_removing_the_entry_deletes_store_and_ephemeris(tmp_path, monkeypatch):
    import custom_components.starlink_train_watch as integration
    removed = []

    class FakeStore:
        def __init__(self, hass, version, key):
            self.key = key

        async def async_remove(self):
            removed.append(self.key)

    async def run(func, *args):
        return func(*args)

    monkeypatch.setattr(integration, 'Store', FakeStore)
    directory = tmp_path / '.storage' / 'starlink_train_watch'
    directory.mkdir(parents=True)
    (directory / 'de421.bsp').write_bytes(b'x')
    hass = SimpleNamespace(async_add_executor_job=run,
                           config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))))
    await integration.async_remove_entry(hass, SimpleNamespace())
    assert removed == ['starlink_train_watch.data']
    assert not directory.exists()


# --- 9 + 12: entities ---------------------------------------------------------------

def test_satellite_list_is_not_recorded():
    from custom_components.starlink_train_watch.sensor import TrainNextPass
    assert 'satellites' in TrainNextPass._entity_component_unrecorded_attributes | \
        TrainNextPass._unrecorded_attributes


def _entity_fixture(monkeypatch):
    from custom_components.starlink_train_watch import entity as module
    from custom_components.starlink_train_watch import sensor
    calls = []
    clock = datetime.now(timezone.utc)
    monkeypatch.setattr(module, 'dt_util', SimpleNamespace(utcnow=lambda: calls.append(1) or clock))
    candidate = {'id': 'x', 'start': clock + timedelta(minutes=5), 'end': clock + timedelta(minutes=7),
                 'launch_group': '2026-230', 'satellites': []}
    c = SimpleNamespace(data={'passes': [candidate], 'status': 'ready', 'fetched_at': clock},
                        last_update_success=True, config_entry=None)
    entry = SimpleNamespace(entry_id='t', runtime_data=c)
    return sensor, c, entry, calls


def test_pending_is_computed_once_per_state_write(monkeypatch):
    sensor, c, entry, calls = _entity_fixture(monkeypatch)
    entity = sensor.TrainNextPass(c, entry)
    entity._refresh()
    calls.clear()
    entity.native_value, entity.extra_state_attributes, entity.native_value
    assert calls == []


def test_only_time_bound_entities_schedule_boundary_timers():
    from custom_components.starlink_train_watch import binary_sensor, calendar, sensor
    from homeassistant.core import is_callback
    from custom_components.starlink_train_watch.entity import TrainEntity
    assert sensor.TrainNextPass._time_bound and binary_sensor.TrainVisible._time_bound
    assert calendar.TrainCalendar._time_bound
    assert not sensor.TrainStatus._time_bound and not sensor.TrainDataTime._time_bound
    assert is_callback(TrainEntity._boundary)


# --- 11: config flow ----------------------------------------------------------------

def test_manifest_declares_single_config_entry():
    manifest = json.loads((ROOT / 'custom_components/starlink_train_watch/manifest.json').read_text())
    assert manifest['single_config_entry'] is True


def test_fractional_minimum_is_rejected_not_truncated():
    from custom_components.starlink_train_watch.config_flow import normalize, schema, valid
    from custom_components.starlink_train_watch.const import DEFAULTS
    assert not valid(normalize(DEFAULTS | {'min_satellites': 3.7}))
    assert normalize(DEFAULTS | {'min_satellites': 4.0})['min_satellites'] == 4
    assert valid(normalize(DEFAULTS | {'min_satellites': 4.0}))
    # The form must still be serialisable by the frontend and use whole-number boxes.
    import probatio
    from homeassistant.helpers import config_validation as cv
    rendered = probatio.to_field_list(schema(DEFAULTS), custom_serializer=cv.custom_serializer)
    assert {f['name'] for f in rendered} == set(DEFAULTS)


# --- 14: blueprint dedupe -------------------------------------------------------------

@pytest.mark.asyncio
async def test_blueprint_dedupes_on_group_and_start_time_tolerance(tmp_path):
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.template import Template
    from homeassistant.util import yaml
    blueprint = yaml.load_yaml(str(ROOT / 'blueprints/automation/starlink_train_watch/notify.yaml'))
    hass = HomeAssistant(str(tmp_path))
    try:
        start = datetime(2026, 11, 20, 17, 10, 2, tzinfo=timezone.utc)
        sensor, helper = 'sensor.next', 'input_text.last'
        hass.states.async_set(sensor, start.isoformat(),
                              {'id': 'new-id', 'launch_group': '2026-230'})
        variables = {'train_sensor': sensor, 'dedupe_helper': helper, 'lead': 10, 'weather_entity': '',
                     'now': lambda: start - timedelta(minutes=5)}
        template = Template(blueprint['conditions'][0]['value_template'], hass)
        render = lambda: template.async_render(variables, parse_result=True)
        hass.states.async_set(helper, '')
        assert render() is True
        # Same train, recomputed 7 s earlier and with a different engine id -> still deduplicated.
        hass.states.async_set(helper, f"2026-230|{(start - timedelta(seconds=7)).isoformat()}")
        assert render() is False
        # Same launch group, a pass ~95 minutes later is a different train.
        hass.states.async_set(helper, f"2026-230|{(start - timedelta(minutes=95)).isoformat()}")
        assert render() is True
        # Another launch at the same time is a different train.
        hass.states.async_set(helper, f"2026-231|{start.isoformat()}")
        assert render() is True
        action = blueprint['actions'][-1]['data']['value']
        assert 'launch_group' in action or 'train_key' in action
    finally:
        await hass.async_stop(force=True)
