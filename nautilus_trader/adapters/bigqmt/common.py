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
"""
Parsing utilities for the Big QMT adapter.

Venue-agnostic converters are reused verbatim from the QMT adapter's ``common``
module; only the venue-specific identifier helpers and the Big QMT field-name
variants (camelCase ``get_full_tick`` payloads, integer order status codes) are
defined here.
"""

from __future__ import annotations

from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_CANCELED
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_JUNK
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_PART_CANCEL
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_PART_SUCC
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_PARTSUCC_CANCEL
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_REPORTED
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_REPORTED_CANCEL
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_SUCCEEDED
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_UNREPORTED
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_WAIT_REPORTING
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE

# Reused verbatim from the QMT adapter — these functions only depend on the
# symbol string, field dict, or bar type and are identical across both venues.
from nautilus_trader.adapters.qmt.common import NANOS_PER_MILLI
from nautilus_trader.adapters.qmt.common import SHANGHAI_TZ
from nautilus_trader.adapters.qmt.common import bar_type_to_qmt_period
from nautilus_trader.adapters.qmt.common import millis_to_nanos
from nautilus_trader.adapters.qmt.common import nautilus_side_to_qmt
from nautilus_trader.adapters.qmt.common import normalize_qmt_symbol
from nautilus_trader.adapters.qmt.common import parse_bar
from nautilus_trader.adapters.qmt.common import parse_order_book_depth10
from nautilus_trader.adapters.qmt.common import parse_quote_tick
from nautilus_trader.adapters.qmt.common import parse_trade_tick
from nautilus_trader.adapters.qmt.common import qmt_instrument_status
from nautilus_trader.adapters.qmt.common import qmt_is_suspended
from nautilus_trader.adapters.qmt.common import qmt_lifecycle_to_order_status
from nautilus_trader.adapters.qmt.common import qmt_order_type_from_price_type
from nautilus_trader.adapters.qmt.common import qmt_side_to_nautilus
from nautilus_trader.adapters.qmt.common import quantity_to_int
from nautilus_trader.adapters.qmt.common import timestamp_to_qmt_str
from nautilus_trader.model.currencies import CNY
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


__all__ = [
    "NANOS_PER_MILLI",
    "SHANGHAI_TZ",
    "bar_type_to_qmt_period",
    "bigqmt_action_to_side",
    "bigqmt_status_to_order_status",
    "bigqmt_symbol_to_instrument_id",
    "instrument_id_to_bigqmt_symbol",
    "millis_to_nanos",
    "nautilus_side_to_qmt",
    "normalize_qmt_symbol",
    "parse_bar",
    "parse_equity",
    "parse_full_tick_as_quote_tick",
    "parse_order_book_depth10",
    "parse_quote_tick",
    "parse_trade_tick",
    "qmt_instrument_status",
    "qmt_is_suspended",
    "qmt_lifecycle_to_order_status",
    "qmt_order_type_from_price_type",
    "qmt_side_to_nautilus",
    "quantity_to_int",
    "timestamp_to_qmt_str",
]


def bigqmt_symbol_to_instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId(symbol=Symbol(normalize_qmt_symbol(symbol)), venue=BIG_QMT_VENUE)


def instrument_id_to_bigqmt_symbol(instrument_id: InstrumentId) -> str:
    if instrument_id.venue != BIG_QMT_VENUE:
        raise ValueError(f"Unsupported venue for BigQMT adapter: {instrument_id.venue}")
    return normalize_qmt_symbol(instrument_id.symbol.value)


def parse_equity(
    symbol: str,
    fields: dict[str, object] | None,
    ts_event: int,
    ts_init: int,
) -> Equity:
    """
    Parse a Big QMT instrument detail payload into an equity at the BIGQMT venue.

    The field schema is shared with QMT, but the instrument identifier must use
    ``BIGQMT`` so the provider, data engine and strategies address the same cache key.
    """
    bigqmt_symbol = normalize_qmt_symbol(symbol)
    fields = fields or {}
    name = (
        fields.get("InstrumentName")
        or fields.get("instrument_name")
        or fields.get("name")
        or bigqmt_symbol
    )
    return Equity(
        instrument_id=bigqmt_symbol_to_instrument_id(bigqmt_symbol),
        raw_symbol=Symbol(bigqmt_symbol),
        currency=CNY,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(100),
        ts_event=ts_event,
        ts_init=ts_init,
        info={
            "qmt_symbol": bigqmt_symbol,
            "name": str(name),
            "instrument_status": qmt_instrument_status(fields),
            "is_suspended": qmt_is_suspended(fields),
            "fields": fields,
        },
    )


def bigqmt_status_to_order_status(status: object) -> OrderStatus:
    """
    Map a Big QMT integer order status code to a Nautilus ``OrderStatus``.

    Big QMT reports the ThinkTrader ``m_nOrderStatus`` integer rather than the
    lifecycle strings the ``quant-qmt-proxy`` normalizes to. See the ``ORDER_*``
    constants in ``xtquant_compat.py``.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return OrderStatus.ACCEPTED
    if code in (BIG_QMT_ORDER_UNREPORTED, BIG_QMT_ORDER_WAIT_REPORTING):
        return OrderStatus.SUBMITTED
    if code == BIG_QMT_ORDER_REPORTED:
        return OrderStatus.ACCEPTED
    if code == BIG_QMT_ORDER_PART_SUCC:
        return OrderStatus.PARTIALLY_FILLED
    if code == BIG_QMT_ORDER_SUCCEEDED:
        return OrderStatus.FILLED
    if code in (
        BIG_QMT_ORDER_REPORTED_CANCEL,
        BIG_QMT_ORDER_PARTSUCC_CANCEL,
        BIG_QMT_ORDER_PART_CANCEL,
        BIG_QMT_ORDER_CANCELED,
    ):
        return OrderStatus.CANCELED
    if code == BIG_QMT_ORDER_JUNK:
        return OrderStatus.REJECTED
    return OrderStatus.ACCEPTED


def bigqmt_action_to_side(action: object) -> OrderSide:
    """
    Map a Big QMT ``action`` (BUY/SELL or 23/24) to a Nautilus ``OrderSide``.
    """
    text = str(action).upper()
    if text in ("BUY", "23"):
        return OrderSide.BUY
    if text in ("SELL", "24"):
        return OrderSide.SELL
    raise ValueError(f"Unsupported BigQMT action: {action}")


def parse_full_tick_as_quote_tick(
    instrument_id: InstrumentId,
    payload: dict[str, object],
    ts_init: int,
) -> QuoteTick | None:
    """
    Parse a Big QMT ``get_full_tick`` payload into a ``QuoteTick``.

    Big QMT's native ``xtdata.get_full_tick`` returns camelCase field names
    (``bidPrice``/``askPrice``/``bidVol``/``askVol``/``time``) rather than the
    ``quant-qmt-proxy`` snake_case schema handled by ``parse_quote_tick``. The
    extraction logic is otherwise identical (top-of-book level 0).
    """
    bid_prices = payload.get("bidPrice") or []
    ask_prices = payload.get("askPrice") or []
    bid_volumes = payload.get("bidVol") or []
    ask_volumes = payload.get("askVol") or []
    if not bid_prices or not ask_prices:
        return None
    bid_price = float(bid_prices[0] or 0.0)
    ask_price = float(ask_prices[0] or 0.0)
    if bid_price <= 0 or ask_price <= 0:
        return None
    bid_size = int(bid_volumes[0] or 0) if bid_volumes else 0
    ask_size = int(ask_volumes[0] or 0) if ask_volumes else 0
    ts_event = millis_to_nanos(payload.get("time")) or ts_init
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(f"{bid_price:.2f}"),
        ask_price=Price.from_str(f"{ask_price:.2f}"),
        bid_size=Quantity.from_int(bid_size),
        ask_size=Quantity.from_int(ask_size),
        ts_event=ts_event,
        ts_init=ts_init,
    )
