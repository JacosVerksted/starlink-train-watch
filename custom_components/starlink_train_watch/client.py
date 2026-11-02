"""Rate-limited public CelesTrak client; observer coordinates stay local."""
from datetime import datetime, timedelta, timezone
import re
import aiohttp

GP_URL = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=last-30-days&FORMAT=JSON'
EPHEMERIS_URL = 'https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp'


def validate_orbits(data):
    keys = {'OBJECT_NAME', 'OBJECT_ID', 'NORAD_CAT_ID', 'EPOCH', 'MEAN_MOTION'}
    return isinstance(data, list) and bool(data) and all(isinstance(r, dict) and keys <= r.keys() for r in data)


def cache_usable(cache, now, hours=12):
    try:
        age = (now-datetime.fromisoformat(cache['fetched_at'])).total_seconds()
        return bool(cache.get('orbits')) and 0 <= age < hours*3600
    except (KeyError, ValueError, TypeError):
        return False


class OrbitClient:
    """Cache one recent-launch feed and immutable launch dates."""
    def __init__(self, session, cache=None):
        self.session = session
        self.cache = cache or {}
        self.last_error = None

    async def _json(self, url):
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=40)) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > 8_000_000:
                    raise ValueError('Orbital feed exceeds size limit')
                chunks.append(chunk)
            body = b''.join(chunks)
            import json
            return json.loads(body)

    async def snapshot(self, now):
        if cache_usable(self.cache, now, 6):
            return self.cache
        try:
            retry_at = self.cache.get('retry_at')
            if retry_at and now < datetime.fromisoformat(retry_at):
                raise ValueError('Feed retry cooldown active')
            orbits = await self._json(GP_URL)
            if not validate_orbits(orbits):
                raise ValueError('Invalid or empty orbital feed')
            dates = dict(self.cache.get('launch_dates', {}))
            representatives = {}
            for record in orbits:
                oid = record.get('OBJECT_ID', '')
                if record['OBJECT_NAME'].startswith('STARLINK-') and re.fullmatch(r'\d{4}-\d{3}[A-Z]+', oid):
                    representatives.setdefault(oid[:8], int(record['NORAD_CAT_ID']))
            missing = []
            for group, catalog_id in representatives.items():
                if group in dates:
                    continue
                try:
                    rows = await self._json(f'https://celestrak.org/satcat/records.php?CATNR={catalog_id}&FORMAT=JSON')
                    if not rows or rows[0].get('OBJECT_ID', '')[:8] != group:
                        raise ValueError('Launch metadata mismatch')
                    value = rows[0]['LAUNCH_DATE']
                    datetime.fromisoformat(value)
                    dates[group] = value
                except (aiohttp.ClientError, ValueError, KeyError, TypeError, TimeoutError):
                    missing.append(group)
                    # Avoid hammering a throttled SATCAT endpoint.
                    break
            self.cache = {'orbits': orbits, 'launch_dates': dates, 'fetched_at': now.isoformat(),
                          'missing_launch_groups': sorted(set(representatives)-dates.keys())}
            self.last_error = None
        except (aiohttp.ClientError, ValueError, KeyError, TypeError, TimeoutError) as error:
            self.last_error = type(error).__name__
            if not self.cache.get('retry_at') or now >= datetime.fromisoformat(self.cache['retry_at']):
                self.cache['retry_at'] = (now+timedelta(hours=2)).isoformat()
            if not cache_usable(self.cache, now):
                raise RuntimeError('Orbital feed unavailable and no fresh cache') from error
        return self.cache
