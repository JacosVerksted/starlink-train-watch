"""Public feed caching and validation."""
import importlib
from datetime import datetime, timedelta, timezone
import pytest

@pytest.mark.asyncio
async def test_cached_snapshot_avoids_network_and_invalid_feed_is_rejected():
    module = importlib.import_module('custom_components.starlink_train_watch.client')
    now = datetime.now(timezone.utc)
    cache = {'fetched_at': now.isoformat(), 'orbits': [{'OBJECT_NAME':'STARLINK-1'}], 'launch_dates': {}}
    client = module.OrbitClient(None, cache)
    assert (await client.snapshot(now))['orbits'] == cache['orbits']
    assert module.validate_orbits([{'error':'rate limited'}]) is False
    assert module.validate_orbits([]) is False
    assert module.validate_orbits([{'OBJECT_NAME':'STARLINK-1','NORAD_CAT_ID':100001,'OBJECT_ID':'2026-230A','EPOCH':'2026-11-02T00:00:00','MEAN_MOTION':15}]) is True
    assert module.cache_usable(cache, now+timedelta(hours=13)) is False


@pytest.mark.asyncio
async def test_json_reads_all_streamed_chunks():
    import asyncio
    import aiohttp
    from aiohttp import web
    from custom_components.starlink_train_watch.client import OrbitClient
    async def handler(request):
        response = web.StreamResponse(headers={'Content-Type': 'application/json'})
        await response.prepare(request)
        await response.write(b'[{"value":')
        await asyncio.sleep(0.02)
        await response.write(b'123}]')
        await response.write_eof()
        return response
    app = web.Application()
    app.router.add_get('/', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            assert await OrbitClient(session)._json(f'http://127.0.0.1:{port}/') == [{'value':123}]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_feed_failure_uses_only_fresh_cache_and_cooldown():
    from custom_components.starlink_train_watch.client import OrbitClient
    now = datetime.now(timezone.utc)
    class Unavailable(OrbitClient):
        calls = 0
        async def _json(self, url):
            self.calls += 1
            raise ValueError('Upstream unavailable')
    cache = {'fetched_at':(now-timedelta(hours=7)).isoformat(), 'orbits':[{'OBJECT_NAME':'STARLINK-1'}], 'launch_dates':{}}
    client = Unavailable(None, cache)
    assert await client.snapshot(now) == cache
    await client.snapshot(now+timedelta(minutes=1))
    assert client.calls == 1
    with pytest.raises(RuntimeError):
        await client.snapshot(now+timedelta(hours=6))
