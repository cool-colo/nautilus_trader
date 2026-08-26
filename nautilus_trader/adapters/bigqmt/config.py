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

from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_PRICE_TYPE_FIX_PRICE
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_PRICE_TYPE_LATEST_PRICE
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProviderConfig
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.config import LiveExecClientConfig
from nautilus_trader.config import PositiveFloat
from nautilus_trader.config import PositiveInt
from nautilus_trader.model.identifiers import Venue


class BigQMTDataClientConfig(LiveDataClientConfig, frozen=True):
    """
    Configuration for ``BigQMTDataClient`` instances.

    Parameters
    ----------
    account_id : str, optional
        Big QMT stock account ID (used to scope Redis quote-event channels).
    redis_host : str, default "127.0.0.1"
        Redis host of the Big QMT RPC bridge.
    redis_port : int, default 6379
        Redis port of the Big QMT RPC bridge.
    redis_db : int, default 5
        Redis database of the Big QMT RPC bridge.
    redis_password : str, optional
        Redis password, if authentication is enabled.
    transport : str, default "redis"
        RPC transport ("redis", "zmq" or "mysql").
    rpc_timeout_secs : PositiveFloat, default 6.0
        RPC request timeout.
    poll_interval_secs : PositiveFloat, default 60.0
        Poll interval for the market-data polling fallback. Since the
        ``subscribe_whole_quote`` push feed is the primary source, polling only
        acts as a slow backstop; set lower if running with ``use_quote_push=False``.
    instrument_provider : BigQMTInstrumentProviderConfig, optional
        Instrument loading configuration.
    adjust_type : str, default "none"
        Big QMT dividend adjustment mode used for historical data requests.
    use_quote_push : bool, default True
        If True, quote/depth subscriptions use the service's ``subscribe_whole_quote``
        push feed when available. Set False to force polling (e.g. against an older
        service without push support).
    poll_enabled : bool, default True
        If True, keeps the market-data poll loop running as a fallback alongside
        the push feed. Set False to rely solely on the push feed.
    """

    account_id: str = ""
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 5
    redis_password: str | None = None
    transport: str = "redis"
    rpc_timeout_secs: PositiveFloat = 6.0
    poll_interval_secs: PositiveFloat = 60.0
    venue: Venue = BIG_QMT_VENUE
    instrument_provider: BigQMTInstrumentProviderConfig | None = None
    adjust_type: str = "none"
    use_quote_push: bool = True
    poll_enabled: bool = True


class BigQMTExecClientConfig(LiveExecClientConfig, frozen=True, kw_only=True):
    """
    Configuration for ``BigQMTExecutionClient`` instances.

    Parameters
    ----------
    account_id : str
        Big QMT stock account ID.
    account_type : str, default "STOCK"
        Big QMT security account type.
    redis_host : str, default "127.0.0.1"
        Redis host of the Big QMT RPC bridge.
    redis_port : int, default 6379
        Redis port of the Big QMT RPC bridge.
    redis_db : int, default 5
        Redis database of the Big QMT RPC bridge.
    redis_password : str, optional
        Redis password, if authentication is enabled.
    transport : str, default "redis"
        RPC transport ("redis", "zmq" or "mysql").
    rpc_timeout_secs : PositiveFloat, default 6.0
        RPC request timeout.
    poll_interval_secs : PositiveFloat, default 1.0
        Poll interval for order, trade and asset synchronization.
    default_market_price_type : PositiveInt, default 5
        Big QMT price type used for Nautilus market orders.
    default_limit_price_type : PositiveInt, default 11
        Big QMT price type used for Nautilus limit orders.
    strategy_name : str, default "nautilus"
        Strategy name recorded on submitted orders.
    enforce_sellable_position : bool, default True
        If True, validates SELL orders against Big QMT ``can_use_volume`` before submitting.
    poll_enabled : bool, default True
        If True, runs the order/trade/asset reconcile poll loop as a fallback
        alongside the real-time callback feed. Set False to rely solely on the
        push callbacks (on-demand report queries still work).
    """

    account_id: str
    account_type: str = "STOCK"
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 5
    redis_password: str | None = None
    transport: str = "redis"
    rpc_timeout_secs: PositiveFloat = 6.0
    poll_interval_secs: PositiveFloat = 1.0
    default_market_price_type: PositiveInt = BIG_QMT_PRICE_TYPE_LATEST_PRICE
    default_limit_price_type: PositiveInt = BIG_QMT_PRICE_TYPE_FIX_PRICE
    strategy_name: str = "nautilus"
    enforce_sellable_position: bool = True
    poll_enabled: bool = True
    venue: Venue = BIG_QMT_VENUE
    instrument_provider: BigQMTInstrumentProviderConfig | None = None
