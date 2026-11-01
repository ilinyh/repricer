"""Форматирование значений для интерфейса."""

from __future__ import annotations

from decimal import Decimal

ZONE_LABELS = {
    "no_stock": "нет в наличии",
    "outage": "аутсток",
    "normal": "норма",
    "overstock": "оверсток",
    "not_on_market": "нет на площадке",
}

MODE_LABELS = {"off": "выключен", "recommend": "рекомендация", "auto": "авто"}

STATUS_LABELS = {
    "pending": "в очереди",
    "approved": "подтверждено",
    "rejected": "отклонено",
    "auto": "авто",
    "sent": "отправлено",
    "applied": "применено",
    "failed": "ошибка",
    "quarantine": "карантин",
    "superseded": "заменено",
}

REASON_LABELS = {
    "no_stock": "нет остатка",
    "no_sales_overstock": "нет продаж",
    "normal": "норма",
    "raise_outage": "повышение",
    "lower_overstock": "снижение",
    "corridor_lower": "нижняя граница коридора",
    "corridor_upper": "верхняя граница коридора",
    "min_margin": "минимальная маржа",
    "no_change": "без изменений",
    "pause": "пауза",
    "incoming": "поставка в пути",
    "return_to_base": "возврат к базовой",
}

LIMITER_LABELS = {
    "corridor_lower": "нижняя граница коридора",
    "corridor_upper": "верхняя граница коридора",
    "min_margin": "минимальная маржа",
    "pause": "пауза",
    "no_stock": "нет остатка",
    "no_sales": "нет продаж",
    "incoming": "аутсток не учитывается",
}

EVENT_LABELS = {
    "recommendation_created": "создана рекомендация",
    "recommendation_superseded": "рекомендация заменена",
    "approved": "подтверждено",
    "rejected": "отклонено",
    "sent": "отправлено на площадку",
    "applied": "цена применена",
    "failed": "ошибка",
    "quarantine": "карантин",
    "no_change": "цена не менялась",
    "mode_changed": "сменён режим",
    "settings_changed": "изменены настройки",
}


def _blank(value: object) -> bool:
    return value is None or value == "" or value == "None"


def rub(value: object) -> str:
    if _blank(value):
        return "—"
    text = f"{Decimal(str(value)) / 100:,.2f}".replace(",", "\u00a0")
    return text.replace(".", ",") + " ₽"


def pct(value: object) -> str:
    if _blank(value):
        return "—"
    return f"{Decimal(str(value)):.1f}".replace(".", ",") + "%"


def days(value: object) -> str:
    if _blank(value):
        return "∞"
    return f"{Decimal(str(value)):.1f}".replace(".", ",")


def num(value: object) -> str:
    if _blank(value):
        return "—"
    return f"{Decimal(str(value)):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def label(mapping: dict[str, str], value: object) -> str:
    if _blank(value):
        return "—"
    return mapping.get(str(value), str(value))
