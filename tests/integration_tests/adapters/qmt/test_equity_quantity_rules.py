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

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from nautilus_trader.adapters.bigqmt.common import parse_equity as parse_bigqmt_equity
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProvider
from nautilus_trader.adapters.qmt.common import parse_equity as parse_qmt_equity
from nautilus_trader.adapters.qmt.equity_quantity_rules import a_share_limit_order_quantity_rules


@pytest.mark.parametrize("parse", [parse_qmt_equity, parse_bigqmt_equity])
@pytest.mark.parametrize(
    ("symbol", "minimum", "maximum", "increment"),
    [
        ("688802.SH", 200, 100_000, 1),
        ("689009.SH", 200, 100_000, 1),
        ("300001.SZ", 100, 300_000, 100),
        ("301001.SZ", 100, 300_000, 100),
        ("600000.SH", 100, 1_000_000, 100),
        ("000001.SZ", 100, 1_000_000, 100),
        ("002001.SZ", 100, 1_000_000, 100),
        ("430047.BJ", 100, 1_000_000, 1),
        ("830799.BJ", 100, 1_000_000, 1),
        ("920001.BJ", 100, 1_000_000, 1),
    ],
)
def test_equity_info_contains_exchange_limit_order_rules(parse, symbol, minimum, maximum, increment):
    fields = {"InstrumentName": "Test equity", "VolumeMultiple": 1}
    instrument = parse(symbol=symbol, fields=fields, ts_event=1, ts_init=2)

    assert instrument.info["limit_order_quantity_rules"] == {
        "min_quantity": minimum,
        "max_quantity": maximum,
        "quantity_increment": increment,
        "allow_odd_lot_sell": True,
    }
    assert instrument.info["fields"] == fields
    # Existing native fields remain unchanged, including support for small sell balances.
    assert instrument.min_quantity is None
    assert instrument.max_quantity is None
    assert instrument.lot_size.as_decimal() == 100


@pytest.mark.parametrize("symbol", ["000001.SH", "510300.SH", "159001.SZ", "899050.BJ"])
def test_non_a_share_instruments_do_not_receive_stock_quantity_rules(symbol):
    assert a_share_limit_order_quantity_rules(symbol) is None


@pytest.mark.asyncio
async def test_bulk_details_populate_instrument_quantity_rules():
    client = MagicMock()
    client.get_all_instrument_details = AsyncMock(return_value={
        "688802.SH": {"InstrumentName": "Test STAR equity", "VolumeMultiple": 1},
    })
    clock = MagicMock()
    clock.timestamp_ns.return_value = 1
    provider = BigQMTInstrumentProvider(client=client, clock=clock)

    await provider.load_all_async()

    assert provider.count == 1
    instrument = provider.list_all()[0]
    assert instrument.info["limit_order_quantity_rules"]["min_quantity"] == 200
    assert instrument.info["limit_order_quantity_rules"]["max_quantity"] == 100_000
    client.get_all_instrument_details.assert_awaited_once_with()
