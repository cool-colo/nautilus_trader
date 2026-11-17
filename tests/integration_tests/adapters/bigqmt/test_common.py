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

import pytest

from nautilus_trader.adapters.bigqmt.common import bigqmt_action_to_side
from nautilus_trader.adapters.bigqmt.common import bigqmt_status_to_order_status
from nautilus_trader.adapters.bigqmt.common import bigqmt_symbol_to_instrument_id
from nautilus_trader.adapters.bigqmt.common import bigqmt_traded_at_to_nanos
from nautilus_trader.adapters.bigqmt.common import instrument_id_to_bigqmt_symbol
from nautilus_trader.adapters.bigqmt.common import parse_equity
from nautilus_trader.adapters.bigqmt.common import parse_full_tick_as_order_book_depth10
from nautilus_trader.adapters.bigqmt.common import parse_full_tick_as_quote_tick
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.model.data import OrderBookDepth10
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=BIG_QMT_VENUE)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (48, OrderStatus.SUBMITTED),  # unreported
        (49, OrderStatus.SUBMITTED),  # wait reporting
        (50, OrderStatus.ACCEPTED),  # reported
        (55, OrderStatus.PARTIALLY_FILLED),  # part success
        (56, OrderStatus.FILLED),  # succeeded
        (51, OrderStatus.CANCELED),  # reported cancel
        (52, OrderStatus.CANCELED),  # partsucc cancel
        (53, OrderStatus.CANCELED),  # part cancel
        (54, OrderStatus.CANCELED),  # canceled
        (57, OrderStatus.REJECTED),  # junk
        ("50", OrderStatus.ACCEPTED),  # string coercion
        (255, OrderStatus.ACCEPTED),  # unknown -> fallback
        (None, OrderStatus.ACCEPTED),  # unparseable -> fallback
    ],
)
def test_bigqmt_status_to_order_status(status, expected):
    assert bigqmt_status_to_order_status(status) == expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("BUY", OrderSide.BUY),
        ("SELL", OrderSide.SELL),
        ("buy", OrderSide.BUY),
        (23, OrderSide.BUY),
        (24, OrderSide.SELL),
        ("23", OrderSide.BUY),
        ("24", OrderSide.SELL),
    ],
)
def test_bigqmt_action_to_side(action, expected):
    assert bigqmt_action_to_side(action) == expected


def test_bigqmt_action_to_side_rejects_unknown():
    with pytest.raises(ValueError):
        bigqmt_action_to_side("HODL")


def test_symbol_instrument_id_roundtrip():
    iid = bigqmt_symbol_to_instrument_id("000001.SZ")
    assert iid == INSTRUMENT_ID
    assert instrument_id_to_bigqmt_symbol(iid) == "000001.SZ"


def test_instrument_id_to_bigqmt_symbol_rejects_wrong_venue():
    from nautilus_trader.model.identifiers import Venue

    other = InstrumentId(symbol=Symbol("000001.SZ"), venue=Venue("QMT"))
    with pytest.raises(ValueError):
        instrument_id_to_bigqmt_symbol(other)


def test_parse_equity_uses_bigqmt_venue():
    instrument = parse_equity(
        symbol="000404.SZ",
        fields={"InstrumentName": "Test equity", "InstrumentStatus": 0},
        ts_event=1,
        ts_init=2,
    )

    assert str(instrument.id) == "000404.SZ.BIGQMT"
    assert instrument.raw_symbol.value == "000404.SZ"
    assert instrument.info["name"] == "Test equity"
    assert instrument.info["is_suspended"] is False


def test_parse_full_tick_as_quote_tick_happy_path():
    tick = parse_full_tick_as_quote_tick(
        INSTRUMENT_ID,
        {
            "lastPrice": 10.50,
            "bidPrice": [10.49, 10.48, 10.47],
            "askPrice": [10.51, 10.52, 10.53],
            "bidVol": [100, 200, 300],
            "askVol": [110, 210, 310],
            "time": 1733118954000,
        },
        ts_init=7,
    )
    assert isinstance(tick, QuoteTick)
    assert tick.instrument_id == INSTRUMENT_ID
    assert str(tick.bid_price) == "10.49"
    assert str(tick.ask_price) == "10.51"
    assert tick.bid_size.as_double() == 100
    assert tick.ask_size.as_double() == 110
    assert tick.ts_event == 1733118954000 * 1_000_000
    assert tick.ts_init == 7


def test_parse_full_tick_falls_back_ts_event_to_ts_init():
    tick = parse_full_tick_as_quote_tick(
        INSTRUMENT_ID,
        {
            "bidPrice": [10.49],
            "askPrice": [10.51],
            "bidVol": [100],
            "askVol": [110],
        },
        ts_init=99,
    )
    assert tick is not None
    assert tick.ts_event == 99


def test_parse_full_tick_drops_empty_book():
    assert parse_full_tick_as_quote_tick(INSTRUMENT_ID, {}, ts_init=1) is None


def test_parse_full_tick_drops_non_positive_price():
    assert (
        parse_full_tick_as_quote_tick(
            INSTRUMENT_ID,
            {"bidPrice": [0.0], "askPrice": [10.51], "bidVol": [0], "askVol": [110]},
            ts_init=1,
        )
        is None
    )


def test_parse_full_tick_as_order_book_depth10_happy_path():
    depth = parse_full_tick_as_order_book_depth10(
        INSTRUMENT_ID,
        {
            "bidPrice": [10.49, 10.48, 10.47],
            "askPrice": [10.51, 10.52, 10.53],
            "bidVol": [100, 200, 300],
            "askVol": [110, 210, 310],
            "time": 1733118954000,
        },
        ts_init=7,
    )
    assert isinstance(depth, OrderBookDepth10)
    assert depth.instrument_id == INSTRUMENT_ID
    # Top of book preserved.
    assert str(depth.bids[0].price) == "10.49"
    assert str(depth.asks[0].price) == "10.51"
    assert depth.bids[0].size.as_double() == 100
    assert depth.asks[0].size.as_double() == 110
    # Third valid level preserved.
    assert str(depth.bids[2].price) == "10.47"
    assert depth.asks[2].size.as_double() == 310
    # Remaining levels padded with null orders (price 0).
    assert depth.bids[3].price.as_double() == 0
    assert depth.ts_event == 1733118954000 * 1_000_000
    assert depth.ts_init == 7


def test_parse_full_tick_as_order_book_depth10_drops_empty_book():
    assert parse_full_tick_as_order_book_depth10(INSTRUMENT_ID, {}, ts_init=1) is None


def test_parse_full_tick_as_order_book_depth10_falls_back_ts_event_to_ts_init():
    depth = parse_full_tick_as_order_book_depth10(
        INSTRUMENT_ID,
        {
            "bidPrice": [10.49],
            "askPrice": [10.51],
            "bidVol": [100],
            "askVol": [110],
        },
        ts_init=99,
    )
    assert depth is not None
    assert depth.ts_event == 99


def test_parse_full_tick_as_order_book_depth10_truncates_at_non_positive_level():
    depth = parse_full_tick_as_order_book_depth10(
        INSTRUMENT_ID,
        {
            "bidPrice": [10.49, 10.48, 10.47],
            "askPrice": [10.51, 10.52, 10.53],
            # Only the bid side truncates at its second zero-volume level.
            "bidVol": [100, 0, 300],
            "askVol": [110, 210, 310],
            "time": 1733118954000,
        },
        ts_init=7,
    )
    assert depth is not None
    assert str(depth.bids[0].price) == "10.49"
    # Level 2 dropped -> padded to null.
    assert depth.bids[1].price.as_double() == 0
    assert depth.asks[1].price.as_double() == 10.52


def test_bigqmt_traded_at_to_nanos_full_datetime():
    import pandas as pd

    result = bigqmt_traded_at_to_nanos("2026-07-02 10:00:00")
    expected = int(pd.Timestamp("2026-07-02 10:00:00", tz="Asia/Shanghai").tz_convert("UTC").value)
    assert result == expected


def test_bigqmt_traded_at_to_nanos_time_only_colon():
    import pandas as pd

    trading_day = "2026-07-02"
    result = bigqmt_traded_at_to_nanos("09:31:00", trading_day=trading_day)
    expected = int(pd.Timestamp("2026-07-02 09:31:00", tz="Asia/Shanghai").tz_convert("UTC").value)
    assert result == expected


def test_bigqmt_traded_at_to_nanos_hhmmss_digits():
    import pandas as pd

    trading_day = "2026-07-02"
    result = bigqmt_traded_at_to_nanos("130524", trading_day=trading_day)
    expected = int(pd.Timestamp("2026-07-02 13:05:24", tz="Asia/Shanghai").tz_convert("UTC").value)
    assert result == expected


def test_bigqmt_traded_at_to_nanos_epoch_seconds():
    import pandas as pd

    result = bigqmt_traded_at_to_nanos("1785914432")
    expected = int(pd.Timestamp(1785914432, unit="s", tz="UTC").value)
    assert result == expected


def test_bigqmt_traded_at_to_nanos_stable_across_calls():
    # The same traded_at value must always yield the same ts_event (regression for
    # the ExecEngine "Fill report data differs ... ts_event" warning).
    first = bigqmt_traded_at_to_nanos("2026-07-02 10:00:00")
    second = bigqmt_traded_at_to_nanos("2026-07-02 10:00:00")
    assert first == second


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-time"])
def test_bigqmt_traded_at_to_nanos_unparseable_returns_zero(value):
    assert bigqmt_traded_at_to_nanos(value) == 0


@pytest.mark.parametrize("empty_side", ["bid", "ask"])
@pytest.mark.parametrize("empty_shape", ["missing", "empty", "zeros"])
def test_depth_accepts_single_sided_price_limit_book(empty_side, empty_shape):
    payload = {"bidPrice": [10.00, 9.99], "bidVol": [100, 200],
               "askPrice": [10.01, 10.02], "askVol": [110, 210], "time": 1000}
    for field in (empty_side + "Price", empty_side + "Vol"):
        if empty_shape == "missing":
            payload.pop(field)
        else:
            payload[field] = [] if empty_shape == "empty" else [0, 0]
    diagnostics = []
    depth = parse_full_tick_as_order_book_depth10(INSTRUMENT_ID, payload, 2, diagnostics)
    assert depth is not None
    assert diagnostics == []
    empty = depth.bids if empty_side == "bid" else depth.asks
    valid = depth.asks if empty_side == "bid" else depth.bids
    assert all(level.price.as_double() == 0 for level in empty)
    assert valid[1].size.as_double() > 0
    assert depth.ts_event == 1000 * 1_000_000


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, "bad"])
def test_malformed_side_keeps_valid_opposite_side_with_reason(bad):
    diagnostics = []
    depth = parse_full_tick_as_order_book_depth10(
        INSTRUMENT_ID, {"bidPrice": [bad], "bidVol": [100],
                        "askPrice": [10.01], "askVol": [110]}, 2, diagnostics)
    assert depth is not None
    assert depth.bids[0].price.as_double() == 0
    assert depth.asks[0].price.as_double() == 10.01
    assert any(reason.startswith("bidPrice:") for reason in diagnostics)


def test_single_sided_snapshot_clears_previous_opposite_side_in_real_book():
    from nautilus_trader.model.book import OrderBook
    from nautilus_trader.model.enums import BookType

    book = OrderBook(INSTRUMENT_ID, BookType.L2_MBP)
    payload = {"bidPrice": [10.00], "bidVol": [100],
               "askPrice": [10.01], "askVol": [110], "time": 1000}
    book.apply_depth(parse_full_tick_as_order_book_depth10(INSTRUMENT_ID, payload, 2))
    assert book.best_bid_price() is not None
    assert book.best_ask_price() is not None
    payload.update(askPrice=[], askVol=[], time=2000)
    book.apply_depth(parse_full_tick_as_order_book_depth10(INSTRUMENT_ID, payload, 3))
    assert book.best_bid_price() is not None
    assert book.best_ask_price() is None
    payload.update(bidPrice=[], bidVol=[], askPrice=[10.01], askVol=[110], time=3000)
    book.apply_depth(parse_full_tick_as_order_book_depth10(INSTRUMENT_ID, payload, 4))
    assert book.best_bid_price() is None
    assert book.best_ask_price() is not None


def test_depth_parses_numpy_arrays_without_ambiguous_truth_value():
    import numpy as np
    depth = parse_full_tick_as_order_book_depth10(
        INSTRUMENT_ID, {"bidPrice": np.array([10.0, 9.99]), "bidVol": np.array([1, 2]),
                        "askPrice": np.array([]), "askVol": np.array([])}, 2)
    assert depth is not None
    assert depth.bids[1].size.as_double() == 2
