"""
Core framework for event-driven architecture.
"""
from core.event_bus import EventBus, Event, EventPriority
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway

__all__ = ["EventBus", "Event", "EventPriority", "BaseService", "MT5Gateway"]
