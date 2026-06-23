# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.model.currencies import CNY
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NANOS_PER_MILLI = 1_000_000


def normalize_qmt_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def qmt_symbol_to_instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId(symbol=Symbol(normalize_qmt_symbol(symbol)), venue=QMT_VENUE)


def instrument_id_to_qmt_symbol(instrument_id: InstrumentId) -> str:
    if instrument_id.venue != QMT_VENUE:
        raise ValueError(f"Unsupported venue for QMT adapter: {instrument_id.venue}")
    return normalize_qmt_symbol(instrument_id.symbol.value)


def millis_to_nanos(ms: int | float | str | None) -> int:
    if ms in (None, ""):
        return 0
    return int(ms) * NANOS_PER_MILLI


def timestamp_to_qmt_str(value: datetime | pd.Timestamp | None) -> str:
    if value is None:
        return ""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    timestamp = timestamp.tz_convert(SHANGHAI_TZ)
    return timestamp.strftime("%Y%m%d%H%M%S")


def bar_type_to_qmt_period(bar_type: BarType) -> str:
    spec = bar_type.spec
    aggregation = spec.aggregation
    step = spec.step
    if aggregation == BarAggregation.MINUTE and step in {1, 5, 15, 30}:
        return f"{step}m"
    if aggregation == BarAggregation.HOUR and step == 1:
        return "1h"
    if aggregation == BarAggregation.DAY and step == 1:
        return "1d"
    if aggregation == BarAggregation.WEEK and step == 1:
        return "1w"
    if aggregation == BarAggregation.MONTH and step == 1:
        return "1mon"
    raise ValueError(f"Unsupported QMT bar type: {bar_type}")


def parse_equity(
    symbol: str,
    fields: dict[str, object] | None,
    ts_event: int,
    ts_init: int,
) -> Equity:
    qmt_symbol = normalize_qmt_symbol(symbol)
    fields = fields or {}
    name = (
        fields.get("InstrumentName")
        or fields.get("instrument_name")
        or fields.get("name")
        or qmt_symbol
    )
    return Equity(
        instrument_id=qmt_symbol_to_instrument_id(qmt_symbol),
        raw_symbol=Symbol(qmt_symbol),
        currency=CNY,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(100),
        ts_event=ts_event,
        ts_init=ts_init,
        info={
            "qmt_symbol": qmt_symbol,
            "name": str(name),
            "fields": fields,
        },
    )


def parse_quote_tick(instrument_id: InstrumentId, payload: dict[str, object], ts_init: int) -> QuoteTick | None:
    bid_prices = payload.get("bid_price") or []
    ask_prices = payload.get("ask_price") or []
    bid_volumes = payload.get("bid_vol") or []
    ask_volumes = payload.get("ask_vol") or []
    if not bid_prices or not ask_prices:
        return None
    bid_price = float(bid_prices[0] or 0.0)
    ask_price = float(ask_prices[0] or 0.0)
    if bid_price <= 0 or ask_price <= 0:
        return None
    bid_size = int(bid_volumes[0] or 0) if bid_volumes else 0
    ask_size = int(ask_volumes[0] or 0) if ask_volumes else 0
    ts_event = millis_to_nanos(payload.get("time_ms")) or ts_init
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(f"{bid_price:.2f}"),
        ask_price=Price.from_str(f"{ask_price:.2f}"),
        bid_size=Quantity.from_int(bid_size),
        ask_size=Quantity.from_int(ask_size),
        ts_event=ts_event,
        ts_init=ts_init,
    )


def parse_bar(bar_type: BarType, payload: dict[str, object], ts_init: int) -> Bar | None:
    try:
        open_price = float(payload.get("open", 0.0) or 0.0)
        high_price = float(payload.get("high", 0.0) or 0.0)
        low_price = float(payload.get("low", 0.0) or 0.0)
        close_price = float(payload.get("close", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if min(open_price, high_price, low_price, close_price) <= 0:
        return None
    ts_event = millis_to_nanos(payload.get("time_ms")) or ts_init
    return Bar(
        bar_type=bar_type,
        open=Price.from_str(f"{open_price:.2f}"),
        high=Price.from_str(f"{high_price:.2f}"),
        low=Price.from_str(f"{low_price:.2f}"),
        close=Price.from_str(f"{close_price:.2f}"),
        volume=Quantity.from_int(int(payload.get("volume", 0) or 0)),
        ts_event=ts_event,
        ts_init=ts_init,
    )


def nautilus_side_to_qmt(side: OrderSide) -> str:
    if side == OrderSide.BUY:
        return "BUY"
    if side == OrderSide.SELL:
        return "SELL"
    raise ValueError(f"Unsupported order side for QMT: {side}")


def qmt_side_to_nautilus(value: object) -> OrderSide:
    if value in {23, "23", "BUY", "buy"}:
        return OrderSide.BUY
    if value in {24, "24", "SELL", "sell"}:
        return OrderSide.SELL
    raise ValueError(f"Unsupported QMT order side: {value}")


def qmt_lifecycle_to_order_status(value: str) -> OrderStatus:
    normalized = str(value or "").upper()
    if normalized == "SUBMITTED":
        return OrderStatus.SUBMITTED
    if normalized == "ACCEPTED":
        return OrderStatus.ACCEPTED
    if normalized == "PARTIALLY_FILLED":
        return OrderStatus.PARTIALLY_FILLED
    if normalized == "FILLED":
        return OrderStatus.FILLED
    if normalized == "CANCELED":
        return OrderStatus.CANCELED
    if normalized == "REJECTED":
        return OrderStatus.REJECTED
    if normalized == "EXPIRED":
        return OrderStatus.EXPIRED
    return OrderStatus.ACCEPTED


def qmt_order_type_from_price_type(price_type: int) -> OrderType:
    return OrderType.LIMIT if int(price_type or 0) == 11 else OrderType.MARKET


def quantity_to_int(quantity: Quantity) -> int:
    return int(Decimal(str(quantity)))
