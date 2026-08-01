import asyncio

from extensions.events import EVENT_MESSAGE, EventBus, MessageEvent


def test_sync_and_async_handlers_both_receive_payload():
    bus = EventBus()
    received = []

    def sync_handler(payload):
        received.append(("sync", payload))

    async def async_handler(payload):
        await asyncio.sleep(0)
        received.append(("async", payload))

    bus.subscribe("x", sync_handler)
    bus.subscribe("x", async_handler)
    asyncio.run(bus.publish("x", {"n": 1}))
    assert received == [("sync", {"n": 1}), ("async", {"n": 1})]


def test_unsubscribe_removes_handler():
    bus = EventBus()
    received = []
    unsub = bus.subscribe("x", lambda payload: received.append(payload))
    unsub()
    asyncio.run(bus.publish("x", 1))
    assert received == []


def test_handler_exception_does_not_block_other_handlers():
    bus = EventBus()
    received = []

    def failing(payload):
        raise RuntimeError("boom")

    def ok(payload):
        received.append(payload)

    bus.subscribe("x", failing)
    bus.subscribe("x", ok)
    asyncio.run(bus.publish("x", 2))
    assert received == [2]


def test_message_event_fields():
    event = MessageEvent(
        platform="onebot11",
        chat_type="private",
        chat_id="10001",
        user_id="10001",
        content="你好",
        raw_content="你好",
        reply=lambda text: None,
    )
    assert event.platform == "onebot11"
    assert event.chat_id == "10001"
    assert event.is_at is False
    assert EVENT_MESSAGE == "message"
