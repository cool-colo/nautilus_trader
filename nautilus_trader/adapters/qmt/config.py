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

from nautilus_trader.adapters.qmt.constants import QMT_DEFAULT_HTTP_URL
from nautilus_trader.adapters.qmt.constants import QMT_DEFAULT_WS_URL
from nautilus_trader.adapters.qmt.constants import QMT_PRICE_TYPE_FIX_PRICE
from nautilus_trader.adapters.qmt.constants import QMT_PRICE_TYPE_LATEST_PRICE
from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProviderConfig
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.config import LiveExecClientConfig
from nautilus_trader.config import PositiveFloat
from nautilus_trader.config import PositiveInt
from nautilus_trader.model.identifiers import Venue


class QMTDataClientConfig(LiveDataClientConfig, frozen=True):
    """
    Configuration for ``QMTDataClient`` instances.

    Parameters
    ----------
    base_url_http : str, default "http://127.0.0.1:8000"
        Base HTTP URL for quant-qmt-proxy.
    base_url_ws : str, default "ws://127.0.0.1:8000"
        Base WebSocket URL for quant-qmt-proxy quote streams.
    api_key : str, optional
        Bearer token for quant-qmt-proxy, if proxy authentication is enabled.
    instrument_provider : QMTInstrumentProviderConfig, optional
        Instrument loading configuration.
    request_timeout_secs : PositiveFloat, default 10.0
        HTTP request timeout.
    adjust_type : str, default "none"
        QMT dividend adjustment mode used for historical data requests.
    """

    base_url_http: str = QMT_DEFAULT_HTTP_URL
    base_url_ws: str = QMT_DEFAULT_WS_URL
    api_key: str | None = None
    venue: Venue = QMT_VENUE
    instrument_provider: QMTInstrumentProviderConfig | None = None
    request_timeout_secs: PositiveFloat = 10.0
    adjust_type: str = "none"


class QMTExecClientConfig(LiveExecClientConfig, frozen=True, kw_only=True):
    """
    Configuration for ``QMTExecutionClient`` instances.

    Parameters
    ----------
    account_id : str
        MiniQMT stock account ID.
    account_type : str, default "STOCK"
        QMT security account type.
    base_url_http : str, default "http://127.0.0.1:8000"
        Base HTTP URL for quant-qmt-proxy.
    base_url_ws : str, default "ws://127.0.0.1:8000"
        Base WebSocket URL for quant-qmt-proxy trading event streams.
    api_key : str, optional
        Bearer token for quant-qmt-proxy, if proxy authentication is enabled.
    poll_interval_secs : PositiveFloat, default 1.0
        Poll interval for order, trade and asset synchronization.
    default_market_price_type : PositiveInt, default 5
        QMT price type used for Nautilus market orders.
    default_limit_price_type : PositiveInt, default 11
        QMT price type used for Nautilus limit orders.
    enforce_sellable_position : bool, default True
        If True, validates SELL orders against QMT ``can_use_volume`` before submitting.
    """

    account_id: str
    account_type: str = "STOCK"
    base_url_http: str = QMT_DEFAULT_HTTP_URL
    base_url_ws: str = QMT_DEFAULT_WS_URL
    api_key: str | None = None
    venue: Venue = QMT_VENUE
    instrument_provider: QMTInstrumentProviderConfig | None = None
    request_timeout_secs: PositiveFloat = 10.0
    poll_interval_secs: PositiveFloat = 1.0
    default_market_price_type: PositiveInt = QMT_PRICE_TYPE_LATEST_PRICE
    default_limit_price_type: PositiveInt = QMT_PRICE_TYPE_FIX_PRICE
    strategy_name: str = "nautilus"
    enforce_sellable_position: bool = True
