"""Synthetic viewing data, never a real sighting or observer location."""
from datetime import datetime
import json
from pathlib import Path

from custom_components.starlink_train_watch.engine import predict

ROOT = Path(__file__).parents[1]


def test_prediction_exposes_member_details_and_honours_minimum():
    scenario = json.loads((ROOT/'tests/fixtures/simulated_launch.json').read_text())
    def calculate(**options):
        return predict(scenario['records'], scenario['launch_dates'],
            datetime.fromisoformat(scenario['calculation_time']), 62.0, 6.0, 0,
            ROOT/'cache/de421.bsp', **options)
    result = calculate(min_satellites=6)
    assert result['passes']
    first = result['passes'][0]
    assert first['launch_date'] == scenario['launch_dates'][first['launch_group']]
    assert first['satellite_count'] == len(first['satellites']) == 6
    assert len({s['norad_id'] for s in first['satellites']}) == 6
    names = {r['OBJECT_NAME'] for r in scenario['records']}
    for sat in first['satellites']:
        assert sat['name'] in names
        assert sat['object_id'].startswith(first['launch_group'])
        assert sat['start'] <= sat['peak'] <= sat['end']
        assert 0 <= sat['peak_azimuth'] < 360
        assert sat['max_elevation'] >= 25
        assert sat['sunlit_at_peak'] is True
        assert sat['sun_elevation_at_peak'] <= -6
        assert sat['range_km_at_peak'] > 0
        assert sat['altitude_km_at_peak'] > 0
        assert sat['orbital_epoch']
    assert calculate(min_satellites=7)['passes'] == []
    assert all(p['peak_spread_seconds'] <= 30 for p in calculate(max_span_seconds=30)['passes'])
