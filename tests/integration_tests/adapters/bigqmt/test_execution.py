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
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.adapters.bigqmt.execution import BigQMTExecutionClient
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import GeneratePositionStatusReports
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=BIG_QMT_VENUE)


class _FakeClock:
    def timestamp_ns(self) -> int:
        return 123

    def utc_now(self) -> datetime:
        return datetime.now(tz=UTC)


class _FakeClient:
    def __init__(self, positions=(), submit_response=None):
        self._positions = list(positions)
        self._submit_response = submit_response or {}
        self.submit_calls = []

    async def get_positions(self):
        return self._positions

    async def submit_order(self, **kwargs):
        self.submit_calls.append(kwargs)
        return self._submit_response


class _FakeLog:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.infos = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        self.infos.append(message)


class _FakeCache:
    def __init__(self, orders):
        self._orders = orders

    def order(self, client_order_id):
        return self._orders.get(client_order_id)


class _StubExecClient:
    account_id = AccountId("BIGQMT-TEST")
    _clock = _FakeClock()

    _handle_asset_update = BigQMTExecutionClient._handle_asset_update
    _handle_order_update = BigQMTExecutionClient._handle_order_update
    _handle_terminal_order_update = BigQMTExecutionClient._handle_terminal_order_update
    _handle_order_error = BigQMTExecutionClient._handle_order_error
    _handle_cancel_error = BigQMTExecutionClient._handle_cancel_error
    _resolve_error_ids = BigQMTExecutionClient._resolve_error_ids
    _handle_trade_update = BigQMTExecutionClient._handle_trade_update
    _parse_order_status_report = BigQMTExecutionClient._parse_order_status_report
    _parse_fill_report = BigQMTExecutionClient._parse_fill_report
    _resolve_client_order_id = BigQMTExecutionClient._resolve_client_order_id
    _order_side = BigQMTExecutionClient._order_side
    _order_price = BigQMTExecutionClient._order_price
    _submit_nautilus_order = BigQMTExecutionClient._submit_nautilus_order
    generate_position_status_reports = BigQMTExecutionClient.generate_position_status_reports

    def __init__(self, positions=(), submit_response=None):
        self._client = _FakeClient(positions, submit_response)
        self._log = _FakeLog()
        self._config = SimpleNamespace(
            default_limit_price_type=11,
            default_market_price_type=5,
            enforce_sellable_position=False,
            strategy_name="test",
        )
        self._last_account_key = None
        self._known_order_status = {}
        self._known_client_order_ids = {}
        self._seen_trade_ids = set()
        self._terminal_events = set()
        self._orders = {}
        self._cache = _FakeCache(self._orders)
        self.account_states = []
        self.submitted = []
        self.accepted = []
        self.rejected = []
        self.cancel_rejected = []
        self.filled = []

    def generate_account_state(self, **kwargs):
        self.account_states.append(kwargs)

    def generate_order_submitted(self, **kwargs):
        self.submitted.append(kwargs)

    def generate_order_accepted(self, **kwargs):
        self.accepted.append(kwargs)

    def generate_order_rejected(self, **kwargs):
        self.rejected.append(kwargs)

    def generate_order_cancel_rejected(self, **kwargs):
        self.cancel_rejected.append(kwargs)

    def generate_order_filled(self, **kwargs):
        self.filled.append(kwargs)


def _test_order(client_order_id="O-1"):
    return SimpleNamespace(
        strategy_id="S-1",
        instrument_id=INSTRUMENT_ID,
        client_order_id=ClientOrderId(client_order_id),
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        quantity=Quantity.from_int(100),
        price=Price.from_str("10.00"),
    )


def test_parse_order_status_report_maps_bigqmt_fields():
    report = _StubExecClient()._parse_order_status_report(
        {
            "stock_code": "000001.SZ",
            "order_sysid": "7788",
            "order_remark": "O-1",
            "action": "SELL",
            "price_type": 11,
            "order_volume": 100,
            "traded_volume": 0,
            "price": 12.34,
            "traded_price": 0.0,
            "order_status": 50,  # reported -> ACCEPTED
        },
    )
    assert report is not None
    assert str(report.venue_order_id) == "7788"
    assert str(report.client_order_id) == "O-1"
    assert report.order_side == OrderSide.SELL
    assert report.order_type == OrderType.LIMIT
    assert report.order_status == OrderStatus.ACCEPTED
    assert str(report.price) == "12.34"
    assert report.avg_px == Decimal(0)


def test_parse_order_status_report_drops_zero_volume():
    report = _StubExecClient()._parse_order_status_report(
        {"stock_code": "000001.SZ", "order_sysid": "1", "order_volume": 0},
    )
    assert report is None


def test_parse_fill_report_maps_bigqmt_fields():
    client = _StubExecClient()

    report = client._parse_fill_report(
        {
            "stock_code": "000001.SZ",
            "order_sysid": "7788",
            "order_remark": "O-1",
            "trade_id": "T-42",
            "action": "BUY",
            "traded_volume": 100,
            "traded_price": 11.5,
            "commission": 0.5,
        },
    )
    assert report is not None
    assert str(report.trade_id) == "T-42"
    assert str(report.venue_order_id) == "7788"
    assert str(report.client_order_id) == "O-1"
    assert report.order_side == OrderSide.BUY
    assert report.last_qty.as_double() == 100
    assert str(report.last_px) == "11.50"


def test_parse_fill_report_drops_missing_ids():
    assert _StubExecClient()._parse_fill_report({"traded_price": 10.0, "traded_volume": 100}) is None


def test_order_side_prefers_action_then_order_type():
    client = _StubExecClient()
    assert client._order_side({"action": "SELL", "order_type": 23}) == OrderSide.SELL
    assert client._order_side({"order_type": 24}) == OrderSide.SELL
    assert client._order_side({"order_type": 23}) == OrderSide.BUY


def test_asset_update_republishes_when_valuation_changes():
    client = _StubExecClient()
    initial_asset = {
        "cash": "100.00",
        "frozen_cash": "10.00",
        "total_asset": "1000.00",
        "market_value": "890.00",
    }
    changed_valuation = {**initial_asset, "total_asset": "1001.00", "market_value": "891.00"}

    client._handle_asset_update(initial_asset)
    client._handle_asset_update(changed_valuation)
    client._handle_asset_update(changed_valuation)

    assert len(client.account_states) == 2
    assert client.account_states[-1]["info"] == changed_valuation


def test_submit_keeps_order_submitted_when_passorder_is_asynchronous():
    client = _StubExecClient(
        submit_response={
            "status": "SUBMITTED",
            "user_order_id": "O-1",
            "order_sys_id": None,
            "message": "passorder submitted",
        },
    )
    order = _test_order()

    asyncio.run(client._submit_nautilus_order(order))

    assert len(client.submitted) == 1
    assert client.accepted == []
    assert client.rejected == []
    assert client._client.submit_calls[0]["order_remark"] == "O-1"
    assert client._known_client_order_ids == {}


def test_order_callback_without_remark_does_not_block_later_poll_correlation():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order
    venue_order_id = VenueOrderId("7788")

    client._handle_order_update({"order_sysid": "7788", "order_status": 50})

    assert client._known_order_status == {}
    assert client.accepted == []

    client._handle_order_update(
        {"order_sysid": "7788", "order_status": 50, "order_remark": "O-1"},
    )

    assert client._known_order_status[venue_order_id] == OrderStatus.ACCEPTED
    assert client._known_client_order_ids[venue_order_id] == order.client_order_id
    assert len(client.accepted) == 1


def test_order_callback_ignores_client_order_id_used_as_temporary_order_id():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order

    client._handle_order_update(
        {
            "order_sysid": "O-1",
            "order_id": "O-1",
            "order_remark": "O-1",
            "order_status": 50,
        },
    )

    assert client._known_order_status == {}
    assert client._known_client_order_ids == {}
    assert client.accepted == []

    client._handle_order_update(
        {
            "order_sysid": "1707",
            "order_id": "1707",
            "order_remark": "O-1",
            "order_status": 50,
        },
    )

    venue_order_id = VenueOrderId("1707")
    assert client._known_order_status[venue_order_id] == OrderStatus.ACCEPTED
    assert client._known_client_order_ids[venue_order_id] == order.client_order_id
    assert client.accepted[0]["venue_order_id"] == venue_order_id


def test_parse_order_status_report_drops_temporary_client_order_id():
    report = _StubExecClient()._parse_order_status_report(
        {
            "order_sysid": "O-1",
            "order_id": "O-1",
            "order_remark": "O-1",
            "stock_code": "000001.SZ",
            "order_volume": 100,
        },
    )

    assert report is None


def test_trade_callback_without_remark_can_be_recovered_by_later_poll():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order
    raw_trade = {
        "stock_code": "000001.SZ",
        "order_sysid": "7788",
        "trade_id": "T-42",
        "action": "BUY",
        "traded_volume": 100,
        "traded_price": 11.5,
    }

    client._handle_trade_update(raw_trade)

    assert client._seen_trade_ids == set()
    assert client.filled == []

    client._handle_trade_update({**raw_trade, "order_remark": "O-1"})

    assert client._seen_trade_ids == {TradeId("T-42")}
    assert len(client.filled) == 1


def test_trade_callback_rejects_source_instrument_without_exchange_suffix():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order

    client._handle_trade_update(
        {
            "stock_code": "000001",
            "order_sysid": "7788",
            "order_remark": "O-1",
            "trade_id": "T-42",
            "action": "BUY",
            "traded_volume": 100,
            "traded_price": 11.5,
        },
    )

    assert client.filled == []
    assert len(client._log.errors) == 1
    assert "source stock_code must include a supported exchange suffix" in client._log.errors[0]


def test_generate_position_status_reports_skips_invalid_quantity():
    client = _StubExecClient(
        [
            {"stock_code": "000001.SZ", "volume": -100, "avg_price": 11.0},
            {"stock_code": "000002.SZ", "volume": 200, "avg_price": 12.3, "can_use_volume": 100},
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
    assert reports[0].instrument_id == InstrumentId(symbol=Symbol("000002.SZ"), venue=BIG_QMT_VENUE)
    assert reports[0].position_side == PositionSide.LONG
    assert reports[0].quantity.as_double() == 200
    assert reports[0].can_use_volume == Decimal(100)
    assert len(client._log.errors) == 1
    assert "Cannot generate BigQMT position status report" in client._log.errors[0]


def test_order_error_emits_rejected_with_counter_reason():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order
    raw = {
        "stock_code": "000001.SZ",
        "order_sys_id": "7788",
        "order_remark": "O-1",
        "error_id": 1,
        "error_msg": "[COUNTER] 资金可用余额不足，尚需100.00",  # noqa: RUF001
    }

    client._handle_order_error(raw)

    assert len(client.rejected) == 1
    assert client.rejected[0]["client_order_id"] == order.client_order_id
    assert "资金可用余额不足" in client.rejected[0]["reason"]
    assert client._known_order_status[VenueOrderId("7788")] == OrderStatus.REJECTED
    assert VenueOrderId("7788") in client._terminal_events


def test_order_error_dedupes_against_poll_loop_terminal_event():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order
    venue_order_id = VenueOrderId("7788")

    # The poll loop already emitted the reject via a status-57 terminal update.
    client._handle_terminal_order_update(
        order,
        {"order_sysid": "7788", "order_remark": "O-1", "status_msg": "rejected"},
        venue_order_id,
        order.client_order_id,
        OrderStatus.REJECTED,
        123,
    )

    client._handle_order_error(
        {
            "stock_code": "000001.SZ",
            "order_sys_id": "7788",
            "order_remark": "O-1",
            "error_msg": "rejected",
        },
    )

    assert len(client.rejected) == 1  # from the poll path only; push deduped


def test_order_error_without_remark_uses_known_client_order_id_map():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order
    client._known_client_order_ids[VenueOrderId("7788")] = order.client_order_id

    client._handle_order_error(
        {
            "stock_code": "000001.SZ",
            "order_sys_id": "7788",
            "error_msg": "rejected",
        },
    )

    assert len(client.rejected) == 1
    assert client.rejected[0]["client_order_id"] == order.client_order_id


def test_order_error_without_cached_order_is_ignored():
    client = _StubExecClient()

    client._handle_order_error(
        {
            "stock_code": "000001.SZ",
            "order_sys_id": "7788",
            "order_remark": "O-unknown",
            "error_msg": "rejected",
        },
    )

    assert client.rejected == []
    assert len(client._log.warnings) == 1
    assert "no cached order" in client._log.warnings[0]


def test_cancel_error_emits_cancel_rejected():
    client = _StubExecClient()
    order = _test_order()
    client._orders[order.client_order_id] = order
    raw = {
        "stock_code": "000001.SZ",
        "order_sys_id": "7788",
        "order_remark": "O-1",
        "error_msg": "撤单失败，订单已成交",  # noqa: RUF001
    }

    client._handle_cancel_error(raw)

    assert len(client.cancel_rejected) == 1
    assert client.cancel_rejected[0]["client_order_id"] == order.client_order_id
    assert client.cancel_rejected[0]["venue_order_id"] == VenueOrderId("7788")
    assert "撤单失败" in client.cancel_rejected[0]["reason"]
    # A cancel rejection is not terminal; order state untouched.
    assert VenueOrderId("7788") not in client._terminal_events
    assert client._known_order_status == {}


def test_cancel_error_without_cache_or_known_ids_is_ignored():
    client = _StubExecClient()

    client._handle_cancel_error(
        {
            "stock_code": "000001.SZ",
            "order_sys_id": "7788",
            "order_remark": "O-unknown",
            "error_msg": "撤单失败",
        },
    )

    assert client.cancel_rejected == []
    assert len(client._log.warnings) == 1
    assert "no cached order" in client._log.warnings[0]
