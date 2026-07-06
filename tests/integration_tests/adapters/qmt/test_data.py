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

import aiohttp
import pytest

from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.adapters.qmt.data import QMTDataClient
from nautilus_trader.model.data import OrderBookDepth10
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=QMT_VENUE)


class _FakeClock:
    def timestamp_ns(self) -> int:
        return 123


class _FakeMessage:
    def __init__(self, payload: dict, msg_type=aiohttp.WSMsgType.TEXT) -> None:
        self._payload = payload
        self.type = msg_type

    def json(self) -> dict:
        return self._payload


class _FakeWebSocket:
    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._messages = messages

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _StubLog:
    def warning(self, *_args, **_kwargs) -> None:
        pass


class _StubDataClient:
    """
    Minimal duck-typed stand-in exposing only what ``_consume_ws`` touches.
    """

    def __init__(self) -> None:
        self._clock = _FakeClock()
        self._log = _StubLog()
        self.handled: list = []

    def _handle_data(self, data) -> None:
        self.handled.append(data)

    # Bind the real coroutine under test to this stub.
    _consume_ws = QMTDataClient._consume_ws
    _dispatch_ws_message = QMTDataClient._dispatch_ws_message


def _quote_event(payload_type: str, data: dict) -> dict:
    return {"type": "quote", "data": {"payload_type": payload_type, "data": data}}


def _run(coro):
    return asyncio.run(coro)


def test_consume_ws_dispatches_l2transaction_to_trade_tick():
    stub = _StubDataClient()
    record = {
        "price": 11.5,
        "volume": 300,
        "trade_index": 42,
        "trade_flag": 1,
        "time_ms": 1733118954000,
    }
    ws = _FakeWebSocket([_FakeMessage(_quote_event("l2transaction", record))])

    _run(stub._consume_ws(ws, trade_instrument_id=INSTRUMENT_ID))

    assert len(stub.handled) == 1
    trade = stub.handled[0]
    assert isinstance(trade, TradeTick)
    assert trade.aggressor_side == AggressorSide.BUYER
    assert str(trade.trade_id) == "42"


def test_consume_ws_drops_l2transaction_without_trade_subscription():
    stub = _StubDataClient()
    record = {"price": 11.5, "volume": 300, "trade_index": 42, "trade_flag": 1}
    ws = _FakeWebSocket([_FakeMessage(_quote_event("l2transaction", record))])

    # Only a quote-tick subscription is active; an l2transaction event must be ignored.
    _run(stub._consume_ws(ws, instrument_id=INSTRUMENT_ID))

    assert stub.handled == []


def test_consume_ws_still_dispatches_tick_and_kline():
    stub = _StubDataClient()
    tick_event = _quote_event(
        "tick",
        {
            "bid_price": [11.39],
            "ask_price": [11.40],
            "bid_vol": [100],
            "ask_vol": [200],
            "time_ms": 1733118954000,
        },
    )
    ws = _FakeWebSocket([_FakeMessage(tick_event)])
    _run(stub._consume_ws(ws, instrument_id=INSTRUMENT_ID))

    assert len(stub.handled) == 1
    assert isinstance(stub.handled[0], QuoteTick)


def test_consume_ws_dispatches_tick_to_order_book_depth():
    stub = _StubDataClient()
    tick_event = _quote_event(
        "tick",
        {
            "bid_price": [11.39, 11.38],
            "ask_price": [11.40, 11.41],
            "bid_vol": [100, 200],
            "ask_vol": [300, 400],
            "time_ms": 1733118954000,
        },
    )
    ws = _FakeWebSocket([_FakeMessage(tick_event)])
    _run(stub._consume_ws(ws, depth_instrument_id=INSTRUMENT_ID))

    assert len(stub.handled) == 1
    assert isinstance(stub.handled[0], OrderBookDepth10)
    assert str(stub.handled[0].bids[1].price) == "11.38"
    assert stub.handled[0].bids[1].size.as_double() == 200
    assert str(stub.handled[0].asks[1].price) == "11.41"
    assert stub.handled[0].asks[1].size.as_double() == 400


def test_consume_ws_ignores_error_messages():
    stub = _StubDataClient()
    ws = _FakeWebSocket([_FakeMessage({"type": "error", "message": "missing-subscription"})])

    _run(stub._consume_ws(ws, trade_instrument_id=INSTRUMENT_ID))

    assert stub.handled == []


@pytest.mark.parametrize("cancel_flag", [3])
def test_consume_ws_drops_cancel_records(cancel_flag):
    stub = _StubDataClient()
    record = {"price": 11.5, "volume": 300, "trade_index": 42, "trade_flag": cancel_flag}
    ws = _FakeWebSocket([_FakeMessage(_quote_event("l2transaction", record))])

    _run(stub._consume_ws(ws, trade_instrument_id=INSTRUMENT_ID))

    assert stub.handled == []
