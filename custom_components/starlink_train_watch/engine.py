"""Local, deterministic rules for experimental Starlink train predictions."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import re
import math

ID_MATCH_SECONDS = 300


class EphemerisError(ValueError):
    """The planetary ephemeris file could not be read (missing or corrupt)."""


def launch_window(launch_date, max_age_days):
    """Return (earliest, latest) UTC times a launch group is considered recent.

    SATCAT only gives a launch *date*. The earliest bound is the start of that
    UTC day (the launch cannot be earlier); the latest bound counts the age from
    the *end* of that day, so a late-evening launch keeps its full window.
    """
    start = datetime.fromisoformat(launch_date).replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1 + max_age_days)


def recent_groups(records, launch_dates, now, max_age_days=7, min_satellites=3):
    """Select named payloads from launches with a verified recent launch date.

    Groups with fewer than ``min_satellites`` members can never form a train and
    are skipped before the expensive orbit calculation.
    """
    groups = {}
    for record in records:
        oid = record.get('OBJECT_ID', '')
        if not record.get('OBJECT_NAME', '').startswith('STARLINK-') or not re.fullmatch(r'\d{4}-\d{3}[A-Z]+', oid):
            continue
        group = oid[:8]
        try:
            earliest, latest = launch_window(launch_dates[group], max_age_days)
        except (KeyError, ValueError, TypeError):
            continue
        if earliest <= now <= latest:
            groups.setdefault(group, []).append(record)
    return {group: items for group, items in sorted(groups.items()) if len(items) >= max(3, min_satellites)}


def _assign_ids(group, results, previous):
    """Give each train an ID that is unique in this result and stable across recalculations.

    A train keeps the ID of a previous train of the same launch group whose peak
    lies within ``ID_MATCH_SECONDS``. New trains get ``<group>_<peak epoch>``,
    with a counter suffix if two of them would collide.
    """
    available = [p for p in (previous or []) if p.get('launch_group') == group and p.get('id')]
    used = set()
    for result in sorted(results, key=lambda r: r['peak']):
        best = min((p for p in available if p['id'] not in used
                    and abs((p['peak'] - result['peak']).total_seconds()) <= ID_MATCH_SECONDS),
                   key=lambda p: abs((p['peak'] - result['peak']).total_seconds()), default=None)
        if best:
            result['id'] = best['id']
        else:
            base = f'{group}_{int(result["peak"].timestamp())}'
            candidate, counter = base, 1
            while candidate in used or any(p.get('id') == candidate for p in available):
                counter += 1
                candidate = f'{base}-{counter}'
            result['id'] = candidate
        used.add(result['id'])


def cluster_passes(group, passes, max_span_seconds=180, min_satellites=3, previous=None):
    """Group similar tracks within a bounded peak-time span; no brightness claim.

    Field meaning of each returned train: ``start``/``end`` are the earliest
    start and latest end of any member, and ``start_azimuth``/``end_azimuth``
    belong to those same members. ``peak`` is the peak of the member that peaks
    first and ``max_elevation`` is the highest elevation of any member.

    Known limits: members are compared with the first (anchor) pass only, so two
    members can differ by up to ~30 degrees; clustering is greedy, so an outlier
    anchor can break up a real train; a satellite seen twice inside one window
    is kept for a later cluster instead of being counted twice. ``previous`` is
    the earlier list of trains, used to keep IDs stable.
    """
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
            else:
                rest.append(item)
        remaining = rest
        if len(members) < min_satellites:
            continue
        earliest = min(members, key=lambda m: m['start'])
        last = max(members, key=lambda m: m['end'])
        results.append({
            'launch_group': group, 'start': earliest['start'], 'end': last['end'],
            'peak': first['peak'], 'satellite_count': len(members),
            'start_azimuth': round(earliest['start_azimuth'], 1),
            'end_azimuth': round(last['end_azimuth'], 1),
            'max_elevation': round(max(m['max_elevation'] for m in members), 1),
            'peak_spread_seconds': round((members[-1]['peak'] - first['peak']).total_seconds()),
            'satellites': [dict(m.get('details', {'norad_id': m['satellite']})) for m in members],
            'confidence': 'experimental',
            'visibility_note': 'Geometric candidate only; brightness and cloud cover not guaranteed.',
        })
    _assign_ids(group, results, previous)
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
            max_age_days=7, max_span_seconds=180, min_satellites=3, previous=None):
    """Predict sampled visible windows, then cluster members of each launch.

    Ten-second sampling deliberately trades sub-second precision for bounded
    CPU use. A candidate is a geometric heuristic, never a brightness estimate.
    Call on an executor thread, never Home Assistant's event loop.
    """
    import numpy as np
    from skyfield.api import EarthSatellite, load, load_file, wgs84
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid observing coordinates')
    if now.tzinfo is None or not (1 <= hours <= 72):
        raise ValueError('An aware time and a 1–72 hour horizon are required')
    if isinstance(min_satellites, bool) or not isinstance(min_satellites, int) or min_satellites < 3:
        raise ValueError('Minimum predicted satellites must be an integer of at least 3')
    groups = recent_groups(records, launch_dates, now, max_age_days, min_satellites)
    end = now + timedelta(hours=hours)
    result = {'passes': [], 'calculated_at': now, 'coverage_end': end,
              'groups_considered': len(groups), 'satellites_considered': 0,
              'stale_satellites': 0, 'invalid_satellites': 0}
    if not groups:
        return result
    ts = load.timescale(builtin=True)
    observer = wgs84.latlon(latitude, longitude, elevation_m=elevation)
    try:
        eph = load_file(str(ephemeris_path))
    except Exception as error:  # skyfield/jplephem raise assorted types on bad files
        raise EphemerisError(f'Cannot read ephemeris ({type(error).__name__})') from error
    try:
        station = eph['earth'] + observer
        t0 = ts.from_datetime(now - timedelta(minutes=15))
        t1 = ts.from_datetime(end)
        for group, members in groups.items():
            passes = []
            launch_limit = launch_window(launch_dates[group], max_age_days)[1]
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
            candidates = cluster_passes(group, passes, max_span_seconds, min_satellites, previous)
            for candidate in candidates:
                candidate['launch_date'] = launch_dates[group]
            result['passes'].extend(candidates)
    finally:
        eph.close()
    result['passes'].sort(key=lambda item: item['start'])
    return result
