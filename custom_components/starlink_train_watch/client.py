"""Rate-limited public CelesTrak client; observer coordinates stay local."""
from datetime import datetime, timedelta
import json
import re
import aiohttp

GP_URL = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=last-30-days&FORMAT=JSON'
EPHEMERIS_URL = 'https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp'
SATCAT_COOLDOWN = timedelta(minutes=45)


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
        self.dirty = False

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
            return json.loads(body)

    @staticmethod
    def _representatives(orbits):
        """Map each recent Starlink launch group to one catalogue number for SATCAT lookup."""
        representatives = {}
        for record in orbits:
            oid = record.get('OBJECT_ID', '')
            if record.get('OBJECT_NAME', '').startswith('STARLINK-') and re.fullmatch(r'\d{4}-\d{3}[A-Z]+', oid):
                representatives.setdefault(oid[:8], int(record['NORAD_CAT_ID']))
        return representatives

    async def _fill_launch_dates(self, now):
        """Look up launch dates still missing from the cache; stop at the first failure.

        A failed lookup sets a cooldown (stored in the cache) so a throttled SATCAT
        endpoint is not hammered, but missing dates are retried long before the
        6-hour orbit-feed refresh.
        """
        try:
            representatives = self._representatives(self.cache.get('orbits', []))
        except (KeyError, ValueError, TypeError):
            return
        dates = dict(self.cache.get('launch_dates', {}))
        missing = sorted(set(representatives) - dates.keys())
        retry_at = self.cache.get('satcat_retry_at')
        cooling = False
        try:
            cooling = bool(retry_at) and now < datetime.fromisoformat(retry_at)
        except ValueError:
            pass
        new_retry = None
        if missing and not cooling:
            for group in missing:
                try:
                    rows = await self._json(f'https://celestrak.org/satcat/records.php?CATNR={representatives[group]}&FORMAT=JSON')
                    if not rows or rows[0].get('OBJECT_ID', '')[:8] != group:
                        raise ValueError('Launch metadata mismatch')
                    value = rows[0]['LAUNCH_DATE']
                    datetime.fromisoformat(value)
                    dates[group] = value
                except (aiohttp.ClientError, ValueError, KeyError, TypeError, TimeoutError):
                    new_retry = (now + SATCAT_COOLDOWN).isoformat()
                    break
        still_missing = sorted(set(representatives) - dates.keys())
        changed = (dates != self.cache.get('launch_dates', {})
                   or still_missing != self.cache.get('missing_launch_groups')
                   or (new_retry is not None and new_retry != retry_at))
        if changed:
            self.cache['launch_dates'] = dates
            self.cache['missing_launch_groups'] = still_missing
            if new_retry:
                self.cache['satcat_retry_at'] = new_retry
            elif not still_missing:
                self.cache.pop('satcat_retry_at', None)
            self.dirty = True

    async def snapshot(self, now):
        """Return the cache, refreshing the orbit feed and missing launch dates as needed.

        ``self.dirty`` is set whenever the cache changed and should be persisted.
        """
        if cache_usable(self.cache, now, 6):
            await self._fill_launch_dates(now)
            return self.cache
        try:
            retry_at = self.cache.get('retry_at')
            if retry_at and now < datetime.fromisoformat(retry_at):
                raise ValueError('Feed retry cooldown active')
            orbits = await self._json(GP_URL)
            if not validate_orbits(orbits):
                raise ValueError('Invalid or empty orbital feed')
            self.cache = {'orbits': orbits, 'launch_dates': dict(self.cache.get('launch_dates', {})),
                          'fetched_at': now.isoformat(), 'missing_launch_groups': [],
                          'satcat_retry_at': self.cache.get('satcat_retry_at')}
            if not self.cache['satcat_retry_at']:
                del self.cache['satcat_retry_at']
            self.dirty = True
            await self._fill_launch_dates(now)
            self.last_error = None
        except (aiohttp.ClientError, ValueError, KeyError, TypeError, TimeoutError) as error:
            self.last_error = type(error).__name__
            if not self.cache.get('retry_at') or now >= datetime.fromisoformat(self.cache['retry_at']):
                self.cache['retry_at'] = (now+timedelta(hours=2)).isoformat()
                self.dirty = True
            if not cache_usable(self.cache, now):
                raise RuntimeError('Orbital feed unavailable and no fresh cache') from error
        return self.cache
