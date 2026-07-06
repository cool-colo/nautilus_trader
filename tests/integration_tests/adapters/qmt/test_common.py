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

from nautilus_trader.adapters.qmt.common import parse_order_book_depth10
from nautilus_trader.adapters.qmt.common import parse_trade_tick
from nautilus_trader.adapters.qmt.common import qmt_trade_flag_to_aggressor
from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=QMT_VENUE)


def _record(**overrides):
    payload = {
        "price": 11.5,
        "volume": 300,
        "amount": 3450.0,
        "trade_index": 42,
        "buy_no": 100,
        "sell_no": 200,
        "trade_type": 1,
        "trade_flag": 1,
        "time_ms": 1733118954000,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        (1, AggressorSide.BUYER),
        (2, AggressorSide.SELLER),
        (0, AggressorSide.NO_AGGRESSOR),
        (3, AggressorSide.NO_AGGRESSOR),
        ("1", AggressorSide.BUYER),
        (None, AggressorSide.NO_AGGRESSOR),
    ],
)
def test_qmt_trade_flag_to_aggressor(flag, expected):
    assert qmt_trade_flag_to_aggressor(flag) == expected


def test_parse_trade_tick_happy_path():
    trade = parse_trade_tick(INSTRUMENT_ID, _record(), ts_init=7)

    assert isinstance(trade, TradeTick)
    assert trade.instrument_id == INSTRUMENT_ID
    assert str(trade.price) == "11.50"
    assert trade.size.as_double() == 300
    assert trade.aggressor_side == AggressorSide.BUYER
    assert str(trade.trade_id) == "42"
    assert trade.ts_event == 1733118954000 * 1_000_000
    assert trade.ts_init == 7


def test_parse_trade_tick_seller_side():
    trade = parse_trade_tick(INSTRUMENT_ID, _record(trade_flag=2), ts_init=1)

    assert trade is not None
    assert trade.aggressor_side == AggressorSide.SELLER


def test_parse_trade_tick_drops_shenzhen_cancel():
    # tradeFlag == 3 is a 深交所撤单, not a trade.
    assert parse_trade_tick(INSTRUMENT_ID, _record(trade_flag=3), ts_init=1) is None


def test_parse_trade_tick_drops_zero_volume():
    assert parse_trade_tick(INSTRUMENT_ID, _record(volume=0), ts_init=1) is None


def test_parse_trade_tick_drops_non_positive_price():
    assert parse_trade_tick(INSTRUMENT_ID, _record(price=0.0), ts_init=1) is None


def test_parse_trade_tick_synthesizes_trade_id_when_missing():
    trade = parse_trade_tick(INSTRUMENT_ID, _record(trade_index=0), ts_init=1)

    assert trade is not None
    # Synthesized from symbol + ts_event so ids stay unique and non-empty.
    assert str(trade.trade_id) == f"000001.SZ-{1733118954000 * 1_000_000}"


def test_parse_trade_tick_falls_back_ts_event_to_ts_init():
    trade = parse_trade_tick(INSTRUMENT_ID, _record(time_ms=None), ts_init=99)

    assert trade is not None
    assert trade.ts_event == 99


def test_parse_order_book_depth10_uses_qmt_tick_levels():
    depth = parse_order_book_depth10(
        INSTRUMENT_ID,
        {
            "bid_price": [11.39, 11.38, 11.37],
            "ask_price": [11.40, 11.41, 11.42],
            "bid_vol": [100, 200, 300],
            "ask_vol": [400, 500, 600],
            "time_ms": 1733118954000,
            "seq": 88,
        },
        ts_init=7,
    )

    assert depth is not None
    assert depth.instrument_id == INSTRUMENT_ID
    assert str(depth.bids[0].price) == "11.39"
    assert depth.bids[0].size.as_double() == 100
    assert str(depth.bids[2].price) == "11.37"
    assert depth.bids[2].size.as_double() == 300
    assert str(depth.asks[0].price) == "11.40"
    assert depth.asks[0].size.as_double() == 400
    assert str(depth.asks[2].price) == "11.42"
    assert depth.asks[2].size.as_double() == 600
    assert depth.bid_counts[:3] == [0, 0, 0]
    assert depth.ask_counts[:3] == [0, 0, 0]
    assert depth.sequence == 88
    assert depth.ts_event == 1733118954000 * 1_000_000
    assert depth.ts_init == 7


def test_parse_order_book_depth10_drops_empty_depth():
    assert (
        parse_order_book_depth10(
            INSTRUMENT_ID,
            {
                "bid_price": [0.0],
                "ask_price": [11.40],
                "bid_vol": [0],
                "ask_vol": [400],
            },
            ts_init=7,
        )
        is None
    )
