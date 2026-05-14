"""
EventBus — Central nervous system for inter-service communication.

Features:
  - Async pub/sub with priority queues
  - Typed events with validation
  - Dead-letter handling for failed handlers
  - Ring-buffer event history (last 1000) for debugging
  - JSON-lines event log for replay/post-mortem analysis
"""

import asyncio
import json
import os
import logging
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger("EventBus")


# ─── Event Priority ──────────────────────────────────────────────────────────

class EventPriority(IntEnum):
    """Lower number = higher priority (processed first)."""
    CRITICAL   = 0   # System shutdown, circuit breakers
    EXECUTION  = 10  # Trade execution, position modifications
    ANALYSIS   = 20  # Quant signals, regime updates
    DATA       = 30  # Market data, ticks
    OUTPUT     = 40  # UI broadcast, telegram, journaling


# ─── Event Type Constants ────────────────────────────────────────────────────

class EventTypes:
    """Central registry of all event type strings."""
    # Data
    TICK_UPDATE        = "TICK_UPDATE"
    CANDLE_READY       = "CANDLE_READY"
    MARKET_DATA_READY  = "MARKET_DATA_READY"
    NEWS_UPDATE        = "NEWS_UPDATE"
    NEWS_TRADE_SIGNAL  = "NEWS_TRADE_SIGNAL"
    NEWS_STRADDLE_REQUEST = "NEWS_STRADDLE_REQUEST"

    # Session Regime
    SESSION_REGIME_UPDATE  = "SESSION_REGIME_UPDATE"
    SESSION_TRADE_SIGNAL   = "SESSION_TRADE_SIGNAL"
    MACRO_FILTER_UPDATE    = "MACRO_FILTER_UPDATE"
    SWEEP_TRADE_SIGNAL     = "SWEEP_TRADE_SIGNAL"
    FLAT_ALL_POSITIONS     = "FLAT_ALL_POSITIONS"

    # Risk Management
    DAILY_LOSS_LIMIT_HIT   = "DAILY_LOSS_LIMIT_HIT"
    KILL_SWITCH_ACTIVATED  = "KILL_SWITCH_ACTIVATED"

    # Analysis
    QUANT_SIGNAL       = "QUANT_SIGNAL"
    REGIME_UPDATE      = "REGIME_UPDATE"
    SENTIMENT_UPDATE   = "SENTIMENT_UPDATE"
    FLOW_UPDATE        = "FLOW_UPDATE"
    PATTERN_UPDATE     = "PATTERN_UPDATE"

    # Decision
    TRADE_CANDIDATE    = "TRADE_CANDIDATE"
    TRADE_APPROVED     = "TRADE_APPROVED"
    TRADE_REJECTED     = "TRADE_REJECTED"

    # Execution
    TRADE_EXECUTED     = "TRADE_EXECUTED"
    TRADE_FAILED       = "TRADE_FAILED"
    POSITION_MODIFIED  = "POSITION_MODIFIED"
    POSITION_CLOSED    = "POSITION_CLOSED"

    # System
    SCAN_START         = "SCAN_START"
    SCAN_COMPLETE      = "SCAN_COMPLETE"
    SERVICE_ERROR      = "SERVICE_ERROR"
    SERVICE_STARTED    = "SERVICE_STARTED"
    SERVICE_STOPPED    = "SERVICE_STOPPED"
    SYSTEM_SHUTDOWN    = "SYSTEM_SHUTDOWN"

    # Performance Analytics
    PERFORMANCE_ALERT       = "PERFORMANCE_ALERT"
    PERFORMANCE_SIZE_UPDATE = "PERFORMANCE_SIZE_UPDATE"

    # Trade Quality
    TRADE_QUALITY_GRADED    = "TRADE_QUALITY_GRADED"

    # Dashboard-compatible (backward compat)
    ACCOUNT_UPDATE     = "ACCOUNT_UPDATE"
    POSITION_UPDATE    = "POSITION_UPDATE"
    SCAN_SUMMARY       = "SCAN_SUMMARY"
    TRADE_EXECUTION    = "TRADE_EXECUTION"
    STAT_ARB_HEDGE     = "STAT_ARB_HEDGE"
    STAT_ARB_FLATTEN   = "STAT_ARB_FLATTEN"


# ─── Priority mapping ────────────────────────────────────────────────────────

_EVENT_PRIORITY: Dict[str, EventPriority] = {
    EventTypes.SYSTEM_SHUTDOWN:    EventPriority.CRITICAL,
    EventTypes.SERVICE_ERROR:      EventPriority.CRITICAL,

    EventTypes.TRADE_APPROVED:     EventPriority.EXECUTION,
    EventTypes.TRADE_EXECUTED:     EventPriority.EXECUTION,
    EventTypes.TRADE_FAILED:       EventPriority.EXECUTION,
    EventTypes.POSITION_MODIFIED:  EventPriority.EXECUTION,
    EventTypes.POSITION_CLOSED:    EventPriority.EXECUTION,
    EventTypes.TRADE_EXECUTION:    EventPriority.EXECUTION,

    EventTypes.QUANT_SIGNAL:       EventPriority.ANALYSIS,
    EventTypes.REGIME_UPDATE:      EventPriority.ANALYSIS,
    EventTypes.SENTIMENT_UPDATE:   EventPriority.ANALYSIS,
    EventTypes.FLOW_UPDATE:        EventPriority.ANALYSIS,
    EventTypes.TRADE_CANDIDATE:    EventPriority.ANALYSIS,
    EventTypes.TRADE_REJECTED:     EventPriority.ANALYSIS,

    EventTypes.TICK_UPDATE:        EventPriority.DATA,
    EventTypes.CANDLE_READY:       EventPriority.DATA,
    EventTypes.MARKET_DATA_READY:  EventPriority.DATA,
    EventTypes.NEWS_UPDATE:        EventPriority.DATA,
    EventTypes.NEWS_TRADE_SIGNAL:  EventPriority.ANALYSIS,
    EventTypes.NEWS_STRADDLE_REQUEST: EventPriority.EXECUTION,

    # Session Regime
    EventTypes.SESSION_REGIME_UPDATE: EventPriority.ANALYSIS,
    EventTypes.SESSION_TRADE_SIGNAL:  EventPriority.ANALYSIS,
    EventTypes.MACRO_FILTER_UPDATE:   EventPriority.DATA,
    EventTypes.SWEEP_TRADE_SIGNAL:    EventPriority.ANALYSIS,
    EventTypes.FLAT_ALL_POSITIONS:    EventPriority.EXECUTION,
    EventTypes.DAILY_LOSS_LIMIT_HIT:  EventPriority.CRITICAL,
    EventTypes.KILL_SWITCH_ACTIVATED: EventPriority.CRITICAL,

    # Performance Analytics
    EventTypes.PERFORMANCE_ALERT:       EventPriority.ANALYSIS,
    EventTypes.PERFORMANCE_SIZE_UPDATE: EventPriority.ANALYSIS,

    # Trade Quality
    EventTypes.TRADE_QUALITY_GRADED:    EventPriority.ANALYSIS,

    EventTypes.SCAN_START:         EventPriority.OUTPUT,
    EventTypes.SCAN_COMPLETE:      EventPriority.OUTPUT,
    EventTypes.SCAN_SUMMARY:       EventPriority.OUTPUT,
    EventTypes.ACCOUNT_UPDATE:     EventPriority.OUTPUT,
    EventTypes.POSITION_UPDATE:    EventPriority.OUTPUT,
}


# ─── Event ────────────────────────────────────────────────────────────────────

@dataclass
class Event:
    """Immutable event object."""
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")

    @property
    def priority(self) -> int:
        return _EVENT_PRIORITY.get(self.type, EventPriority.OUTPUT)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
        }

    def __lt__(self, other):
        """For PriorityQueue ordering."""
        if not isinstance(other, Event):
            return NotImplemented
        return self.priority < other.priority


# ─── Dead Letter ──────────────────────────────────────────────────────────────

@dataclass
class DeadLetter:
    event: Event
    handler_name: str
    error: str
    traceback: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ─── EventBus ─────────────────────────────────────────────────────────────────

class EventBus:
    """
    Async pub/sub event bus.

    Usage:
        bus = EventBus()
        bus.subscribe("TRADE_EXECUTED", my_handler)
        await bus.start()
        await bus.publish(Event(type="TRADE_EXECUTED", payload={...}))
    """

    def __init__(self, log_dir: Optional[str] = None, history_size: int = 1000):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._wildcard_subscribers: List[Callable] = []  # Subscribe to ALL events
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running = False
        self._consumer_task: Optional[asyncio.Task] = None

        # Event history (ring buffer)
        self._history: deque = deque(maxlen=history_size)

        # Dead letter queue
        self._dead_letters: deque = deque(maxlen=200)

        # Event logging
        self._log_file = None
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_path = os.path.join(log_dir, f"events_{date_str}.jsonl")
            self._log_file = open(log_path, "a", encoding="utf-8")

        # Metrics
        self._event_count = 0
        self._error_count = 0

    # ─── Subscription ─────────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Callable[[Event], Coroutine]):
        """Subscribe a coroutine handler to an event type."""
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed {handler.__qualname__} to {event_type}")

    def subscribe_all(self, handler: Callable[[Event], Coroutine]):
        """Subscribe a handler to ALL events (for broadcasting/logging)."""
        self._wildcard_subscribers.append(handler)
        logger.debug(f"Subscribed {handler.__qualname__} to ALL events")

    def unsubscribe(self, event_type: str, handler: Callable):
        """Remove a handler from a specific event type."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h != handler
            ]

    # ─── Publishing ───────────────────────────────────────────────────────

    async def publish(self, event: Event):
        """Publish an event to the bus. Non-blocking (queued)."""
        await self._queue.put(event)

    def publish_nowait(self, event: Event):
        """Non-async publish (for use from sync code / threads)."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error(f"EventBus queue full — dropping event {event.type}")

    # ─── Consumer Loop ────────────────────────────────────────────────────

    async def start(self):
        """Start the event consumer loop."""
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("EventBus started")

    async def stop(self):
        """Gracefully stop the consumer loop."""
        self._running = False
        # Push a sentinel to unblock the consumer
        await self._queue.put(Event(type="__STOP__", source="EventBus"))
        if self._consumer_task:
            await self._consumer_task
        if self._log_file:
            self._log_file.close()
        logger.info(f"EventBus stopped. Total events: {self._event_count}, errors: {self._error_count}")

    async def _consume_loop(self):
        """Main consumer: dequeue events and dispatch to handlers."""
        while self._running:
            try:
                event = await self._queue.get()

                if event.type == "__STOP__":
                    break

                self._event_count += 1
                self._history.append(event)

                # Persist to log file
                self._log_event(event)

                # Dispatch to type-specific subscribers
                handlers = self._subscribers.get(event.type, [])
                # Plus wildcard subscribers
                all_handlers = handlers + self._wildcard_subscribers

                if all_handlers:
                    tasks = []
                    for handler in all_handlers:
                        tasks.append(self._safe_dispatch(handler, event))
                    await asyncio.gather(*tasks)

                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventBus consumer error: {e}")
                self._error_count += 1

    async def _safe_dispatch(self, handler: Callable, event: Event):
        """Dispatch event to handler with error isolation."""
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            self._error_count += 1
            handler_name = getattr(handler, '__qualname__', str(handler))
            tb = traceback.format_exc()
            logger.error(f"Handler {handler_name} failed on {event.type}: {e}")

            # Store in dead letter queue
            self._dead_letters.append(DeadLetter(
                event=event,
                handler_name=handler_name,
                error=str(e),
                traceback=tb,
            ))

    # ─── Event Logging ────────────────────────────────────────────────────

    def _log_event(self, event: Event):
        """Persist event to JSONL file for replay/debugging."""
        if not self._log_file:
            return
        try:
            # Serialize payload — skip non-serializable objects
            safe_payload = {}
            for k, v in event.payload.items():
                try:
                    json.dumps(v)
                    safe_payload[k] = v
                except (TypeError, ValueError):
                    safe_payload[k] = str(v)

            line = json.dumps({
                "type": event.type,
                "source": event.source,
                "timestamp": event.timestamp,
                "event_id": event.event_id,
                "payload": safe_payload,
            })
            self._log_file.write(line + "\n")
            self._log_file.flush()
        except Exception:
            pass  # Never let logging crash the bus

    # ─── Introspection ────────────────────────────────────────────────────

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def history(self) -> List[Event]:
        return list(self._history)

    @property
    def dead_letters(self) -> List[DeadLetter]:
        return list(self._dead_letters)

    @property
    def subscriber_count(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self._subscribers.items()}

    def get_recent_events(self, event_type: str = None, limit: int = 20) -> List[Event]:
        """Get recent events, optionally filtered by type."""
        events = list(self._history)
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]
