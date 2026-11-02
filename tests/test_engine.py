"""Prediction rules: synthetic fixtures are explicitly test-only."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys

p = Path(__file__).parents[1] / 'custom_components/starlink_train_watch/engine.py'
spec = importlib.util.spec_from_file_location('train_engine', p)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
if p.exists():
    spec.loader.exec_module(engine)

NOW = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)

def test_recent_groups_exclude_old_debris_and_missing_designators():
    records = [
        {'OBJECT_NAME': 'STARLINK-1', 'OBJECT_ID': '2026-230A'},
        {'OBJECT_NAME': 'STARLINK-2', 'OBJECT_ID': '2026-230B'},
        {'OBJECT_NAME': 'STARLINK-3', 'OBJECT_ID': '2026-230C'},
        {'OBJECT_NAME': 'STARLINK-4', 'OBJECT_ID': '2020-001A'},
        {'OBJECT_NAME': 'FALCON 9 DEB', 'OBJECT_ID': '2026-230D'},
        {'OBJECT_NAME': 'STARLINK-5', 'OBJECT_ID': ''},
    ]
    dates = {'2026-230': '2026-11-01', '2020-001': '2020-01-01'}
    groups = engine.recent_groups(records, dates, NOW, max_age_days=7)
    assert list(groups) == ['2026-230']
    assert len(groups['2026-230']) == 3


def test_compact_train_needs_three_distinct_satellites_on_similar_tracks():
    def sample(n, seconds, az=45):
        return {'satellite': n, 'peak': NOW + timedelta(seconds=seconds),
                'start': NOW + timedelta(seconds=seconds-20),
                'end': NOW + timedelta(seconds=seconds+20), 'azimuth': az,
                'max_elevation': 40, 'start_azimuth': az-20, 'end_azimuth': az+20}
    passes = [sample(1, 0), sample(2, 30), sample(3, 60), sample(4, 900), sample(5, 930)]
    result = engine.cluster_passes('2026-230', passes, max_span_seconds=120)
    assert len(result) == 1
    assert result[0]['satellite_count'] == 3
    assert result[0]['confidence'] == 'experimental'
    assert engine.cluster_passes('2026-230', [sample(1, 0), sample(1, 20), sample(1, 40)]) == []
    assert engine.cluster_passes('2026-230', [sample(1, 0), sample(2, 30, 180), sample(3, 60, 300)]) == []


def test_visibility_mask_rejects_daylight_shadow_low_elevation_and_stale_orbits():
    import numpy as np
    good = engine.visibility_mask(np.array([40, 40, 40, 10, 40]),
        np.array([-12, -2, -12, -12, -12]), np.array([True, True, False, True, True]),
        np.array([1, 1, 1, 1, 4]), min_elevation=25, max_sun_altitude=-6, max_epoch_days=3)
    assert good.tolist() == [True, False, False, False, False]


def test_future_launch_dates_and_unknown_launch_dates_are_excluded():
    records = [{'OBJECT_NAME': f'STARLINK-{i}', 'OBJECT_ID': f'2026-230{c}'} for i,c in enumerate('ABC')]
    assert engine.recent_groups(records, {}, NOW) == {}
    assert engine.recent_groups(records, {'2026-230': '2026-11-03'}, NOW) == {}


def test_prediction_runs_real_orbits_and_rejects_invalid_coordinates():
    import json
    import pytest
    root = Path(__file__).parents[1]
    records = json.loads((root / 'tests/fixtures/recent.json').read_text())
    dates = json.loads((root / 'tests/fixtures/launch_dates.json').read_text())
    with pytest.raises(ValueError):
        engine.predict(records, dates, NOW, 100, 6, 0, root / 'cache/de421.bsp')
    result = engine.predict(records, dates, NOW, 62.0, 6.0, 0, root / 'cache/de421.bsp', hours=24)
    assert result['groups_considered'] >= 1
    assert result['satellites_considered'] >= 3
    assert result['calculated_at'] == NOW
    for candidate in result['passes']:
        assert candidate['satellite_count'] >= 3
        assert candidate['max_elevation'] >= 25
        assert candidate['end'] > candidate['start']
        assert candidate['confidence'] == 'experimental'
