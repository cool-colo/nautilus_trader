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

from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.adapters.bigqmt.data import BigQMTDataClient
from nautilus_trader.adapters.bigqmt.data import _lookup_symbol
from nautilus_trader.model.data import OrderBookDepth10
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=BIG_QMT_VENUE)


class _FakeClock:
    def timestamp_ns(self) -> int:
        return 123


class _FakeLog:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        pass


class _StubDataClient:
    _handle_quote_push = BigQMTDataClient._handle_quote_push

    def __init__(self):
        self._log = _FakeLog()
        self._clock = _FakeClock()
        self._push_quote_ids = set()
        self._push_depth_ids = set()
        self.handled = []

    def _handle_data(self, data):
        self.handled.append(data)


def _full_tick_payload():
    return {
        "bidPrice": [10.0, 9.99, 9.98],
        "askPrice": [10.01, 10.02, 10.03],
        "bidVol": [200, 300, 400],
        "askVol": [150, 250, 350],
        "time": 1700000000000,
    }


def test_handle_quote_push_forwards_quote_tick_for_quote_subscription():
    client = _StubDataClient()
    client._push_quote_ids.add(INSTRUMENT_ID)
    payload = {"000001.SZ": _full_tick_payload()}

    client._handle_quote_push(payload)

    assert len(client.handled) == 1
    tick = client.handled[0]
    assert isinstance(tick, QuoteTick)
    assert tick.instrument_id == INSTRUMENT_ID
    assert str(tick.bid_price) == "10.00"
    assert str(tick.ask_price) == "10.01"


def test_handle_quote_push_forwards_depth_for_depth_subscription():
    client = _StubDataClient()
    client._push_depth_ids.add(INSTRUMENT_ID)
    payload = {"000001.SZ": _full_tick_payload()}

    client._handle_quote_push(payload)

    assert len(client.handled) == 1
    depth = client.handled[0]
    assert isinstance(depth, OrderBookDepth10)
    assert depth.instrument_id == INSTRUMENT_ID
    # OrderBookDepth10 pads to 10 levels with zero placeholders; assert live levels.
    assert len([b for b in depth.bids if b.price > 0]) == 3
    assert len([a for a in depth.asks if a.price > 0]) == 3


def test_handle_quote_push_forwards_both_for_quote_and_depth():
    client = _StubDataClient()
    client._push_quote_ids.add(INSTRUMENT_ID)
    client._push_depth_ids.add(INSTRUMENT_ID)
    payload = {"000001.SZ": _full_tick_payload()}

    client._handle_quote_push(payload)

    assert len(client.handled) == 2
    assert any(isinstance(d, QuoteTick) for d in client.handled)
    assert any(isinstance(d, OrderBookDepth10) for d in client.handled)


def test_handle_quote_push_skips_unsubscribed_or_empty_code():
    client = _StubDataClient()
    client._push_quote_ids.add(INSTRUMENT_ID)

    # Code not subscribed -> nothing forwarded.
    client._handle_quote_push({"000002.SZ": _full_tick_payload()})
    assert client.handled == []

    # Code subscribed but payload empty (incremental partial delta) -> skip.
    client._handle_quote_push({"000001.SZ": {}})
    assert client.handled == []


def test_handle_quote_push_skips_missing_arrays():
    client = _StubDataClient()
    client._push_quote_ids.add(INSTRUMENT_ID)

    # Partial incremental delta without bid/ask arrays -> converter returns None.
    client._handle_quote_push({"000001.SZ": {"time": 1700000000000, "lastPrice": 10.0}})
    assert client.handled == []


def test_lookup_symbol_case_insensitive():
    data = {"000001.SZ": {"time": 1}}
    assert _lookup_symbol(data, "000001.SZ") == {"time": 1}
    assert _lookup_symbol(data, "000001.sz") == {"time": 1}
    assert _lookup_symbol(data, "000001.SH") is None
