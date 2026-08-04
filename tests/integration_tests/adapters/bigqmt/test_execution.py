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

from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.adapters.bigqmt.execution import BigQMTExecutionClient
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import GeneratePositionStatusReports
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol


INSTRUMENT_ID = InstrumentId(symbol=Symbol("000001.SZ"), venue=BIG_QMT_VENUE)


class _FakeClock:
    def timestamp_ns(self) -> int:
        return 123

    def utc_now(self) -> datetime:
        return datetime.now(tz=UTC)


class _FakeClient:
    def __init__(self, positions=()):
        self._positions = list(positions)

    async def get_positions(self):
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
    account_id = AccountId("BIGQMT-TEST")
    _clock = _FakeClock()

    _parse_order_status_report = BigQMTExecutionClient._parse_order_status_report
    _parse_fill_report = BigQMTExecutionClient._parse_fill_report
    _order_side = BigQMTExecutionClient._order_side
    generate_position_status_reports = BigQMTExecutionClient.generate_position_status_reports

    def __init__(self, positions=()):
        self._client = _FakeClient(positions)
        self._log = _FakeLog()


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
    report = _StubExecClient()._parse_fill_report(
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
