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

from typing import Any

import aiohttp


class QMTProxyError(RuntimeError):
    """
    Raised when quant-qmt-proxy returns an unsuccessful response.
    """


class QMTHttpClient:
    """
    Small async REST client for quant-qmt-proxy.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_secs: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout_secs)
        self._session: aiohttp.ClientSession | None = None

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            # Keep-alive connections are reused across requests, but bounded by
            # keepalive_timeout so an idle connection is dropped before the upstream
            # (or a dev tunnel edge) can leave it stale. The retry-once in _request
            # covers the rare case where a still-pooled connection has gone bad.
            connector = aiohttp.TCPConnector(keepalive_timeout=15.0, limit=40)
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=self.headers,
                connector=connector,
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=self._normalize_query_params(params))

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=payload or {})

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    # HTTP statuses returned by the tunnel edge when it transiently cannot reach the
    # upstream proxy. A retry with a fresh connection usually succeeds.
    _TRANSIENT_STATUSES = (502, 503, 504)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(2):
            await self.connect()
            if self._session is None:
                raise QMTProxyError("HTTP session is not initialized")
            try:
                async with self._session.request(method, url, **kwargs) as response:
                    if response.status in self._TRANSIENT_STATUSES and attempt == 0:
                        body = await response.text()
                        last_exc = QMTProxyError(
                            f"QMT proxy HTTP {response.status} "
                            f"(content_type={response.content_type}): {body!r}",
                        )
                        await self.close()  # discard the connection and retry fresh
                        continue
                    if response.status >= 400:
                        body = await response.text()
                        raise QMTProxyError(
                            f"QMT proxy HTTP {response.status} "
                            f"(content_type={response.content_type}): {body!r}",
                        )
                    try:
                        payload = await response.json()
                    except aiohttp.ContentTypeError as exc:
                        text = await response.text()
                        raise QMTProxyError(
                            f"QMT proxy returned non-JSON response "
                            f"(status={response.status}, content_type={response.content_type}): {text!r}",
                        ) from exc
                    if isinstance(payload, dict) and not payload.get("success", True):
                        raise QMTProxyError(str(payload.get("message") or payload))
                    if isinstance(payload, dict) and "data" in payload:
                        return payload["data"]
                    return payload
            except (aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError) as exc:
                last_exc = exc
                await self.close()
                if attempt == 0:
                    continue
                raise QMTProxyError(f"QMT proxy connection error: {exc}") from exc

        # Both attempts hit a transient status; surface the last error.
        raise last_exc if last_exc is not None else QMTProxyError("QMT proxy request failed")

    @staticmethod
    def _normalize_query_params(params: dict[str, Any] | None) -> dict[str, str | int | float] | None:
        if params is None:
            return None

        normalized: dict[str, str | int | float] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                normalized[key] = str(value).lower()
            elif isinstance(value, (str, int, float)):
                normalized[key] = value
            else:
                normalized[key] = str(value)
        return normalized

    async def get_instrument(self, symbol: str, complete: bool = False) -> dict[str, Any]:
        return await self.get(f"/api/v1/data/instrument/{symbol}", params={"complete": complete})

    async def get_sectors(self) -> list[dict[str, Any]]:
        data = await self.get("/api/v1/data/sectors")
        return list(data.get("items", []))

    async def get_kline_history(
        self,
        symbols: list[str],
        period: str,
        start_time: str = "",
        end_time: str = "",
        adjust_type: str = "none",
        fill_data: bool = True,
    ) -> list[dict[str, Any]]:
        data = await self.post(
            "/api/v1/data/kline-history",
            {
                "symbols": symbols,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "adjust_type": adjust_type,
                "fill_data": fill_data,
            },
        )
        return list(data.get("items", []))

    async def get_tick_history(
        self,
        symbols: list[str],
        start_time: str = "",
        end_time: str = "",
        adjust_type: str = "none",
    ) -> list[dict[str, Any]]:
        data = await self.post(
            "/api/v1/data/tick-history",
            {
                "symbols": symbols,
                "start_time": start_time,
                "end_time": end_time,
                "adjust_type": adjust_type,
            },
        )
        return list(data.get("items", []))

    async def create_quote_subscription(
        self,
        symbols: list[str],
        period: str = "tick",
        start_time: str = "",
        adjust_type: str = "none",
        count: int = 0,
    ) -> dict[str, Any]:
        return await self.post(
            "/api/v1/data/subscriptions/quote",
            {
                "symbols": symbols,
                "period": period,
                "start_time": start_time,
                "adjust_type": adjust_type,
                "count": count,
            },
        )

    async def delete_subscription(self, subscription_id: str) -> None:
        await self.delete(f"/api/v1/data/subscriptions/{subscription_id}")

    async def open_session(self, account_id: str, account_type: str) -> dict[str, Any]:
        return await self.post(
            "/api/v1/trading/sessions",
            {
                "account_id": account_id,
                "account_type": account_type,
            },
        )

    async def close_session(self, session_id: str) -> None:
        await self.delete(f"/api/v1/trading/sessions/{session_id}")

    async def get_asset(self, session_id: str) -> dict[str, Any]:
        return await self.get(f"/api/v1/trading/sessions/{session_id}/asset")

    async def get_positions(self, session_id: str) -> list[dict[str, Any]]:
        data = await self.get(f"/api/v1/trading/sessions/{session_id}/positions")
        return list(data.get("items", []))

    async def get_orders(self, session_id: str, cancelable_only: bool = False) -> list[dict[str, Any]]:
        data = await self.get(
            f"/api/v1/trading/sessions/{session_id}/orders",
            params={"cancelable_only": cancelable_only},
        )
        return list(data.get("items", []))

    async def get_trades(self, session_id: str) -> list[dict[str, Any]]:
        data = await self.get(f"/api/v1/trading/sessions/{session_id}/trades")
        return list(data.get("items", []))

    async def submit_order(
        self,
        session_id: str,
        stock_code: str,
        side: str,
        price_type: int,
        volume: int,
        price: float,
        strategy_name: str,
        order_remark: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self.post(
            f"/api/v1/trading/sessions/{session_id}/orders",
            {
                "stock_code": stock_code,
                "side": side,
                "price_type": price_type,
                "volume": volume,
                "price": price,
                "strategy_name": strategy_name,
                "order_remark": order_remark,
                "client_order_id": client_order_id,
            },
        )

    async def cancel_order(
        self,
        session_id: str,
        order_id: str | None = None,
        market: str | int | None = None,
        order_sysid: str | None = None,
    ) -> bool:
        data = await self.post(
            f"/api/v1/trading/sessions/{session_id}/cancel",
            {
                "order_id": order_id,
                "market": market,
                "order_sysid": order_sysid,
            },
        )
        return bool(data.get("success", False))
