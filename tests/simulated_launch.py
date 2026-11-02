"""TEST ONLY: invented orbital elements, never a real launch forecast."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from skyfield.api import EarthSatellite, load, load_file, wgs84
from custom_components.starlink_train_watch.engine import predict

TARGET = datetime(2026, 11, 2, 16, 30, tzinfo=timezone.utc)
OBSERVER = (62.0, 6.0, 0)

def make_scenario(root):
    ts = load.timescale(builtin=True)
    t = ts.from_datetime(TARGET)
    observer = wgs84.latlon(*OBSERVER)
    base = {
        'OBJECT_NAME': 'STARLINK-SIMULATED-1', 'OBJECT_ID': '2026-999A',
        'NORAD_CAT_ID': 300001, 'EPOCH': '2026-11-02T16:30:00.000000',
        'MEAN_MOTION': 15.2, 'ECCENTRICITY': 0.0001, 'INCLINATION': 70.0,
        'RA_OF_ASC_NODE': 0.0, 'ARG_OF_PERICENTER': 0.0, 'MEAN_ANOMALY': 0.0,
        'EPHEMERIS_TYPE': 0, 'CLASSIFICATION_TYPE': 'U', 'ELEMENT_SET_NO': 1,
        'REV_AT_EPOCH': 1, 'BSTAR': 0.0, 'MEAN_MOTION_DOT': 0.0,
        'MEAN_MOTION_DDOT': 0.0,
    }
    eph = load_file(str(root/'cache/de421.bsp'))
    try:
        sun_alt = (eph['earth']+observer).at(t).observe(eph['sun']).apparent().altaz()[0].degrees
        assert sun_alt <= -6, sun_alt
        best = None
        for raan in range(0, 360, 5):
            for anomaly in range(0, 360, 5):
                record = base | {'RA_OF_ASC_NODE': float(raan), 'MEAN_ANOMALY': float(anomaly)}
                sat = EarthSatellite.from_omm(ts, record)
                pos = sat.at(t)
                altitude = (pos-observer.at(t)).altaz()[0].degrees
                if 35 <= altitude <= 65 and pos.is_sunlit(eph) and (best is None or abs(altitude-50) < abs(best[0]-50)):
                    best = (altitude, record)
        assert best is not None, 'No suitable synthetic sunlit geometry found'
        records = [best[1] | {'OBJECT_NAME': f'STARLINK-SIMULATED-{i+1}',
            'OBJECT_ID': '2026-999'+chr(65+i), 'NORAD_CAT_ID': 300001+i,
            'MEAN_ANOMALY': (best[1]['MEAN_ANOMALY']+i*0.6)%360} for i in range(6)]
    finally:
        eph.close()
    return {'warning':'SYNTHETIC TEST DATA — NOT A REAL LAUNCH OR FORECAST',
        'records':records, 'launch_dates':{'2026-999':'2026-11-02'},
        'calculation_time':TARGET-timedelta(minutes=15), 'sun_altitude_at_target':float(sun_alt)}


def run(root):
    scenario = make_scenario(root)
    result = predict(scenario['records'], scenario['launch_dates'], scenario['calculation_time'],
        *OBSERVER, root/'cache/de421.bsp', hours=2)
    assert result['passes'], result
    assert any(p['satellite_count']==6 for p in result['passes']), result
    output = root/'simulation-results'
    output.mkdir(exist_ok=True)
    (output/'scenario.json').write_text(json.dumps(scenario, default=str, indent=2))
    (output/'prediction.json').write_text(json.dumps(result, default=str, indent=2))
    print(json.dumps({'warning':scenario['warning'], 'sun_altitude_at_target':scenario['sun_altitude_at_target'],
        'result':result}, default=str, indent=2))

if __name__=='__main__':
    run(Path(__file__).parents[1])
