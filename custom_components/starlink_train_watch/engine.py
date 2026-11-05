"""Local, deterministic rules for experimental Starlink train predictions."""
from __future__ import annotations
from datetime import datetime, timezone
import re
import math


def recent_groups(records, launch_dates, now, max_age_days=7):
    """Select named payloads from launches with a verified recent launch date."""
    groups = {}
    for record in records:
        oid = record.get('OBJECT_ID', '')
        if not record.get('OBJECT_NAME', '').startswith('STARLINK-') or not re.fullmatch(r'\d{4}-\d{3}[A-Z]+', oid):
            continue
        group = oid[:8]
        try:
            launched = datetime.fromisoformat(launch_dates[group]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        age = (now - launched).total_seconds() / 86400
        if 0 <= age <= max_age_days:
            groups.setdefault(group, []).append(record)
    return {group: items for group, items in sorted(groups.items()) if len(items) >= 3}


def cluster_passes(group, passes, max_span_seconds=180, min_satellites=3):
    """Group similar tracks within a bounded peak-time span; no brightness claim."""
    remaining = sorted(passes, key=lambda p: p['peak'])
    results = []
    while remaining:
        first = remaining.pop(0)
        members = [first]
        rest = []
        for item in remaining:
            delta = (item['peak'] - first['peak']).total_seconds()
            az_delta = math.radians(item['azimuth'] - first['azimuth'])
            elevation_a = math.radians(item['max_elevation'])
            elevation_b = math.radians(first['max_elevation'])
            cosine = (math.sin(elevation_a) * math.sin(elevation_b)
                      + math.cos(elevation_a) * math.cos(elevation_b) * math.cos(az_delta))
            angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
            if delta <= max_span_seconds and angle <= 15 and abs(item['max_elevation'] - first['max_elevation']) <= 15:
                if item['satellite'] not in {m['satellite'] for m in members}:
                    members.append(item)
            else:
                rest.append(item)
        remaining = rest
        if len(members) < min_satellites:
            continue
        start = min(m['start'] for m in members)
        last = max(members, key=lambda m: m['end'])
        results.append({
            'id': f'{group}_{int(first["peak"].timestamp()) // 600}',
            'launch_group': group, 'start': start, 'end': last['end'],
            'peak': first['peak'], 'satellite_count': len(members),
            'start_azimuth': round(first['start_azimuth'], 1),
            'end_azimuth': round(last['end_azimuth'], 1),
            'max_elevation': round(max(m['max_elevation'] for m in members), 1),
            'peak_spread_seconds': round((members[-1]['peak'] - first['peak']).total_seconds()),
            'satellites': [dict(m.get('details', {'norad_id': m['satellite']})) for m in members],
            'confidence': 'experimental',
            'visibility_note': 'Geometric candidate only; brightness and cloud cover not guaranteed.',
        })
    return results


def visibility_mask(altitude, sun_altitude, sunlit, epoch_age_days, *,
                    min_elevation=25, max_sun_altitude=-6, max_epoch_days=3):
    """Reject unsafe extrapolations and geometrically invisible samples."""
    import numpy as np
    return (np.isfinite(altitude) & np.isfinite(sun_altitude)
            & (altitude >= min_elevation) & (sun_altitude <= max_sun_altitude)
            & sunlit & (np.abs(epoch_age_days) <= max_epoch_days))


def predict(records, launch_dates, now, latitude, longitude, elevation, ephemeris_path,
            *, hours=48, min_elevation=25, max_sun_altitude=-6,
            max_age_days=7, max_span_seconds=180, min_satellites=3):
    """Predict sampled visible windows, then cluster members of each launch.

    Ten-second sampling deliberately trades sub-second precision for bounded
    CPU use. A candidate is a geometric heuristic, never a brightness estimate.
    Call on an executor thread, never Home Assistant's event loop.
    """
    from datetime import timedelta
    import numpy as np
    from skyfield.api import EarthSatellite, load, load_file, wgs84
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid observing coordinates')
    if now.tzinfo is None or not (1 <= hours <= 72):
        raise ValueError('An aware time and a 1–72 hour horizon are required')
    if isinstance(min_satellites, bool) or not isinstance(min_satellites, int) or min_satellites < 3:
        raise ValueError('Minimum predicted satellites must be an integer of at least 3')
    groups = recent_groups(records, launch_dates, now, max_age_days)
    end = now + timedelta(hours=hours)
    result = {'passes': [], 'calculated_at': now, 'coverage_end': end,
              'groups_considered': len(groups), 'satellites_considered': 0,
              'stale_satellites': 0, 'invalid_satellites': 0}
    if not groups:
        return result
    ts = load.timescale(builtin=True)
    observer = wgs84.latlon(latitude, longitude, elevation_m=elevation)
    eph = load_file(str(ephemeris_path))
    try:
        station = eph['earth'] + observer
        t0 = ts.from_datetime(now - timedelta(minutes=15))
        t1 = ts.from_datetime(end)
        for group, members in groups.items():
            passes = []
            launch_limit = datetime.fromisoformat(launch_dates[group]).replace(tzinfo=timezone.utc) + timedelta(days=max_age_days)
            for record in members:
                try:
                    sat = EarthSatellite.from_omm(ts, record)
                    epoch_age = abs(float(ts.from_datetime(now) - sat.epoch))
                    if epoch_age > 3:
                        result['stale_satellites'] += 1
                        continue
                    result['satellites_considered'] += 1
                    times, events = sat.find_events(observer, t0, t1, altitude_degrees=min_elevation)
                    for index, event in enumerate(events):
                        if event != 1:
                            continue
                        left = times[index-1] if index and events[index-1] == 0 else t0
                        right = times[index+1] if index+1 < len(events) and events[index+1] == 2 else t1
                        count = max(3, int(float(right-left)*86400/10)+2)
                        samples = ts.tt_jd(np.linspace(left.tt, right.tt, count))
                        position = sat.at(samples)
                        alt, az, _ = (position - observer.at(samples)).altaz()
                        sun_alt, _, _ = station.at(samples).observe(eph['sun']).apparent().altaz()
                        good = visibility_mask(alt.degrees, sun_alt.degrees, position.is_sunlit(eph), samples-sat.epoch,
                                               min_elevation=min_elevation, max_sun_altitude=max_sun_altitude)
                        dates = samples.utc_datetime()
                        good &= np.array([d <= launch_limit for d in dates])
                        # Preserve disjoint sunlit/dark windows instead of joining gaps.
                        indices = np.flatnonzero(good)
                        for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1)+1):
                            if len(run) < 2:
                                continue
                            a, b = int(run[0]), int(run[-1])
                            if dates[b] <= now:
                                continue
                            peak = int(run[np.argmax(alt.degrees[run])])
                            peak_position = sat.at(samples[peak])
                            _, _, distance = (peak_position - observer.at(samples[peak])).altaz()
                            details = {
                                'name': record['OBJECT_NAME'], 'norad_id': int(record['NORAD_CAT_ID']),
                                'object_id': record['OBJECT_ID'], 'orbital_epoch': record['EPOCH'],
                                'start': dates[a], 'end': dates[b], 'peak': dates[peak],
                                'start_azimuth': round(float(az.degrees[a]), 1),
                                'end_azimuth': round(float(az.degrees[b]), 1),
                                'peak_azimuth': round(float(az.degrees[peak]), 1),
                                'max_elevation': round(float(alt.degrees[peak]), 1),
                                'sunlit_at_peak': bool(peak_position.is_sunlit(eph)),
                                'sun_elevation_at_peak': round(float(sun_alt.degrees[peak]), 1),
                                'range_km_at_peak': round(float(distance.km), 1),
                                'altitude_km_at_peak': round(float(wgs84.subpoint(peak_position).elevation.km), 1),
                            }
                            passes.append({'satellite': int(record['NORAD_CAT_ID']), 'details': details,
                                'start': dates[a], 'end': dates[b], 'peak': dates[peak],
                                'azimuth': float(az.degrees[peak]), 'max_elevation': float(alt.degrees[peak]),
                                'start_azimuth': float(az.degrees[a]), 'end_azimuth': float(az.degrees[b])})
                except (ValueError, KeyError, TypeError, OverflowError):
                    result['invalid_satellites'] += 1
            candidates = cluster_passes(group, passes, max_span_seconds, min_satellites)
            for candidate in candidates:
                candidate['launch_date'] = launch_dates[group]
            result['passes'].extend(candidates)
    finally:
        eph.close()
    result['passes'].sort(key=lambda item: item['start'])
    return result
