"""Real atomic Redis admission scripts, shared by independent API/worker clients."""
import asyncio
import hashlib
import time
import unittest
from unittest.mock import AsyncMock, patch
from redis.exceptions import ConnectionError
from redis.asyncio import ConnectionPool
from fakeredis.aioredis import FakeAsyncRedisConnection
from arq.connections import ArqRedis
import test_screenshot_schedule as fixtures
from app import check_gate as gate

import pytest
pytest.importorskip('lupa', reason="fakeredis esegue gli script Lua solo con 'lupa' installato (tests/requirements.txt)")


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        server = self.redis.connection_pool.connection_kwargs['server']
        self.second = ArqRedis(pool_or_conn=ConnectionPool(connection_class=FakeAsyncRedisConnection, server=server))
        self.extra = [patch.object(gate.settings,'STATUS_CHECK_SERVER_PAUSE_SECONDS',0),
                      patch.object(gate.settings,'STATUS_CHECK_QUEUE_SECONDS',2),
                      patch.object(gate.settings,'STATUS_CHECK_CONCURRENCY',2)]
        for p in self.extra:p.start()

    async def asyncTearDown(self):
        for p in reversed(self.extra):p.stop()
        await self.second.aclose(close_connection_pool=True)
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def identify(self, redis, endpoint):
        return endpoint

    async def test_global_limit_is_shared_across_independent_clients(self):
        release = asyncio.Event(); two = asyncio.Event(); active=0; peak=0
        async def request(i):
            nonlocal active, peak
            async with gate.status_slot(str(i),1,redis=self.redis if i%2 else self.second,identify=self.identify):
                active+=1;peak=max(peak,active)
                if active==2:two.set()
                await release.wait();active-=1
        tasks=[asyncio.create_task(request(i)) for i in range(5)]
        try:
            await asyncio.wait_for(two.wait(),1)
            self.assertEqual(active,2)
            release.set();await asyncio.gather(*tasks)
            self.assertEqual(peak,2)
        finally:
            release.set();await asyncio.gather(*tasks,return_exceptions=True)

    async def test_two_domains_on_same_ip_are_serial_and_paced(self):
        entered=asyncio.Event();release=asyncio.Event();times=[]
        identify=AsyncMock(return_value='203.0.113.1')
        async def first():
            async with gate.status_slot('domain-a',1,redis=self.redis,identify=identify):
                entered.set();await release.wait()
        async def second():
            async with gate.status_slot('domain-b',1,redis=self.second,identify=identify):times.append(time.monotonic())
        with patch.object(gate.settings,'STATUS_CHECK_SERVER_PAUSE_SECONDS',0.04):
            a=asyncio.create_task(first());await entered.wait();b=asyncio.create_task(second())
            await asyncio.sleep(0.02);self.assertFalse(times)
            ended=time.monotonic();release.set();await asyncio.gather(a,b)
        self.assertGreaterEqual(times[0]-ended,0.035)

    async def test_cancellation_releases_global_and_server_claims(self):
        entered=asyncio.Event()
        async def request():
            async with gate.status_slot('server',1,redis=self.redis,identify=self.identify):
                entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(request());await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(await self.redis.zcard('status:active'),0)
        async with gate.status_slot('server',1,redis=self.second,identify=self.identify):pass

    async def test_optional_resources_defer_immediately_when_update_brake_is_held(self):
        await self.redis.set('srv:slot:server:0','1',ex=30)
        token=gate.queue_seconds.set(0)
        try:
            with self.assertRaises(gate.CheckDeferred):
                async with gate.status_slot('server',1,redis=self.redis,identify=self.identify):self.fail('request sent')
        finally:gate.queue_seconds.reset(token)
        self.assertEqual(await self.redis.zcard('status:active'),0)

    async def test_update_owner_can_refresh_without_deadlocking_on_its_own_brake(self):
        await self.redis.set('srv:slot:server:0','1',ex=30)
        token=gate.updating_server.set('server')
        try:
            async with gate.status_slot('server',1,redis=self.redis,identify=self.identify):pass
        finally:gate.updating_server.reset(token)

    async def test_release_does_not_delete_replacement_server_owner(self):
        key='status:server:'+hashlib.sha256(b'server').hexdigest()[:32]
        async with gate.status_slot('server',1,redis=self.redis,identify=self.identify):
            await self.redis.set(key,'replacement',px=10000)
        self.assertEqual(await self.redis.get(key),b'replacement')

    async def test_short_request_does_not_expire_global_long_request_claim(self):
        async with gate.status_slot('long',120,redis=self.redis,identify=self.identify):
            before=await self.redis.pttl('status:active')
            async with gate.status_slot('short',1,redis=self.second,identify=self.identify):
                after=await self.redis.pttl('status:active')
            self.assertGreater(after,140000);self.assertGreaterEqual(after,before-100)

    async def test_redis_failure_defers_instead_of_sending_uncontrolled_requests(self):
        r=type('Redis',(),{'eval':AsyncMock(side_effect=ConnectionError())})()
        with self.assertRaises(gate.CheckDeferred):
            async with gate.status_slot('server',1,redis=r,identify=self.identify):self.fail('request sent')
