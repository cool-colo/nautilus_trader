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

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any


class BigQMTClientError(RuntimeError):
    """
    Raised when the Big QMT RPC bridge returns an unsuccessful response.
    """


class BigQMTClient:
    """
    Async wrapper around the synchronous ``xtquant_big_convert`` RPC client.

    Big QMT access is a synchronous, blocking Redis/ZMQ RPC. This wrapper runs
    every call in a bounded thread pool so the Nautilus asyncio event loop is
    never blocked, presenting the same ``await``-able surface the QMT adapter's
    ``QMTHttpClient`` exposes.

    The ``bigqmt_signal_trader`` package is imported lazily at ``connect()`` so
    importing the adapter never hard-requires it.
    """

    def __init__(
        self,
        account_id: str,
        redis_config: dict[str, Any],
        timeout_secs: float = 6.0,
        transport: str = "redis",
    ) -> None:
        self._account_id = str(account_id or "")
        self._redis_config = dict(redis_config or {})
        self._timeout_secs = timeout_secs
        self._transport = transport
        self._trader: Any = None
        self._xtdata: Any = None
        self._account: Any = None
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="bigqmt_rpc",
        )
        self._lifecycle_lock = asyncio.Lock()
        self._connection_users = 0

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def trader(self) -> Any:
        return self._trader

    @property
    def xtdata(self) -> Any:
        return self._xtdata

    async def _run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self._executor is None:
            raise BigQMTClientError("BigQMT client is closed")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, partial(fn, *args, **kwargs))

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            if self._trader is None:
                try:
                    from bigqmt_signal_trader.xtquant_compat import BigQmtXtData
                    from bigqmt_signal_trader.xtquant_compat import BigQmtXtTrader
                    from bigqmt_signal_trader.xtquant_compat import StockAccount
                except ImportError as exc:  # pragma: no cover - import guard
                    raise BigQMTClientError(
                        "The 'bigqmt_signal_trader' package (xtquant_big_convert) is required "
                        "for the BigQMT adapter; install it and ensure it is importable.",
                    ) from exc

                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=4,
                        thread_name_prefix="bigqmt_rpc",
                    )
                redis_config = dict(self._redis_config)
                redis_config.setdefault("transport", self._transport)
                trader = BigQmtXtTrader(
                    account_id=self._account_id,
                    redis_config=redis_config,
                    timeout_seconds=self._timeout_secs,
                )
                try:
                    # Ping the RPC bridge before publishing the connected state.
                    await self._run(trader.connect)
                except Exception:
                    with contextlib.suppress(Exception):
                        await self._run(trader.stop)
                    raise
                self._trader = trader
                self._xtdata = BigQmtXtData(trader.client)
                self._account = StockAccount(self._account_id)
            self._connection_users += 1

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._connection_users == 0:
                return
            self._connection_users -= 1
            if self._connection_users > 0:
                return
            if self._trader is not None:
                with contextlib.suppress(Exception):
                    await self._run(self._trader.stop)
            if self._executor is not None:
                self._executor.shutdown(wait=False)
                self._executor = None
            self._trader = None
            self._xtdata = None
            self._account = None

    # ---------------------------------------------------------------------
    # Market data
    # ---------------------------------------------------------------------

    async def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        return await self._run(self._xtdata.get_instrument_detail, stock_code) or {}

    async def get_stock_list_in_sector(self, sector_name: str) -> list[str]:
        result = await self._run(self._xtdata.get_stock_list_in_sector, sector_name)
        return list(result or [])

    async def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        return await self._run(self._xtdata.get_full_tick, code_list) or {}

    def is_quote_push_supported(self) -> bool:
        """
        Return True if the connected service exposes the whole-quote push API.
        """
        xtdata = self._xtdata
        return xtdata is not None and hasattr(xtdata, "subscribe_whole_quote")

    async def subscribe_whole_quote(
        self,
        code_list: list[str],
        callback: Callable[[dict[str, Any]], None],
    ) -> int:
        """
        Subscribe to the whole-quote push feed for ``code_list``; returns a sub id.

        The service primes ``callback`` once with a full-tick snapshot (invoked on
        the calling thread) and then pushes incremental ``{code: {field: value}}``
        batches on its own subscriber thread.
        """
        return await self._run(self._xtdata.subscribe_whole_quote, list(code_list), callback)

    async def unsubscribe_whole_quote(self, sub_id: int) -> int:
        return await self._run(self._xtdata.unsubscribe_quote, sub_id)

    async def get_market_data_ex(
        self,
        stock_list: list[str],
        period: str,
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: str = "none",
    ) -> dict[str, Any]:
        return await self._run(
            self._xtdata.get_market_data_ex,
            field_list=[],
            stock_list=list(stock_list),
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=count,
            dividend_type=dividend_type,
        ) or {}

    # ---------------------------------------------------------------------
    # Trading / account
    # ---------------------------------------------------------------------

    async def get_asset(self) -> dict[str, Any]:
        asset = await self._run(self._trader.query_stock_asset, self._account)
        return _compat_to_dict(asset)

    async def get_positions(self) -> list[dict[str, Any]]:
        positions = await self._run(self._trader.query_stock_positions, self._account)
        return [_compat_to_dict(item) for item in positions or []]

    async def get_orders(self, cancelable_only: bool = False) -> list[dict[str, Any]]:
        # strategy_name="" queries all orders for the account (not just one
        # strategy's) — see the strategy-name trap in the Big QMT docs.
        orders = await self._run(
            self._trader.query_stock_orders,
            self._account,
            cancelable_only,
            "",
        )
        return [_compat_to_dict(item) for item in orders or []]

    async def get_trades(self) -> list[dict[str, Any]]:
        trades = await self._run(self._trader.query_stock_trades, self._account, "")
        return [_compat_to_dict(item) for item in trades or []]

    async def submit_order(
        self,
        stock_code: str,
        order_type: int,
        volume: int,
        price_type: int,
        price: float,
        strategy_name: str,
        order_remark: str,
    ) -> dict[str, Any]:
        return await self._run(
            self._trader.order_stock_result,
            self._account,
            stock_code,
            order_type,
            volume,
            price_type,
            price,
            strategy_name,
            order_remark,
        ) or {}

    async def cancel_order(self, order_id: str) -> bool:
        return bool(await self._run(self._trader.cancel_order_stock, self._account, order_id))

    # ---------------------------------------------------------------------
    # Real-time execution events (Redis Pub/Sub)
    # ---------------------------------------------------------------------

    def register_callback(self, callback: Any) -> None:
        """
        Register an ``XtQuantTraderCallback`` and start the background event thread.
        """
        self._trader.register_callback(callback)
        self._trader.start()

    def redis_client(self) -> Any:
        """
        Return the underlying redis client (used for quote-event subscriptions).
        """
        return self._trader.client._redis()


def _compat_to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    # CompatObject stores fields on __dict__.
    return dict(getattr(obj, "__dict__", {}))
