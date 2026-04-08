"""
Unit tests for EventBus core infrastructure.
Tests pub/sub, priority ordering, error isolation, and history.
"""

import asyncio
import sys
import os
import pytest

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.event_bus import EventBus, Event, EventTypes, EventPriority


class TestEventBus:
    """Test the EventBus pub/sub system."""

    def test_event_creation(self):
        """Events should have correct fields."""
        e = Event(type="TEST", payload={"key": "val"}, source="test")
        assert e.type == "TEST"
        assert e.payload["key"] == "val"
        assert e.source == "test"
        assert e.timestamp is not None
        assert e.event_id is not None

    def test_event_priority(self):
        """Critical events should have lower priority number (higher priority)."""
        critical = Event(type=EventTypes.SYSTEM_SHUTDOWN)
        data = Event(type=EventTypes.TICK_UPDATE)
        output = Event(type=EventTypes.SCAN_START)

        assert critical.priority < data.priority
        assert data.priority < output.priority

    def test_event_ordering(self):
        """Events should sort by priority for PriorityQueue."""
        e1 = Event(type=EventTypes.TICK_UPDATE)       # DATA=30
        e2 = Event(type=EventTypes.TRADE_EXECUTED)     # EXECUTION=10
        e3 = Event(type=EventTypes.SYSTEM_SHUTDOWN)    # CRITICAL=0

        events = sorted([e1, e2, e3])
        assert events[0].type == EventTypes.SYSTEM_SHUTDOWN
        assert events[1].type == EventTypes.TRADE_EXECUTED
        assert events[2].type == EventTypes.TICK_UPDATE

    def test_event_to_dict(self):
        """Event should serialize to dict."""
        e = Event(type="TEST", payload={"x": 1}, source="unit")
        d = e.to_dict()
        assert d["type"] == "TEST"
        assert d["payload"]["x"] == 1
        assert d["source"] == "unit"


class TestEventBusAsync:
    """Async tests for EventBus pub/sub."""

    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    def test_subscribe_and_publish(self):
        """Handler should receive published events."""
        received = []

        async def handler(event):
            received.append(event)

        async def run():
            bus = EventBus()
            bus.subscribe("TEST_EVENT", handler)
            await bus.start()

            await bus.publish(Event(type="TEST_EVENT", payload={"v": 42}))
            await asyncio.sleep(0.2)  # Let consumer process

            await bus.stop()
            return received

        result = asyncio.run(run())
        assert len(result) == 1
        assert result[0].payload["v"] == 42

    def test_wildcard_subscriber(self):
        """Wildcard subscribers should receive all events."""
        received = []

        async def handler(event):
            received.append(event)

        async def run():
            bus = EventBus()
            bus.subscribe_all(handler)
            await bus.start()

            await bus.publish(Event(type="A"))
            await bus.publish(Event(type="B"))
            await asyncio.sleep(0.3)

            await bus.stop()
            return received

        result = asyncio.run(run())
        assert len(result) == 2

    def test_error_isolation(self):
        """A failing handler should not crash the bus or block other handlers."""
        good_received = []

        async def bad_handler(event):
            raise ValueError("intentional error")

        async def good_handler(event):
            good_received.append(event)

        async def run():
            bus = EventBus()
            bus.subscribe("TEST", bad_handler)
            bus.subscribe("TEST", good_handler)
            await bus.start()

            await bus.publish(Event(type="TEST"))
            await asyncio.sleep(0.3)

            await bus.stop()
            assert bus.error_count >= 1
            assert len(bus.dead_letters) >= 1
            return good_received

        result = asyncio.run(run())
        assert len(result) == 1  # Good handler still received

    def test_event_history(self):
        """Bus should maintain event history."""
        async def run():
            bus = EventBus(history_size=10)
            await bus.start()

            for i in range(15):
                await bus.publish(Event(type="HIST", payload={"i": i}))

            await asyncio.sleep(0.5)
            await bus.stop()

            # Should only keep last 10 (ring buffer)
            assert len(bus.history) <= 10
            # Verify ring buffer works (oldest events are dropped)
            assert bus.event_count == 15

        asyncio.run(run())

    def test_event_count(self):
        """Bus should count events."""
        async def run():
            bus = EventBus()
            await bus.start()

            await bus.publish(Event(type="A"))
            await bus.publish(Event(type="B"))
            await bus.publish(Event(type="C"))
            await asyncio.sleep(0.3)

            await bus.stop()
            assert bus.event_count == 3

        asyncio.run(run())

    def test_unsubscribe(self):
        """Unsubscribed handlers should not receive events."""
        received = []

        async def handler(event):
            received.append(event)

        async def run():
            bus = EventBus()
            bus.subscribe("TEST", handler)
            await bus.start()

            await bus.publish(Event(type="TEST"))
            await asyncio.sleep(0.2)
            assert len(received) == 1

            bus.unsubscribe("TEST", handler)
            await bus.publish(Event(type="TEST"))
            await asyncio.sleep(0.2)

            await bus.stop()
            return received

        result = asyncio.run(run())
        assert len(result) == 1  # Only first event received


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
