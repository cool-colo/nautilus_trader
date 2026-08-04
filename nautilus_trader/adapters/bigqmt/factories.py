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
from weakref import WeakValueDictionary

from nautilus_trader.adapters.bigqmt.client import BigQMTClient
from nautilus_trader.adapters.bigqmt.config import BigQMTDataClientConfig
from nautilus_trader.adapters.bigqmt.config import BigQMTExecClientConfig
from nautilus_trader.adapters.bigqmt.data import BigQMTDataClient
from nautilus_trader.adapters.bigqmt.execution import BigQMTExecutionClient
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProvider
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProviderConfig
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.live.factories import LiveExecClientFactory


BIG_QMT_INSTRUMENT_PROVIDERS: WeakValueDictionary[tuple, BigQMTInstrumentProvider] = (
    WeakValueDictionary()
)
BIG_QMT_CLIENTS: WeakValueDictionary[tuple, BigQMTClient] = WeakValueDictionary()


def _redis_config(config: BigQMTDataClientConfig | BigQMTExecClientConfig) -> dict:
    return {
        "host": config.redis_host,
        "port": config.redis_port,
        "db": config.redis_db,
        "password": config.redis_password,
        "transport": config.transport,
    }


def get_cached_bigqmt_client(
    account_id: str,
    redis_config: dict,
    timeout_secs: float,
    transport: str,
) -> BigQMTClient:
    key = (
        str(account_id),
        str(redis_config["host"]),
        int(redis_config["port"]),
        int(redis_config["db"]),
        redis_config["password"],
        str(transport),
        float(timeout_secs),
    )
    client = BIG_QMT_CLIENTS.get(key)
    if client is None:
        client = BigQMTClient(
            account_id=account_id,
            redis_config=redis_config,
            timeout_secs=timeout_secs,
            transport=transport,
        )
        BIG_QMT_CLIENTS[key] = client
    return client


def get_cached_bigqmt_instrument_provider(
    client: BigQMTClient,
    clock: LiveClock,
    config: BigQMTInstrumentProviderConfig | None,
) -> BigQMTInstrumentProvider:
    provider_config = config or BigQMTInstrumentProviderConfig()
    key = (id(client), provider_config)
    provider = BIG_QMT_INSTRUMENT_PROVIDERS.get(key)
    if provider is None:
        provider = BigQMTInstrumentProvider(
            client=client,
            clock=clock,
            config=provider_config,
        )
        BIG_QMT_INSTRUMENT_PROVIDERS[key] = provider
    return provider


class BigQMTLiveDataClientFactory(LiveDataClientFactory):
    """
    Provides a BigQMT live data client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: BigQMTDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> BigQMTDataClient:
        client = get_cached_bigqmt_client(
            account_id=config.account_id,
            redis_config=_redis_config(config),
            timeout_secs=config.rpc_timeout_secs,
            transport=config.transport,
        )
        provider = get_cached_bigqmt_instrument_provider(
            client=client,
            clock=clock,
            config=config.instrument_provider,
        )
        return BigQMTDataClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )


class BigQMTLiveExecClientFactory(LiveExecClientFactory):
    """
    Provides a BigQMT live execution client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: BigQMTExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> BigQMTExecutionClient:
        client = get_cached_bigqmt_client(
            account_id=config.account_id,
            redis_config=_redis_config(config),
            timeout_secs=config.rpc_timeout_secs,
            transport=config.transport,
        )
        provider = get_cached_bigqmt_instrument_provider(
            client=client,
            clock=clock,
            config=config.instrument_provider,
        )
        return BigQMTExecutionClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )
