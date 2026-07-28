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

import asyncio
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from nautilus_trader.adapters.qmt.common import parse_equity
from nautilus_trader.adapters.qmt.common import parse_order_book_depth10
from nautilus_trader.adapters.qmt.common import parse_trade_tick
from nautilus_trader.adapters.qmt.common import qmt_instrument_status
from nautilus_trader.adapters.qmt.common import qmt_is_suspended
from nautilus_trader.adapters.qmt.common import qmt_lifecycle_to_order_status
from nautilus_trader.adapters.qmt.common import qmt_trade_flag_to_aggressor
from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.adapters.qmt.execution import QMTExecutionClient
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import GeneratePositionStatusReports
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.objects import Quantity


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=QMT_VENUE)


class _FakeClock:
    def timestamp_ns(self) -> int:
        return 123

    def utc_now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class _FakeHttpClient:
    def __init__(self, positions):
        self._positions = positions

    async def get_positions(self, session_id):
        assert session_id == "SESSION"
        return self._positions


class _FakeLog:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)


class _StubExecClient:
    account_id = AccountId("QMT-TEST")
    _clock = _FakeClock()

    _parse_order_status_report = QMTExecutionClient._parse_order_status_report
    generate_position_status_reports = QMTExecutionClient.generate_position_status_reports

    def __init__(self, positions=()):
        self._http_client = _FakeHttpClient(positions)
        self._log = _FakeLog()
        self._session_id = "SESSION"


class _DispatchStub:
    _dispatch_trading_ws_message = QMTExecutionClient._dispatch_trading_ws_message

    def __init__(self):
        self.orders = []
        self.trades = []
        self.assets = []
        self._log = _FakeLog()

    def _handle_order_update(self, raw_order):
        self.orders.append(raw_order)

    def _handle_trade_update(self, raw_trade):
        self.trades.append(raw_trade)

    def _handle_asset_update(self, asset, force=False):
        self.assets.append((asset, force))


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


def test_qmt_pending_cancel_lifecycle_maps_to_nautilus_pending_cancel():
    assert qmt_lifecycle_to_order_status("PENDING_CANCEL") == OrderStatus.PENDING_CANCEL


def test_parse_order_status_report_uses_zero_avg_px_for_unfilled_order():
    report = _StubExecClient()._parse_order_status_report(
        {
            "stock_code": "000001.SZ",
            "order_id": "7788",
            "client_order_id": "O-1",
            "order_type": 24,
            "price_type": 11,
            "order_volume": 100,
            "traded_volume": 0,
            "price": 12.34,
            "traded_price": 0.0,
            "lifecycle_status": "ACCEPTED",
            "order_time_ms": 1783473582000,
        },
    )

    assert report is not None
    assert report.avg_px == Decimal("0")


def test_generate_position_status_reports_skips_invalid_quantity():
    client = _StubExecClient(
        [
            {"stock_code": "000001.SZ", "volume": -100, "avg_price": 11.0},
            {"stock_code": "000002.SZ", "volume": 200, "avg_price": 12.3},
        ],
    )
    command = GeneratePositionStatusReports(
        instrument_id=None,
        start=None,
        end=None,
        command_id=UUID4(),
        ts_init=client._clock.timestamp_ns(),
    )

    reports = asyncio.run(client.generate_position_status_reports(command))

    assert len(reports) == 1
    assert reports[0].instrument_id == InstrumentId(symbol=Symbol("000002.SZ"), venue=QMT_VENUE)
    assert reports[0].position_side == PositionSide.LONG
    assert reports[0].quantity.as_double() == 200
    assert len(client._log.errors) == 1
    assert "Cannot generate QMT position status report" in client._log.errors[0]
    assert "can't convert negative value to uint128_t" in client._log.errors[0]
    assert "raw_position" in client._log.errors[0]


def test_submit_sell_rejects_when_sellable_precheck_raises():
    rejected = []

    class _SubmitStub:
        _clock = _FakeClock()
        _config = SimpleNamespace(enforce_sellable_position=True, default_limit_price_type=11)
        _session_id = "SESSION"

        _submit_nautilus_order = QMTExecutionClient._submit_nautilus_order

        def _order_price(self, order):
            return 12.34

        async def _get_sellable_volume(self, instrument_id):
            raise RuntimeError("QMT proxy connection error: Connector is closed.")

        def generate_order_rejected(self, **kwargs):
            rejected.append(kwargs)

    order = SimpleNamespace(
        order_type=OrderType.LIMIT,
        strategy_id="S-001",
        instrument_id=INSTRUMENT_ID,
        client_order_id=ClientOrderId("O-1"),
        side=OrderSide.SELL,
        quantity=Quantity.from_int(100),
    )

    asyncio.run(_SubmitStub()._submit_nautilus_order(order))

    assert len(rejected) == 1
    assert rejected[0]["instrument_id"] == INSTRUMENT_ID
    assert rejected[0]["client_order_id"] == ClientOrderId("O-1")
    assert "Connector is closed" in rejected[0]["reason"]


def test_qmt_execution_dispatches_trading_websocket_messages():
    client = _DispatchStub()

    client._dispatch_trading_ws_message(
        {
            "type": "trading",
            "data": {
                "event_type": "order_update",
                "payload": {"order_id": "1", "client_order_id": "O-1"},
            },
        },
    )
    client._dispatch_trading_ws_message(
        {
            "type": "trading",
            "data": {
                "event_type": "trade_update",
                "payload": {"traded_id": "T-1", "client_order_id": "O-1"},
            },
        },
    )
    client._dispatch_trading_ws_message(
        {
            "type": "trading",
            "data": {
                "event_type": "asset_update",
                "payload": {"cash": 1, "frozen_cash": 2},
            },
        },
    )
    client._dispatch_trading_ws_message({"type": "heartbeat"})

    assert client.orders == [{"order_id": "1", "client_order_id": "O-1"}]
    assert client.trades == [{"traded_id": "T-1", "client_order_id": "O-1"}]
    assert client.assets == [({"cash": 1, "frozen_cash": 2}, False)]


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


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"InstrumentStatus": "0"}, 0),  # normal, QMT returns strings
        ({"InstrumentStatus": "6"}, 6),  # suspended
        ({"InstrumentStatus": 6}, 6),  # already an int
        ({"InstrumentStatus": ""}, None),  # empty
        ({"InstrumentStatus": "abc"}, None),  # unparseable
        ({}, None),  # absent
        (None, None),
    ],
)
def test_qmt_instrument_status(fields, expected):
    assert qmt_instrument_status(fields) == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"InstrumentStatus": "0"}, False),  # normal (600519.SH observed)
        ({"InstrumentStatus": "6"}, True),  # suspended (688277.SH observed)
        ({"InstrumentStatus": "1"}, True),  # boundary: >= 1 is suspended
        # IsTrading must NOT override the InstrumentStatus rule — normal stocks
        # read IsTrading=False outside trading hours.
        ({"InstrumentStatus": "0", "IsTrading": "False"}, False),
        ({}, None),  # unknown, not "trading"
        (None, None),
    ],
)
def test_qmt_is_suspended(fields, expected):
    assert qmt_is_suspended(fields) is expected


def test_parse_equity_exposes_suspension_flags():
    suspended = parse_equity(
        symbol="688277.SH",
        fields={"InstrumentName": "天智航-U", "InstrumentStatus": "6", "IsTrading": "False"},
        ts_event=1,
        ts_init=2,
    )
    assert suspended.info["is_suspended"] is True
    assert suspended.info["instrument_status"] == 6

    normal = parse_equity(
        symbol="600519.SH",
        fields={"InstrumentName": "贵州茅台", "InstrumentStatus": "0"},
        ts_event=1,
        ts_init=2,
    )
    assert normal.info["is_suspended"] is False
    assert normal.info["instrument_status"] == 0

    unknown = parse_equity(symbol="000001.SZ", fields={}, ts_event=1, ts_init=2)
    assert unknown.info["is_suspended"] is None
    assert unknown.info["instrument_status"] is None


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
