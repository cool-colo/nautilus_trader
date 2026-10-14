# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at http://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

import asyncio
from unittest.mock import MagicMock

import pytest

from bigqmt_signal_trader.xtquant_compat import BigQmtXtTrader
from bigqmt_signal_trader.xtquant_compat import StockAccount
from nautilus_trader.adapters.bigqmt.client import BigQMTClient


@pytest.mark.parametrize("broker_success", [True, False])
def test_cancel_order_maps_new_client_result_and_preserves_broker_id(broker_success):
    trader = BigQmtXtTrader(account_id="test-account")
    trader.client = MagicMock(account_id="test-account")
    trader.client.call.return_value = {"success": broker_success}
    trader.stop = MagicMock()
    client = BigQMTClient("test-account", {})
    client._trader = trader
    client._account = StockAccount("test-account")
    client._connection_users = 1

    async def cancel_and_close():
        try:
            return await client.cancel_order("000001234567")
        finally:
            await client.close()

    assert asyncio.run(cancel_and_close()) is broker_success
    trader.client.call.assert_called_once_with(
        "cancel_order_stock_sysid",
        {
            "account_id": "test-account",
            "account_type": "STOCK",
            "market": "",
            "order_sysid": "000001234567",
        },
        account_id="test-account",
    )
