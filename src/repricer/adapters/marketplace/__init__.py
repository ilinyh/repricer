"""Клиенты площадок."""

from .wb_client import MarketGood, TaskItemResult, TaskStatus, UploadPrice, WBClient, WBClientError

__all__ = [
    "MarketGood",
    "TaskItemResult",
    "TaskStatus",
    "UploadPrice",
    "WBClient",
    "WBClientError",
]
