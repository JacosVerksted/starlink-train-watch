"""Bounded network fetches and executor-based local orbit prediction."""
from datetime import datetime, timedelta
from functools import partial
import logging
from pathlib import Path
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .client import OrbitClient, EPHEMERIS_URL
from .const import DOMAIN, DEFAULTS
from .engine import predict

_LOGGER = logging.getLogger(__name__)

class TrainCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(minutes=30))
        self.entry = entry
        self.store = Store(hass, 1, f'{DOMAIN}.data')
        self.client = None
        self.ephemeris = Path(hass.config.path('.storage', DOMAIN, 'de421.bsp'))

    async def _ephemeris(self):
        if await self.hass.async_add_executor_job(self.ephemeris.is_file):
            return
        session = async_get_clientsession(self.hass)
        async with session.get(EPHEMERIS_URL, timeout=aiohttp.ClientTimeout(total=120)) as response:
            response.raise_for_status()
            data = await response.content.read(20_000_001)
            # StreamReader.read(n) may return fewer bytes than requested.
            chunks = [data]
            total = len(data)
            async for chunk in response.content.iter_chunked(65536):
                total += len(chunk)
                if total > 20_000_000:
                    raise ValueError('Ephemeris exceeds size limit')
                chunks.append(chunk)
            data = b''.join(chunks)
        if not data.startswith(b'DAF/SPK') or len(data) < 1_000_000:
            raise ValueError('Invalid ephemeris download')
        def save():
            self.ephemeris.parent.mkdir(parents=True, exist_ok=True)
            staged = self.ephemeris.with_suffix('.part')
            staged.write_bytes(data)
            staged.replace(self.ephemeris)
        await self.hass.async_add_executor_job(save)

    async def _async_update_data(self):
        now = dt_util.utcnow()
        try:
            if self.client is None:
                self.client = OrbitClient(async_get_clientsession(self.hass), await self.store.async_load())
            cache = await self.client.snapshot(now)
            await self.store.async_save(cache)
            await self._ephemeris()
            options = DEFAULTS | self.entry.data | self.entry.options
            result = await self.hass.async_add_executor_job(partial(predict,
                cache['orbits'], cache['launch_dates'], now,
                self.hass.config.latitude, self.hass.config.longitude, self.hass.config.elevation,
                self.ephemeris, **options))
            incomplete = bool(cache.get('missing_launch_groups'))
            if self.client.last_error:
                status = 'cached_feed'
            elif incomplete:
                status = 'partial_launch_metadata'
            elif result['stale_satellites'] or result['invalid_satellites']:
                status = 'partial_orbital_data'
            else:
                status = 'ready' if result['passes'] else 'no_candidates'
            return result | {'status': status, 'fetched_at': datetime.fromisoformat(cache['fetched_at']),
                'missing_launch_groups': cache.get('missing_launch_groups', []),
                'prediction_note': 'Experimental geometry only. Brightness, local horizon and clouds are not modelled.'}
        except (OSError, RuntimeError, ValueError, KeyError, aiohttp.ClientError, TimeoutError) as error:
            raise UpdateFailed(f'Prediction update failed ({type(error).__name__})') from error
