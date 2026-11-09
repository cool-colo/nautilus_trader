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
from types import SimpleNamespace

import pytest

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


class _FakeLoop:
    def __init__(self):
        self.now = 10.0

    def time(self) -> float:
        return self.now


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
    _handle_full_tick_payload = BigQMTDataClient._handle_full_tick_payload

    def __init__(self):
        self._log = _FakeLog()
        self._clock = _FakeClock()
        self._loop = _FakeLoop()
        self._push_quote_ids = set()
        self._push_depth_ids = set()
        self._push_last_received_at = None
        self._push_fallback_active = False
        self.handled = []

    def _handle_data(self, data):
        self.handled.append(data)


class _FallbackClient(_StubDataClient):
    _monitor_quote_push = BigQMTDataClient._monitor_quote_push

    def __init__(self):
        super().__init__()
        self._loop.now = 0.0
        self._config = SimpleNamespace(
            quote_push_stale_after_secs=30.0,
            quote_push_fallback_poll_interval_secs=60.0,
        )
        self._client = SimpleNamespace(get_full_tick=self._get_full_tick)
        self._push_codes = {"000002.SZ", "000001.SZ"}
        self._push_subscription_started_at = 0.0
        self._push_monitor_task = object()
        self.snapshot_requests = []

    async def _get_full_tick(self, codes):
        self.snapshot_requests.append(list(codes))
        return {}


class _SubscriptionClient:
    _sync_quote_subscription = BigQMTDataClient._sync_quote_subscription
    _retry_quote_subscription = BigQMTDataClient._retry_quote_subscription

    def __init__(self):
        self._quote_push_supported = True
        self._push_sync_lock = asyncio.Lock()
        self._push_codes = {"000001.SZ", "000002.SZ"}
        self._push_subscribed_codes = frozenset({"000001.SZ"})
        self._push_sub_id = 1
        self._push_retry_task = None
        self._push_subscription_started_at = 1.0
        self._loop = _FakeLoop()
        self._log = _FakeLog()
        self.subscribe_calls = []
        self.unsubscribe_calls = []
        self._client = SimpleNamespace(
            subscribe_whole_quote=self._subscribe_whole_quote,
            unsubscribe_whole_quote=self._unsubscribe_whole_quote,
        )

    def _on_quote_push(self, _data):
        pass

    def _ensure_push_retry(self):
        # The test drives the retry coroutine directly.
        pass

    async def _subscribe_whole_quote(self, codes, _callback):
        self.subscribe_calls.append(list(codes))
        if len(self.subscribe_calls) == 1:
            raise RuntimeError("transient control-plane failure")
        return 2

    async def _unsubscribe_whole_quote(self, sub_id):
        self.unsubscribe_calls.append(sub_id)


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


def test_handle_quote_push_marks_push_healthy_and_stops_fallback():
    client = _StubDataClient()
    client._push_quote_ids.add(INSTRUMENT_ID)
    client._push_fallback_active = True

    client._handle_quote_push({"000001.SZ": _full_tick_payload()})

    assert client._push_last_received_at == 10.0
    assert client._push_fallback_active is False


def test_fallback_payload_does_not_mark_push_healthy():
    client = _StubDataClient()
    client._push_quote_ids.add(INSTRUMENT_ID)
    client._push_fallback_active = True

    client._handle_full_tick_payload({"000001.SZ": _full_tick_payload()})

    assert len(client.handled) == 1
    assert client._push_last_received_at is None
    assert client._push_fallback_active is True


def test_push_monitor_batches_union_only_after_silence_and_stops_on_recovery(monkeypatch):
    client = _FallbackClient()
    healthy_push_times = {20.0, 40.0, 55.0, 120.0, 140.0, 160.0, 180.0}

    async def advance_clock(delay):
        client._loop.now += delay
        if client._loop.now in healthy_push_times:
            client._handle_quote_push({"000001.SZ": _full_tick_payload()})
        if client._loop.now >= 190.0:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "nautilus_trader.adapters.bigqmt.data.asyncio.sleep",
        advance_clock,
    )

    async def run_monitor():
        client._push_monitor_task = asyncio.current_task()
        await client._monitor_quote_push()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_monitor())

    # Pushes through t=55 keep the feed healthy. One 60-second silent period
    # triggers one batched snapshot at t=115; the t=120 push then stops the
    # fallback, and later healthy pushes prevent any additional polling.
    assert client.snapshot_requests == [["000001.SZ", "000002.SZ"]]
    assert client._push_fallback_active is False
    assert client._push_monitor_task is None


def test_subscription_retry_is_not_short_circuited_by_old_subscription_id(monkeypatch):
    client = _SubscriptionClient()

    async def no_delay(_delay):
        pass

    monkeypatch.setattr(
        "nautilus_trader.adapters.bigqmt.data.asyncio.sleep",
        no_delay,
    )
    asyncio.run(client._retry_quote_subscription())

    assert client.subscribe_calls == [
        ["000001.SZ", "000002.SZ"],
        ["000001.SZ", "000002.SZ"],
    ]
    assert client._push_sub_id == 2
    assert client._push_subscribed_codes == frozenset({"000001.SZ", "000002.SZ"})
    assert client.unsubscribe_calls == [1]


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
