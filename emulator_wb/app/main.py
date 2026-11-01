"""HTTP-сервис эмулятора Wildberries «Цены и скидки».

Пути и форматы — как в документации WB. Эмулятор ничего не знает о репрайсере:
только HTTP, никакого общего кода и общей базы.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import UploadRequest
from .store import Good, Store, Task, TaskItem, load_goods

DEFAULT_SEED = Path(__file__).resolve().parent.parent / "data" / "goods.json"


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, default))
    except InvalidOperation:
        return Decimal(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def create_app(
    store: Store | None = None,
    *,
    rate_limit: int | None = None,
    rate_window: float | None = None,
) -> FastAPI:
    if store is None:
        seed_path = Path(os.getenv("WB_SEED_PATH", str(DEFAULT_SEED)))
        delay = float(os.getenv("WB_APPLY_DELAY_SECONDS", "3"))
        quarantine = _env_decimal("WB_QUARANTINE_PCT", "30")
        store = Store(load_goods(seed_path), apply_delay=delay, quarantine_pct=quarantine)
    if rate_limit is None:
        rate_limit = max(0, _env_int("WB_RATE_LIMIT", 0))
    if rate_window is None:
        rate_window = max(0.01, _env_float("WB_RATE_LIMIT_WINDOW_SECONDS", 1.0))

    app = FastAPI(title="WB Prices Emulator", version="0.1.0")
    app.state.store = store

    buckets: dict[str, deque[float]] = {}
    buckets_lock = threading.Lock()

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        if rate_limit <= 0 or request.url.path == "/health":
            return await call_next(request)
        token = request.headers.get("authorization", "")
        now = time.monotonic()
        with buckets_lock:
            bucket = buckets.setdefault(token, deque())
            while bucket and now - bucket[0] > rate_window:
                bucket.popleft()
            if len(bucket) >= rate_limit:
                retry_after = max(1, math.ceil(rate_window - (now - bucket[0])))
                return JSONResponse(
                    status_code=429,
                    content={"error": True, "errorText": "Слишком много запросов"},
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
        return await call_next(request)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not authorization.strip():
            raise HTTPException(status_code=401, detail="Не указан токен")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": True, "errorText": str(exc.detail)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": True, "errorText": "Некорректный запрос"},
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/v2/list/goods/filter", dependencies=[Depends(require_token)])
    def list_goods(
        limit: int = Query(default=1000, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        filterNmID: int | None = Query(default=None),
        filterVendorCode: str | None = Query(default=None),
    ) -> dict:
        goods = store.list_goods(
            limit=limit, offset=offset, nm_id=filterNmID, vendor_code=filterVendorCode
        )
        return {
            "data": {"listGoods": [_good_json(good) for good in goods]},
            "error": False,
            "errorText": "",
        }

    @app.post("/api/v2/upload/task", dependencies=[Depends(require_token)])
    def upload(payload: UploadRequest) -> dict:
        if not payload.data:
            raise HTTPException(status_code=400, detail="Пустой список товаров")
        items = [
            TaskItem(nm_id=row.nmID, price=row.price, discount=row.discount)
            for row in payload.data
        ]
        task, already_exists = store.create_task(items)
        return {
            "data": {"id": task.id, "alreadyExists": already_exists},
            "error": False,
            "errorText": "",
        }

    @app.get("/api/v2/history/tasks", dependencies=[Depends(require_token)])
    def history_tasks(uploadID: int = Query(...)) -> dict:
        task = store.get_task(uploadID)
        if task is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {
            "data": {
                "uploadID": task.id,
                "status": task.status,
                "uploadDate": task.created_at.isoformat(),
                "activationDate": task.applied_at.isoformat() if task.applied_at else "",
                "overAllLimit": 0,
                "processed": task.applied_at.isoformat() if task.applied_at else "",
                "goods": [_task_item_json(store, task, item, with_code=True) for item in task.items],
            },
            "error": False,
            "errorText": "",
        }

    @app.get("/api/v2/history/goods/task", dependencies=[Depends(require_token)])
    def history_goods(
        uploadID: int = Query(...),
        limit: int = Query(default=1000, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        task = store.get_task(uploadID)
        if task is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        items = store.task_goods(task, limit=limit, offset=offset)
        return {
            "data": {
                "uploadID": task.id,
                "goods": [_task_item_json(store, task, item, with_code=True) for item in items],
            },
            "error": False,
            "errorText": "",
        }

    return app


def _good_json(good: Good) -> dict:
    return {
        "nmID": good.nm_id,
        "vendorCode": good.vendor_code,
        "sizes": [
            {
                "techSize": "0",
                "skus": [],
                # В списке WB цены — в копейках.
                "price": good.price_kopecks,
                "discountedPrice": good.discounted_price_kopecks,
            }
        ],
        "currencyIsoCode4217": "RUB",
        "discount": good.discount,
        "clubDiscount": 0,
        "editableSizePrice": False,
    }


def _task_item_json(store: Store, task: Task, item: TaskItem, *, with_code: bool = False) -> dict:
    payload = {
        "nmID": item.nm_id,
        "vendorCode": store.vendor_code(item.nm_id),
        "price": item.price,
        "discount": item.discount,
        "errorText": item.error_text,
    }
    if with_code:
        payload["errorCode"] = item.error_code or ""
    return payload


app = create_app()