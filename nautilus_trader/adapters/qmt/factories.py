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

from nautilus_trader.adapters.qmt.config import QMTDataClientConfig
from nautilus_trader.adapters.qmt.config import QMTExecClientConfig
from nautilus_trader.adapters.qmt.data import QMTDataClient
from nautilus_trader.adapters.qmt.execution import QMTExecutionClient
from nautilus_trader.adapters.qmt.http import QMTHttpClient
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProvider
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProviderConfig
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.live.factories import LiveExecClientFactory


QMT_INSTRUMENT_PROVIDERS: dict[tuple, QMTInstrumentProvider] = {}


def get_cached_qmt_http_client(
    base_url: str,
    api_key: str | None,
    timeout_secs: float,
) -> QMTHttpClient:
    return QMTHttpClient(
        base_url=base_url,
        api_key=api_key,
        timeout_secs=timeout_secs,
    )


def get_cached_qmt_instrument_provider(
    client: QMTHttpClient,
    clock: LiveClock,
    config: QMTInstrumentProviderConfig | None,
) -> QMTInstrumentProvider:
    provider_config = config or QMTInstrumentProviderConfig()
    key = (id(client), hash(provider_config))
    if key not in QMT_INSTRUMENT_PROVIDERS:
        QMT_INSTRUMENT_PROVIDERS[key] = QMTInstrumentProvider(
            client=client,
            clock=clock,
            config=provider_config,
        )
    return QMT_INSTRUMENT_PROVIDERS[key]


class QMTLiveDataClientFactory(LiveDataClientFactory):
    """
    Provides a QMT live data client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: QMTDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> QMTDataClient:
        http_client = get_cached_qmt_http_client(
            base_url=config.base_url_http,
            api_key=config.api_key,
            timeout_secs=config.request_timeout_secs,
        )
        provider = get_cached_qmt_instrument_provider(
            client=http_client,
            clock=clock,
            config=config.instrument_provider,
        )
        return QMTDataClient(
            loop=loop,
            http_client=http_client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )


class QMTLiveExecClientFactory(LiveExecClientFactory):
    """
    Provides a QMT live execution client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: QMTExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> QMTExecutionClient:
        http_client = get_cached_qmt_http_client(
            base_url=config.base_url_http,
            api_key=config.api_key,
            timeout_secs=config.request_timeout_secs,
        )
        provider = get_cached_qmt_instrument_provider(
            client=http_client,
            clock=clock,
            config=config.instrument_provider,
        )
        return QMTExecutionClient(
            loop=loop,
            http_client=http_client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )
