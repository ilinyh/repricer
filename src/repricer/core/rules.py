"""Ядро: детерминированное решение о цене товара.

Функция :func:`decide` не выполняет ввод-вывод, не читает системные часы и не обращается к БД.
Все данные, включая дату расчёта, передаются аргументами.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .money import fmt_pct, fmt_rubles, margin_pct, quantize_days, quantize_pct, round_to_ruble
from .types import Decision, Mode, ProductSnapshot, Reason, Rules, Zone


def _sales_total(product: ProductSnapshot, rules: Rules, as_of_date: date) -> Decimal:
    """Сумма продаж за окно, заканчивающееся датой расчёта (включительно)."""

    start = as_of_date - timedelta(days=rules.sales_window_days - 1)
    total = Decimal(0)
    for day, qty in product.sales_by_day:
        if start <= day <= as_of_date:
            total += Decimal(qty)
    return total


def _corridor(rules: Rules, base_price_kopecks: int) -> tuple[Decimal, Decimal]:
    lower = Decimal(base_price_kopecks) * (Decimal(100) - rules.max_discount_pct) / Decimal(100)
    upper = Decimal(base_price_kopecks) * (Decimal(100) + rules.max_markup_pct) / Decimal(100)
    return lower, upper


def _round_inside_corridor(
    raw: Decimal, *, raising: bool, lower: Decimal, upper: Decimal, limiters: list[str]
) -> int:
    """Ограничить цену коридором и округлить до целого рубля внутрь коридора."""

    direction_up = raising
    if raw <= lower:
        raw = lower
        limiters.append("corridor_lower")
        direction_up = True
    elif raw >= upper:
        raw = upper
        limiters.append("corridor_upper")
        direction_up = False

    price = round_to_ruble(raw, up=direction_up)

    # Страховка: округление не должно выводить за коридор.
    if Decimal(price) < lower:
        price = round_to_ruble(lower, up=True)
    if Decimal(price) > upper:
        price = round_to_ruble(upper, up=False)
    return price


def decide(
    product: ProductSnapshot,
    rules: Rules = Rules(),
    *,
    as_of: datetime,
    mode: Mode = Mode.OFF,
) -> Decision:
    """Посчитать решение по товару на дату расчёта ``as_of``.

    ``mode`` на арифметику не влияет: режим определяет лишь, что делать с решением дальше.
    """

    as_of_date = as_of.date()
    stock = Decimal(product.stock_qty)
    current = product.current_price_kopecks
    margin_before = margin_pct(current, product.cost_kopecks)
    warning = margin_before is not None and margin_before < rules.min_margin_pct

    total = _sales_total(product, rules, as_of_date)
    sales_per_day = total / Decimal(rules.sales_window_days)

    days_of_stock: Decimal | None
    if stock <= 0:
        zone = Zone.NO_STOCK
        sales_per_day = Decimal(0) if total == 0 else sales_per_day
        days_of_stock = None
    elif sales_per_day == 0:
        zone = Zone.OVERSTOCK
        days_of_stock = None
    else:
        days_of_stock = stock / sales_per_day
        if days_of_stock < rules.outage_days:
            zone = Zone.OUTAGE
        elif days_of_stock > rules.overstock_days:
            zone = Zone.OVERSTOCK
        else:
            zone = Zone.NORMAL

    if zone is Zone.NO_STOCK:
        return Decision(
            vendor_code=product.vendor_code,
            zone=zone,
            sales_per_day=sales_per_day,
            days_of_stock=days_of_stock,
            current_price_kopecks=current,
            recommended_price_kopecks=current,
            margin_before_pct=None,
            margin_after_pct=None,
            reason=Reason.NO_STOCK,
            message="Остатка нет — цену не трогаем",
            limiters=("no_stock",),
        )

    if zone is Zone.NORMAL:
        return Decision(
            vendor_code=product.vendor_code,
            zone=zone,
            sales_per_day=sales_per_day,
            days_of_stock=quantize_days(days_of_stock),
            current_price_kopecks=current,
            recommended_price_kopecks=current,
            margin_before_pct=quantize_pct(margin_before),
            margin_after_pct=quantize_pct(margin_before),
            reason=Reason.NORMAL,
            message="Запас в норме — цену не трогаем",
            margin_warning=warning,
        )

    raising = zone is Zone.OUTAGE
    limiters: list[str] = []
    if sales_per_day == 0:
        limiters.append("no_sales")

    step = rules.step_pct / Decimal(100)
    factor = Decimal(1) + step if raising else Decimal(1) - step
    raw = Decimal(current) * factor

    lower, upper = _corridor(rules, product.base_price_kopecks)
    new_price = _round_inside_corridor(
        raw, raising=raising, lower=lower, upper=upper, limiters=limiters
    )

    def unchanged(reason: Reason, message: str, extra: tuple[str, ...] = ()) -> Decision:
        return Decision(
            vendor_code=product.vendor_code,
            zone=zone,
            sales_per_day=sales_per_day,
            days_of_stock=quantize_days(days_of_stock),
            current_price_kopecks=current,
            recommended_price_kopecks=current,
            margin_before_pct=quantize_pct(margin_before),
            margin_after_pct=quantize_pct(margin_before),
            reason=reason,
            message=message,
            limiters=tuple(limiters) + extra,
            margin_warning=warning,
        )

    price_changed = new_price != current

    if price_changed and new_price < current:
        margin_new = margin_pct(new_price, product.cost_kopecks)
        if margin_new is not None and margin_new < rules.min_margin_pct:
            return unchanged(
                Reason.MIN_MARGIN,
                f"Снижение не разрешено: маржа {fmt_pct(margin_new)}% ниже минимальной {fmt_pct(rules.min_margin_pct)}%",
                ("min_margin",),
            )

    if price_changed and product.last_price_change_at is not None:
        elapsed = as_of - product.last_price_change_at
        if elapsed < timedelta(hours=rules.pause_hours):
            hours = elapsed.total_seconds() / 3600
            return unchanged(
                Reason.PAUSE,
                f"Пауза после изменения цены: прошло {hours:.1f} ч из {rules.pause_hours} ч",
                ("pause",),
            )

    if not price_changed:
        if "corridor_lower" in limiters:
            return unchanged(
                Reason.CORRIDOR_LOWER,
                f"Упёрлись в нижнюю границу коридора ({fmt_rubles(lower)} ₽) — изменения нет",
            )
        if "corridor_upper" in limiters:
            return unchanged(
                Reason.CORRIDOR_UPPER,
                f"Упёрлись в верхнюю границу коридора ({fmt_rubles(upper)} ₽) — изменения нет",
            )
        return unchanged(Reason.NO_CHANGE, "После ограничений и округления цена не изменилась")

    margin_after = margin_pct(new_price, product.cost_kopecks)
    if zone is Zone.OUTAGE:
        reason = Reason.RAISE_OUTAGE
        message = (
            f"Аутсток: {fmt_rubles(current)} × {fmt_pct(rules.step_pct + 100)}% = "
            f"{fmt_rubles(raw)} → {fmt_rubles(new_price)} ₽"
        )
    else:
        reason = Reason.NO_SALES_OVERSTOCK if sales_per_day == 0 else Reason.LOWER_OVERSTOCK
        message = (
            f"Оверсток: {fmt_rubles(current)} × {fmt_pct(100 - rules.step_pct)}% = "
            f"{fmt_rubles(raw)} → {fmt_rubles(new_price)} ₽"
        )
        if sales_per_day == 0:
            message = f"Продаж за окно нет — запас бесконечный. {message}"

    return Decision(
        vendor_code=product.vendor_code,
        zone=zone,
        sales_per_day=sales_per_day,
        days_of_stock=quantize_days(days_of_stock),
        current_price_kopecks=current,
        recommended_price_kopecks=new_price,
        margin_before_pct=quantize_pct(margin_before),
        margin_after_pct=quantize_pct(margin_after),
        reason=reason,
        message=message,
        limiters=tuple(limiters),
        margin_warning=warning,
        price_changed=True,
    )
