"""
BaseService — Abstract base class for all event-driven services.

Every service in the system inherits from this. It provides:
  - Standardized lifecycle (start/stop)
  - EventBus integration
  - Health reporting
  - Error isolation (a service crash doesn't bring down others)
"""

import asyncio
import logging
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from core.event_bus import EventBus, Event, EventTypes


class BaseService(ABC):
    """
    Abstract base for all event-driven services.

    Subclasses must implement:
        - name (property): unique service identifier string
        - _setup(): subscribe to events, initialize state
        - _teardown(): cleanup resources

    Optional overrides:
        - _run_loop(): for services that need a periodic loop (e.g., data polling)
    """

    def __init__(self, event_bus: EventBus):
        self.bus = event_bus
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._started_at: Optional[str] = None
        self._error_count = 0
        self.logger = logging.getLogger(self.name)

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique service name."""
        ...

    @abstractmethod
    async def _setup(self):
        """Subscribe to events, initialize resources. Called once on start."""
        ...

    async def _teardown(self):
        """Cleanup resources. Called once on stop. Override if needed."""
        pass

    async def _run_loop(self):
        """Optional periodic loop. Override for polling services."""
        pass

    # ─── Lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        """Start the service."""
        try:
            self._running = True
            self._started_at = datetime.now(timezone.utc).isoformat()

            await self._setup()

            # Start the run loop if the subclass defines one
            self._loop_task = asyncio.create_task(self._guarded_loop())

            self.logger.info(f"[{self.name}] Service started")
            await self.bus.publish(Event(
                type=EventTypes.SERVICE_STARTED,
                payload={"service": self.name},
                source=self.name,
            ))
        except Exception as e:
            self.logger.error(f"[{self.name}] Failed to start: {e}")
            await self._publish_error(e)

    async def stop(self):
        """Gracefully stop the service."""
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        try:
            await self._teardown()
        except Exception as e:
            self.logger.error(f"[{self.name}] Error during teardown: {e}")

        self.logger.info(f"[{self.name}] Service stopped")
        await self.bus.publish(Event(
            type=EventTypes.SERVICE_STOPPED,
            payload={"service": self.name},
            source=self.name,
        ))

    # ─── Guarded Loop ────────────────────────────────────────────────────

    async def _guarded_loop(self):
        """Wraps _run_loop with error isolation and restart logic."""
        while self._running:
            try:
                await self._run_loop()
                # If _run_loop returns (no infinite loop), we stop
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                self.logger.error(f"[{self.name}] Loop error (#{self._error_count}): {e}")
                self.logger.debug(traceback.format_exc())
                await self._publish_error(e)

                # Exponential backoff on repeated failures, max 60s
                backoff = min(60, 2 ** min(self._error_count, 6))
                self.logger.info(f"[{self.name}] Restarting loop in {backoff}s...")
                await asyncio.sleep(backoff)

    # ─── Helpers ──────────────────────────────────────────────────────────

    async def emit(self, event_type: str, payload: dict = None):
        """Convenience: publish an event from this service."""
        await self.bus.publish(Event(
            type=event_type,
            payload=payload or {},
            source=self.name,
        ))

    def emit_sync(self, event_type: str, payload: dict = None):
        """Sync convenience: publish an event from this service (non-blocking)."""
        self.bus.publish_nowait(Event(
            type=event_type,
            payload=payload or {},
            source=self.name,
        ))

    async def _publish_error(self, error: Exception):
        """Publish a SERVICE_ERROR event."""
        await self.bus.publish(Event(
            type=EventTypes.SERVICE_ERROR,
            payload={
                "service": self.name,
                "error": str(error),
                "traceback": traceback.format_exc(),
            },
            source=self.name,
        ))

    # ─── Health ────────────────────────────────────────────────────────────

    async def health(self) -> dict:
        """Return service health info."""
        return {
            "name": self.name,
            "running": self._running,
            "started_at": self._started_at,
            "error_count": self._error_count,
            "loop_alive": self._loop_task is not None and not self._loop_task.done(),
        }
