"""Модели запросов эмулятора."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UploadItem(BaseModel):
    """Одна строка загрузки цен. Цена — в рублях, скидка — в процентах."""

    model_config = ConfigDict(extra="ignore")

    nmID: int
    price: int
    discount: int


class UploadRequest(BaseModel):
    """Тело POST /api/v2/upload/task."""

    model_config = ConfigDict(extra="ignore")

    data: list[UploadItem]