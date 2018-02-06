import pytest
from hedgehog.utils.test_utils import event_loop, assertTimeout, assertImmediate

import asyncio
import itertools
from aiostream import stream, pipe as p_
from contextlib import contextmanager

from hedgehog.utils.asyncio import repeat_func, repeat_func_eof, stream_from_queue, pipe
from hedgehog.utils.asyncio import Actor, ActorException


# Pytest fixtures
event_loop


async def assert_stream(expected, _stream):
    async with stream.enumerate(_stream).stream() as streamer:
        i = -1
        async for i, item in streamer:
            assert item == expected[i]
        assert i == len(expected) - 1


@contextmanager
def assert_actor_cleanup(actor):
    try:
        yield actor
    finally:
        with pytest.raises(AttributeError):
            actor.cmd_pipe


@pytest.mark.asyncio
async def test_repeat_func():
    with assertImmediate():
        await assert_stream(
            [0, 1, 2],
            repeat_func(itertools.count().__next__, 3))


@pytest.mark.asyncio
async def test_repeat_func_eof():
    with assertImmediate():
        await assert_stream(
            [0, 1, 2],
            repeat_func_eof(itertools.count().__next__, 3))


@pytest.mark.asyncio
async def test_stream_from_queue():
    with assertImmediate():
        queue = asyncio.Queue()
        await queue.put(0)

        async def put_next(item):
            await queue.put(item + 1)

        await assert_stream(
            [0, 1, 2],
            (stream_from_queue(queue) | p_.action(put_next))[:3])


@pytest.mark.asyncio
async def test_stream_from_queue_eof():
    with assertImmediate():
        EOF = object()
        queue = asyncio.Queue()
        await queue.put(3)

        async def put_next(item):
            await queue.put(item - 1 if item > 0 else EOF)

        await assert_stream(
            [3, 2, 1, 0],
            stream_from_queue(queue, EOF, use_is=True) | p_.action(put_next))


@pytest.mark.asyncio
async def test_pipe():
    a, b = pipe()

    task = asyncio.ensure_future(b.recv())
    await assertTimeout(task, 1, shield=True)
    with assertImmediate():
        await a.send("foo")
        assert await task == "foo"


@pytest.mark.asyncio
async def test_repeat_func_eof_task():
    a, b = pipe()

    async def run():
        async with stream.enumerate(repeat_func_eof(b.recv, 10)).stream() as streamer:
            i = -1
            async for i, msg in streamer:
                assert msg == i
            assert i == 9

    with assertImmediate():
        task = asyncio.ensure_future(run())
        for i in range(11):
            await a.send(i)
        await task


@pytest.mark.asyncio
async def test_actor():
    class MyActor(Actor):
        def __init__(self, greeting):
            super(MyActor, self).__init__()
            self.greeting = greeting

        async def greet(self, msg):
            await self.cmd_pipe.send((b'GREET', msg))
            return await self.cmd_pipe.recv()

        async def agreet(self, msg):
            await self.cmd_pipe.send((b'AGREET', msg))

        async def run(self, cmd_pipe, evt_pipe):
            await evt_pipe.send(b'$START')

            expected = [(b'GREET', "world"), (b'AGREET', "world")]

            async with stream.enumerate(repeat_func_eof(cmd_pipe.recv, b'$TERM')).stream() as streamer:
                i = -1
                async for i, cmd in streamer:
                    assert cmd == expected[i]
                    cmd, *payload = cmd

                    if cmd == b'GREET':
                        msg, = payload
                        await cmd_pipe.send(f"{self.greeting} {msg}")
                    elif cmd == b'AGREET':
                        msg, = payload
                        await evt_pipe.send((b'AGREET', f"{self.greeting} {msg}"))
                assert i == len(expected) - 1
            await evt_pipe.send((b'IGNORED',))

    with assertImmediate():
        with assert_actor_cleanup(MyActor("hello")) as a:
            async with a:
                assert await a.greet("world") == "hello world"
                await a.agreet("world")
                assert await a.evt_pipe.recv() == (b'AGREET', "hello world")


@pytest.mark.asyncio
async def test_faulty_actor():
    class FaultyActor(Actor):
        async def run(self, cmd_pipe, evt_pipe):
            pass

    with assertImmediate():
        with pytest.raises(ActorException):
            with assert_actor_cleanup(FaultyActor()) as a:
                async with a:
                    await a


@pytest.mark.asyncio
async def test_failing_actor():
    class MyException(Exception):
        pass

    class FailingActor(Actor):
        async def run(self, cmd_pipe, evt_pipe):
            await evt_pipe.send(b'$START')
            raise MyException()

    with assertImmediate():
        with pytest.raises(MyException):
            with assert_actor_cleanup(FailingActor()) as a:
                async with a:
                    await a


@pytest.mark.asyncio
async def test_init_failing_actor():
    class MyException(Exception):
        pass

    class InitFailingActor(Actor):
        async def run(self, cmd_pipe, evt_pipe):
            raise MyException()

    with assertImmediate():
        with pytest.raises(MyException):
            with assert_actor_cleanup(InitFailingActor()) as a:
                async with a:
                    await a


@pytest.mark.asyncio
async def test_actor_consume_term():
    class FailingActor(Actor):
        async def run(self, cmd_pipe, evt_pipe):
            await evt_pipe.send(b'$START')

    with assertImmediate():
        with assert_actor_cleanup(FailingActor()) as a:
            async with a:
                assert await a.evt_pipe.recv() == b'$TERM'


@pytest.mark.asyncio
async def test_actor_stop_early():
    class FailingActor(Actor):
        async def run(self, cmd_pipe, evt_pipe):
            await evt_pipe.send(b'$START')
            return "foo"

    with assertImmediate():
        with assert_actor_cleanup(FailingActor()) as a:
            async with a:
                assert await a.stop() == "foo"


@pytest.mark.asyncio
async def test_actor_receive_after_term():
    class AfterTerminationActor(Actor):
        async def run(self, cmd_pipe, evt_pipe):
            await evt_pipe.send(b'$START')
            assert await cmd_pipe.recv() == b'$TERM'
            await evt_pipe.send(b'AFTER_TERM')
            return "foo"

    with assertImmediate():
        with assert_actor_cleanup(AfterTerminationActor()) as a:
            async with a:
                assert await a.stop(block=False) is None
                assert await a.evt_pipe.recv() == b'AFTER_TERM'
                assert await a == "foo"
