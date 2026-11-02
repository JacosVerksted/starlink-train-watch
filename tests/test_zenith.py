"""Regression: azimuth alone is unstable close to the zenith."""
from datetime import datetime, timedelta, timezone
from custom_components.starlink_train_watch.engine import cluster_passes


def test_near_zenith_tracks_do_not_split_on_peak_azimuth():
    start = datetime(2026, 11, 2, 16, tzinfo=timezone.utc)
    passes = [dict(satellite=i+1, start=start+timedelta(seconds=10*i),
        end=start+timedelta(minutes=3, seconds=10*i),
        peak=start+timedelta(minutes=1, seconds=10*i),
        azimuth=az, max_elevation=89.0, start_azimuth=180.0, end_azimuth=0.0)
        for i, az in enumerate((0, 120, 240))]
    result = cluster_passes('2026-999', passes)
    assert len(result) == 1
    assert result[0]['satellite_count'] == 3
