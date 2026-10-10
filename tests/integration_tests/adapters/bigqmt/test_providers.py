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

from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProvider


@pytest.mark.asyncio
async def test_load_all_uses_single_bulk_instrument_details_call() -> None:
    client = MagicMock()
    client.get_all_instrument_details = AsyncMock(
        return_value={
            "600000.SH": {"InstrumentName": "浦发银行"},
            "000001.SZ": {"InstrumentName": "平安银行"},
        },
    )
    client.get_stock_list_in_sector = AsyncMock()
    client.get_instrument_detail = AsyncMock()
    clock = MagicMock()
    clock.timestamp_ns.return_value = 1
    provider = BigQMTInstrumentProvider(client=client, clock=clock)

    await provider.load_all_async()

    assert provider.count == 2
    client.get_all_instrument_details.assert_awaited_once_with()
    client.get_stock_list_in_sector.assert_not_awaited()
    client.get_instrument_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_all_does_not_fall_back_when_bulk_call_fails() -> None:
    client = MagicMock()
    client.get_all_instrument_details = AsyncMock(side_effect=RuntimeError("not supported"))
    client.get_stock_list_in_sector = AsyncMock(return_value=["600000.SH"])
    client.get_instrument_detail = AsyncMock(
        return_value={"InstrumentName": "浦发银行"},
    )
    clock = MagicMock()
    clock.timestamp_ns.return_value = 1
    provider = BigQMTInstrumentProvider(client=client, clock=clock)

    with pytest.raises(RuntimeError, match="not supported"):
        await provider.load_all_async()

    assert provider.count == 0
    client.get_all_instrument_details.assert_awaited_once_with()
    client.get_stock_list_in_sector.assert_not_awaited()
    client.get_instrument_detail.assert_not_awaited()
