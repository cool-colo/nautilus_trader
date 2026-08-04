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
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from nautilus_trader.adapters.bigqmt.client import BigQMTClient
from nautilus_trader.adapters.bigqmt.factories import BIG_QMT_CLIENTS
from nautilus_trader.adapters.bigqmt.factories import BIG_QMT_INSTRUMENT_PROVIDERS
from nautilus_trader.adapters.bigqmt.factories import get_cached_bigqmt_client
from nautilus_trader.adapters.bigqmt.factories import get_cached_bigqmt_instrument_provider
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProviderConfig


@pytest.fixture(autouse=True)
def clear_bigqmt_factory_caches():
    BIG_QMT_CLIENTS.clear()
    BIG_QMT_INSTRUMENT_PROVIDERS.clear()
    yield
    BIG_QMT_CLIENTS.clear()
    BIG_QMT_INSTRUMENT_PROVIDERS.clear()


def _client() -> BigQMTClient:
    return get_cached_bigqmt_client(
        account_id="12345678",
        redis_config={
            "host": "redis.example.com",
            "port": 6379,
            "db": 5,
            "password": "secret",
            "transport": "redis",
        },
        timeout_secs=6.0,
        transport="redis",
    )


def test_get_cached_bigqmt_client_reuses_matching_client() -> None:
    assert _client() is _client()


def test_get_cached_bigqmt_instrument_provider_reuses_provider_for_shared_client() -> None:
    client = _client()
    clock = MagicMock()
    config = BigQMTInstrumentProviderConfig(load_all=True)

    provider1 = get_cached_bigqmt_instrument_provider(client, clock, config)
    provider2 = get_cached_bigqmt_instrument_provider(client, clock, config)

    assert provider1 is provider2


@pytest.mark.asyncio
async def test_shared_provider_initializes_only_once() -> None:
    client = _client()
    provider = get_cached_bigqmt_instrument_provider(
        client,
        MagicMock(),
        BigQMTInstrumentProviderConfig(load_all=True),
    )
    provider.load_all_async = AsyncMock()

    await asyncio.gather(provider.initialize(), provider.initialize())

    provider.load_all_async.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_shared_client_closes_after_last_connection_user() -> None:
    client = _client()
    trader = MagicMock()
    client._trader = trader

    await client.connect()
    await client.connect()
    await client.close()

    assert client.trader is trader
    trader.stop.assert_not_called()

    await client.close()

    trader.stop.assert_called_once_with()
    assert client.trader is None
