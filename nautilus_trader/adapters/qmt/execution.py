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
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp

from nautilus_trader.adapters.qmt.common import instrument_id_to_qmt_symbol
from nautilus_trader.adapters.qmt.common import millis_to_nanos
from nautilus_trader.adapters.qmt.common import nautilus_side_to_qmt
from nautilus_trader.adapters.qmt.common import qmt_lifecycle_to_order_status
from nautilus_trader.adapters.qmt.common import qmt_order_type_from_price_type
from nautilus_trader.adapters.qmt.common import qmt_side_to_nautilus
from nautilus_trader.adapters.qmt.common import qmt_symbol_to_instrument_id
from nautilus_trader.adapters.qmt.common import quantity_to_int
from nautilus_trader.adapters.qmt.config import QMTExecClientConfig
from nautilus_trader.adapters.qmt.constants import QMT_LIFECYCLE_CANCELED
from nautilus_trader.adapters.qmt.constants import QMT_LIFECYCLE_EXPIRED
from nautilus_trader.adapters.qmt.constants import QMT_LIFECYCLE_REJECTED
from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.adapters.qmt.http import QMTHttpClient
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProvider
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import BatchCancelOrders
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.messages import GenerateFillReports
from nautilus_trader.execution.messages import GenerateOrderStatusReport
from nautilus_trader.execution.messages import GenerateOrderStatusReports
from nautilus_trader.execution.messages import GeneratePositionStatusReports
from nautilus_trader.execution.messages import ModifyOrder
from nautilus_trader.execution.messages import QueryAccount
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.execution.messages import SubmitOrderList
from nautilus_trader.execution.reports import ExecutionMassStatus
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import CNY
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import LiquiditySide
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import AccountBalance
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import Order


_QMT_CHINA_TZ = ZoneInfo("Asia/Shanghai")
_QMT_OPEN_ORDER_STATUSES = {
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.PENDING_CANCEL,
    OrderStatus.PENDING_UPDATE,
    OrderStatus.SUBMITTED,
}
_QMT_STALE_DAY_ORDER_STATUSES = {
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.PENDING_CANCEL,
    OrderStatus.SUBMITTED,
}
_QMT_USE_TRADING_WEBSOCKET = True
_QMT_CALLBACK_FALLBACK_POLL_INTERVAL_SECS = 10.0


def _coerce_utc_datetime(value: datetime | Any) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _qmt_order_status(
    raw_order: dict[str, Any],
    now: datetime | None = None,
    allow_stale_day_expiry: bool = True,
) -> OrderStatus:
    status = qmt_lifecycle_to_order_status(raw_order.get("lifecycle_status"))
    if status not in _QMT_STALE_DAY_ORDER_STATUSES:
        return status

    # The "stale DAY order -> EXPIRED" downgrade below infers expiry purely from the
    # order's date (order_time_ms). It is only safe during reconciliation, where the
    # goal is to purge overnight leftovers the venue no longer knows. On the live poll
    # path a bad/stale order_time_ms would wrongly expire a still-open same-day order,
    # so callers there pass allow_stale_day_expiry=False and get the raw lifecycle
    # mapping instead.
    if not allow_stale_day_expiry:
        return status

    order_time_ms = raw_order.get("order_time_ms")
    if order_time_ms is None:
        return status

    try:
        order_time_ms_int = int(order_time_ms)
    except (TypeError, ValueError):
        return status

    if order_time_ms_int <= 0:
        return status

    if now is None:
        now = datetime.now(tz=timezone.utc)
    now = _coerce_utc_datetime(now)

    order_date = datetime.fromtimestamp(
        order_time_ms_int / 1000,
        tz=timezone.utc,
    ).astimezone(_QMT_CHINA_TZ).date()
    current_date = now.astimezone(_QMT_CHINA_TZ).date()

    # QMT A-share orders are DAY orders. If the proxy still returns an open
    # lifecycle for an older trading date, treat it as terminal so the live cache
    # does not keep trying to cancel a venue order QMT no longer knows.
    if order_date < current_date:
        return OrderStatus.EXPIRED

    return status


def _is_prior_qmt_day(ts_ns: int, now: datetime | Any) -> bool:
    if ts_ns <= 0:
        return False

    now = _coerce_utc_datetime(now)
    ts_date = datetime.fromtimestamp(
        ts_ns / 1_000_000_000,
        tz=timezone.utc,
    ).astimezone(_QMT_CHINA_TZ).date()
    current_date = now.astimezone(_QMT_CHINA_TZ).date()

    return ts_date < current_date


class QMTExecutionClient(LiveExecutionClient):
    """
    Live execution client for Chinese A-shares via quant-qmt-proxy.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        http_client: QMTHttpClient,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: QMTInstrumentProvider,
        config: QMTExecClientConfig,
        name: str | None,
    ) -> None:
        client_id = ClientId(name or QMT_VENUE.value)
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=QMT_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=CNY,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._http_client = http_client
        self._config = config
        self._ws_base_url = config.base_url_ws.rstrip("/")
        self._session_id: str | None = None
        self._poll_task: asyncio.Task | None = None
        self._trading_stream_task: asyncio.Task | None = None
        self._known_order_status: dict[VenueOrderId, OrderStatus] = {}
        self._known_client_order_ids: dict[VenueOrderId, ClientOrderId] = {}
        self._seen_trade_ids: set[TradeId] = set()
        self._terminal_events: set[VenueOrderId] = set()
        # Last published (cash, frozen_cash) so the 1s poll only generates a new
        # account state — and the Portfolio INFO log — when the balance actually
        # changes, instead of once per poll for a static balance.
        self._last_account_key: tuple[Decimal, Decimal] | None = None

        self._set_account_id(AccountId(f"{client_id.value}-{config.account_id}"))
        self._log.info(f"{config.base_url_http=}", LogColor.BLUE)
        self._log.info(f"{config.base_url_ws=}", LogColor.BLUE)
        self._log.info(f"{config.account_id=}", LogColor.BLUE)
        self._log.info(f"{config.account_type=}", LogColor.BLUE)
        self._log.info(f"{config.poll_interval_secs=}", LogColor.BLUE)
        self._log.info(f"{_QMT_USE_TRADING_WEBSOCKET=}", LogColor.BLUE)

    async def _connect(self) -> None:
        await self._http_client.connect()
        await self._instrument_provider.initialize()
        session = await self._http_client.open_session(
            account_id=self._config.account_id,
            account_type=self._config.account_type,
        )
        self._session_id = session["session_id"]
        await self._refresh_account_state(force=True)
        if _QMT_USE_TRADING_WEBSOCKET:
            self._trading_stream_task = self.create_task(
                self._stream_trading_events(self._session_id),
                log_msg="qmt_trading_stream",
            )
        self._poll_task = self.create_task(self._poll_loop(), log_msg="qmt_execution_poll")

    async def _disconnect(self) -> None:
        if self._trading_stream_task is not None:
            self._trading_stream_task.cancel()
            self._trading_stream_task = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        if self._session_id is not None:
            try:
                await self._http_client.close_session(self._session_id)
            finally:
                self._session_id = None
        await self._http_client.close()

    async def _submit_order(self, command: SubmitOrder) -> None:
        await self._submit_nautilus_order(command.order)

    async def _submit_nautilus_order(self, order) -> None:
        if order.order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            self.generate_order_denied(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"QMT adapter supports MARKET and LIMIT orders only, got {order.order_type}",
                ts_event=self._clock.timestamp_ns(),
            )
            return
        if self._session_id is None:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason="QMT trading session is not connected",
                ts_event=self._clock.timestamp_ns(),
            )
            return

        price_type = (
            self._config.default_limit_price_type
            if order.order_type == OrderType.LIMIT
            else self._config.default_market_price_type
        )
        price = self._order_price(order)
        if self._config.enforce_sellable_position and order.side == OrderSide.SELL:
            requested_volume = quantity_to_int(order.quantity)
            try:
                sellable_volume, raw_position = await self._get_sellable_volume(order.instrument_id)
            except Exception as exc:
                self.generate_order_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    reason=str(exc),
                    ts_event=self._clock.timestamp_ns(),
                )
                return
            if requested_volume > sellable_volume:
                self.generate_order_denied(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    reason=(
                        f"QMT sellable volume is {sellable_volume}, "
                        f"requested SELL volume is {requested_volume}; "
                        f"position={raw_position or {}}"
                    ),
                    ts_event=self._clock.timestamp_ns(),
                )
                return
        ts_now = self._clock.timestamp_ns()
        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=ts_now,
        )
        try:
            response = await self._http_client.submit_order(
                session_id=self._session_id,
                stock_code=instrument_id_to_qmt_symbol(order.instrument_id),
                side=nautilus_side_to_qmt(order.side),
                price_type=price_type,
                volume=quantity_to_int(order.quantity),
                price=price,
                strategy_name=self._config.strategy_name,
                order_remark=order.client_order_id.value,
                client_order_id=order.client_order_id.value,
            )
        except Exception as exc:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(exc),
                ts_event=self._clock.timestamp_ns(),
            )
            return
        self._handle_order_update(response)

    async def _get_sellable_volume(self, instrument_id) -> tuple[int, dict[str, Any] | None]:
        if self._session_id is None:
            return 0, None

        symbol = instrument_id_to_qmt_symbol(instrument_id)
        for raw_position in await self._http_client.get_positions(self._session_id):
            if str(raw_position.get("stock_code", "")).upper() != symbol:
                continue
            if "can_use_volume" in raw_position:
                return int(raw_position.get("can_use_volume", 0) or 0), raw_position
            if "available_volume" in raw_position:
                return int(raw_position.get("available_volume", 0) or 0), raw_position
            return 0, raw_position
        return 0, None

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        for order in command.order_list.orders:
            await self._submit_nautilus_order(order)

    async def _modify_order(self, command: ModifyOrder) -> None:
        self._log.warning("QMT order modification is not supported; cancel and replace the order")

    async def _cancel_order(self, command: CancelOrder) -> None:
        if self._session_id is None:
            return
        venue_order_id = command.venue_order_id
        if venue_order_id is None:
            order = self._cache.order(command.client_order_id)
            venue_order_id = order.venue_order_id if order is not None else None
        if venue_order_id is None:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=VenueOrderId("UNKNOWN"),
                reason="No QMT order_id available for cancel",
                ts_event=self._clock.timestamp_ns(),
            )
            return
        success = await self._http_client.cancel_order(
            session_id=self._session_id,
            order_id=venue_order_id.value,
        )
        if not success:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason="QMT proxy returned cancel success=false",
                ts_event=self._clock.timestamp_ns(),
            )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        if self._session_id is None:
            return
        for raw_order in await self._http_client.get_orders(self._session_id, cancelable_only=True):
            order_id = raw_order.get("order_id")
            if order_id:
                await self._http_client.cancel_order(self._session_id, order_id=str(order_id))

    async def _batch_cancel_orders(self, command: BatchCancelOrders) -> None:
        for cancel in command.cancels:
            await self._cancel_order(cancel)

    async def _query_account(self, command: QueryAccount) -> None:
        await self._refresh_account_state(force=True)

    async def generate_order_status_report(
        self,
        command: GenerateOrderStatusReport,
    ) -> OrderStatusReport | None:
        reports = await self.generate_order_status_reports(
            GenerateOrderStatusReports(
                instrument_id=command.instrument_id,
                start=None,
                end=None,
                open_only=False,
                command_id=UUID4(),
                ts_init=self._clock.timestamp_ns(),
            ),
        )
        for report in reports:
            if command.client_order_id and report.client_order_id != command.client_order_id:
                continue
            if command.venue_order_id and report.venue_order_id != command.venue_order_id:
                continue
            return report
        return None

    async def generate_order_status_reports(
        self,
        command: GenerateOrderStatusReports,
    ) -> list[OrderStatusReport]:
        if self._session_id is None:
            return []
        reports = []
        for raw_order in await self._http_client.get_orders(self._session_id, cancelable_only=False):
            # Reconciliation path: keep the stale-DAY-order -> EXPIRED downgrade so
            # overnight leftovers the venue no longer knows are purged from the cache.
            status = _qmt_order_status(
                raw_order,
                now=self._clock.utc_now(),
                allow_stale_day_expiry=True,
            )
            if command.open_only and status not in _QMT_OPEN_ORDER_STATUSES:
                continue
            report = self._parse_order_status_report(raw_order)
            if report is not None and (command.instrument_id is None or report.instrument_id == command.instrument_id):
                reports.append(report)
        return reports

    async def generate_mass_status(
        self,
        lookback_mins: int | None = None,
    ) -> ExecutionMassStatus | None:
        mass_status = await super().generate_mass_status(lookback_mins=lookback_mins)
        if mass_status is None:
            return None
        if self._session_id is None:
            return mass_status

        stale_reports = self._stale_cached_open_order_reports(mass_status)
        if stale_reports:
            self._log.warning(
                f"Adding {len(stale_reports)} stale cached QMT DAY order(s) as EXPIRED "
                "to startup reconciliation mass status",
                LogColor.YELLOW,
            )
            mass_status.add_order_reports(stale_reports)

        return mass_status

    async def generate_fill_reports(self, command: GenerateFillReports) -> list[FillReport]:
        if self._session_id is None:
            return []
        reports = []
        for raw_trade in await self._http_client.get_trades(self._session_id):
            report = self._parse_fill_report(raw_trade)
            if report is None:
                continue
            if command.instrument_id is not None and report.instrument_id != command.instrument_id:
                continue
            if command.venue_order_id is not None and report.venue_order_id != command.venue_order_id:
                continue
            reports.append(report)
        return reports

    async def generate_position_status_reports(
        self,
        command: GeneratePositionStatusReports,
    ) -> list[PositionStatusReport]:
        if self._session_id is None:
            return []
        reports = []
        ts_init = self._clock.timestamp_ns()
        for raw_position in await self._http_client.get_positions(self._session_id):
            try:
                instrument_id = qmt_symbol_to_instrument_id(str(raw_position.get("stock_code", "")))
                if command.instrument_id is not None and instrument_id != command.instrument_id:
                    continue
                volume = int(raw_position.get("volume", 0) or 0)
                quantity = Quantity.from_int(volume)
                side = PositionSide.LONG if volume > 0 else PositionSide.FLAT
                # QMT reports `can_use_volume` (可用数量): the T+1-eligible sellable quantity,
                # already net of today's buys, frozen, and in-transit shares. Carry it through so
                # the strategy can size sells against broker ground truth instead of inferring
                # today's buys from reconciliation-rebuilt fill timestamps.
                raw_can_use = raw_position.get("can_use_volume")
                can_use_volume = Decimal(str(raw_can_use)) if raw_can_use is not None else None
                reports.append(
                    PositionStatusReport(
                        account_id=self.account_id,
                        instrument_id=instrument_id,
                        position_side=side,
                        quantity=quantity,
                        avg_px_open=Decimal(str(raw_position.get("avg_price", "0") or "0")),
                        can_use_volume=can_use_volume,
                        report_id=UUID4(),
                        ts_last=ts_init,
                        ts_init=ts_init,
                    ),
                )
            except Exception as exc:
                self._log.error(
                    "Cannot generate QMT position status report: "
                    f"{exc}; raw_position={raw_position!r}",
                )
                continue
        return reports

    async def _poll_loop(self) -> None:
        base_interval = (
            max(self._config.poll_interval_secs, _QMT_CALLBACK_FALLBACK_POLL_INTERVAL_SECS)
            if _QMT_USE_TRADING_WEBSOCKET
            else self._config.poll_interval_secs
        )
        max_backoff = 30.0
        failure_streak = 0
        while True:
            try:
                await self._refresh_account_state()
                if self._session_id is not None:
                    for raw_order in await self._http_client.get_orders(self._session_id):
                        self._handle_order_update(raw_order)
                    for raw_trade in await self._http_client.get_trades(self._session_id):
                        self._handle_trade_update(raw_trade)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                # Log the first failure of a streak and then every 30th, so a tunnel
                # outage does not spam a warning on every poll interval.
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"QMT execution poll failed (streak={failure_streak}): {exc}",
                    )
                # Exponential backoff capped at max_backoff while the proxy is unreachable.
                backoff = min(base_interval * (2 ** min(failure_streak, 5)), max_backoff)
                await asyncio.sleep(backoff)
                continue

            if failure_streak:
                self._log.info(
                    f"QMT execution poll recovered after {failure_streak} failure(s)",
                )
                failure_streak = 0
            await asyncio.sleep(base_interval)

    async def _stream_trading_events(self, session_id: str) -> None:
        url = f"{self._ws_base_url}/ws/trading/{session_id}"
        if self._config.api_key:
            url = f"{url}?{urlencode({'token': self._config.api_key})}"

        backoff = 1.0
        max_backoff = 30.0
        failure_streak = 0
        while True:
            try:
                async with (
                    aiohttp.ClientSession() as session,
                    session.ws_connect(url) as ws,
                ):
                    if failure_streak:
                        self._log.info(
                            "QMT trading stream reconnected "
                            f"after {failure_streak} failure(s)",
                        )
                        failure_streak = 0
                        backoff = 1.0
                    await self._consume_trading_ws(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"QMT trading stream failed (streak={failure_streak}): {exc}",
                    )
            else:
                failure_streak += 1
                if failure_streak == 1:
                    self._log.warning("QMT trading stream closed; reconnecting")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _consume_trading_ws(self, ws) -> None:
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                self._dispatch_trading_ws_message(message.json())
            elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                break

    def _dispatch_trading_ws_message(self, payload: dict[str, Any]) -> None:
        msg_type = payload.get("type")
        if msg_type == "error":
            self._log.warning(f"QMT trading stream error message: {payload.get('message')}")
            return
        if msg_type != "trading":
            return

        event = payload.get("data") or {}
        event_type = event.get("event_type")
        raw_payload = event.get("payload") or {}
        if event_type == "order_update":
            self._handle_order_update(raw_payload)
        elif event_type == "trade_update":
            self._handle_trade_update(raw_payload)
        elif event_type == "asset_update":
            self._handle_asset_update(raw_payload)
        elif event_type in {"order_error", "cancel_error"}:
            self._log.warning(f"QMT trading stream {event_type}: {raw_payload!r}")

    async def _refresh_account_state(self, force: bool = False) -> None:
        if self._session_id is None:
            return
        asset = await self._http_client.get_asset(self._session_id)
        self._handle_asset_update(asset, force=force)

    def _handle_asset_update(self, asset: dict[str, Any], force: bool = False) -> None:
        cash = Decimal(str(asset.get("cash", "0") or "0"))
        frozen_cash = Decimal(str(asset.get("frozen_cash", "0") or "0"))
        # The poll loop runs every poll_interval_secs and the balance is usually
        # unchanged between polls; skip regenerating the account state (which the
        # Portfolio logs at INFO) unless the balance moved or the caller forces a
        # refresh (initial connect / explicit account query).
        account_key = (cash, frozen_cash)
        if not force and account_key == self._last_account_key:
            return
        self._last_account_key = account_key
        total_cash = cash + frozen_cash
        balance = AccountBalance(
            total=Money(total_cash, CNY),
            locked=Money(frozen_cash, CNY),
            free=Money(cash, CNY),
        )
        self.generate_account_state(
            balances=[balance],
            margins=[],
            reported=True,
            ts_event=self._clock.timestamp_ns(),
            info=asset,
        )

    def _handle_order_update(self, raw_order: dict[str, Any]) -> None:
        venue_order_id_value = str(raw_order.get("order_id") or raw_order.get("order_sysid") or "")
        if not venue_order_id_value:
            return
        venue_order_id = VenueOrderId(venue_order_id_value)
        client_order_id_value = str(raw_order.get("client_order_id") or "")
        client_order_id = ClientOrderId(client_order_id_value) if client_order_id_value else None
        if client_order_id is not None:
            self._known_client_order_ids[venue_order_id] = client_order_id

        # Live poll path: do NOT apply the date-based "stale DAY order -> EXPIRED"
        # downgrade. A same-day open order must never be expired here just because the
        # proxy reported a wrong/stale order_time_ms (xtquant order_time can carry the
        # prior trading day's date for a live order); overnight-leftover cleanup is the
        # reconciliation path's job.
        status = _qmt_order_status(
            raw_order,
            now=self._clock.utc_now(),
            allow_stale_day_expiry=False,
        )
        previous_status = self._known_order_status.get(venue_order_id)
        if previous_status == status:
            return
        self._known_order_status[venue_order_id] = status

        if client_order_id is None:
            return
        order = self._cache.order(client_order_id)
        if order is None:
            return

        ts_event = millis_to_nanos(raw_order.get("order_time_ms")) or self._clock.timestamp_ns()
        if status == OrderStatus.PENDING_CANCEL:
            return

        if status in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            if previous_status is None:
                self.generate_order_accepted(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=venue_order_id,
                    ts_event=ts_event,
                )
            return

        if venue_order_id in self._terminal_events:
            return
        lifecycle = str(raw_order.get("lifecycle_status") or "").upper()
        if lifecycle == QMT_LIFECYCLE_CANCELED:
            self.generate_order_canceled(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                ts_event=ts_event,
            )
            self._terminal_events.add(venue_order_id)
        elif lifecycle == QMT_LIFECYCLE_REJECTED:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                reason=str(raw_order.get("status_msg") or "QMT order rejected"),
                ts_event=ts_event,
            )
            self._terminal_events.add(venue_order_id)
        elif lifecycle == QMT_LIFECYCLE_EXPIRED or status == OrderStatus.EXPIRED:
            self.generate_order_expired(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                ts_event=ts_event,
            )
            self._terminal_events.add(venue_order_id)

    def _handle_trade_update(self, raw_trade: dict[str, Any]) -> None:
        report = self._parse_fill_report(raw_trade)
        if report is None or report.trade_id in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(report.trade_id)
        if report.client_order_id is None:
            return
        order = self._cache.order(report.client_order_id)
        if order is None:
            return
        order_type = order.order_type if order is not None else OrderType.MARKET
        self.generate_order_filled(
            strategy_id=order.strategy_id,
            instrument_id=report.instrument_id,
            client_order_id=report.client_order_id,
            venue_order_id=report.venue_order_id,
            venue_position_id=None,
            trade_id=report.trade_id,
            order_side=report.order_side,
            order_type=order_type,
            last_qty=report.last_qty,
            last_px=report.last_px,
            quote_currency=CNY,
            commission=report.commission,
            liquidity_side=report.liquidity_side,
            ts_event=report.ts_event,
        )

    def _parse_order_status_report(self, raw_order: dict[str, Any]) -> OrderStatusReport | None:
        venue_order_id_value = str(raw_order.get("order_id") or raw_order.get("order_sysid") or "")
        if not venue_order_id_value:
            return None
        client_order_id_value = str(raw_order.get("client_order_id") or "")
        client_order_id = ClientOrderId(client_order_id_value) if client_order_id_value else None
        price = Decimal(str(raw_order.get("price", "0") or "0"))
        avg_px = Decimal(str(raw_order.get("traded_price", "0") or "0"))
        order_volume = int(raw_order.get("order_volume", 0) or 0)
        if order_volume <= 0:
            return None
        status = _qmt_order_status(raw_order, now=self._clock.utc_now())
        ts = millis_to_nanos(raw_order.get("order_time_ms")) or self._clock.timestamp_ns()
        return OrderStatusReport(
            account_id=self.account_id,
            instrument_id=qmt_symbol_to_instrument_id(str(raw_order.get("stock_code", ""))),
            venue_order_id=VenueOrderId(venue_order_id_value),
            client_order_id=client_order_id,
            order_side=qmt_side_to_nautilus(raw_order.get("order_type")),
            order_type=qmt_order_type_from_price_type(int(raw_order.get("price_type", 0) or 0)),
            time_in_force=TimeInForce.DAY,
            order_status=status,
            quantity=Quantity.from_int(order_volume),
            filled_qty=Quantity.from_int(int(raw_order.get("traded_volume", 0) or 0)),
            price=Price.from_str(f"{price:.2f}") if price > 0 else None,
            avg_px=avg_px,
            report_id=UUID4(),
            ts_accepted=ts,
            ts_last=ts,
            ts_init=self._clock.timestamp_ns(),
            cancel_reason=raw_order.get("status_msg")
            if status == OrderStatus.CANCELED
            else None,
        )

    def _stale_cached_open_order_reports(
        self,
        mass_status: ExecutionMassStatus,
    ) -> list[OrderStatusReport]:
        reported_client_order_ids: set[ClientOrderId] = set()
        reported_venue_order_ids: set[VenueOrderId] = set()

        for report in mass_status.order_reports.values():
            if report.client_order_id is not None:
                reported_client_order_ids.add(report.client_order_id)
            if report.venue_order_id is not None:
                reported_venue_order_ids.add(report.venue_order_id)

        reports: list[OrderStatusReport] = []
        now = self._clock.utc_now()
        for order in self._cache.orders_open(venue=self.venue, account_id=self.account_id):
            if order.client_order_id in reported_client_order_ids:
                continue
            if order.venue_order_id is None or order.venue_order_id in reported_venue_order_ids:
                continue
            if order.time_in_force != TimeInForce.DAY:
                continue
            if order.status not in _QMT_STALE_DAY_ORDER_STATUSES:
                continue
            if not _is_prior_qmt_day(order.ts_last, now=now):
                continue

            reports.append(self._cached_order_expired_report(order))

        return reports

    def _cached_order_expired_report(self, order: Order) -> OrderStatusReport:
        ts_last = order.ts_last or self._clock.timestamp_ns()
        return OrderStatusReport(
            account_id=self.account_id,
            instrument_id=order.instrument_id,
            venue_order_id=order.venue_order_id,
            client_order_id=order.client_order_id,
            order_side=order.side,
            order_type=order.order_type,
            time_in_force=TimeInForce.DAY,
            order_status=OrderStatus.EXPIRED,
            quantity=order.quantity,
            filled_qty=order.filled_qty,
            price=order.price if order.has_price else None,
            avg_px=Decimal(str(order.avg_px)) if order.avg_px > 0 else Decimal("0"),
            report_id=UUID4(),
            ts_accepted=ts_last,
            ts_last=ts_last,
            ts_init=self._clock.timestamp_ns(),
        )

    def _parse_fill_report(self, raw_trade: dict[str, Any]) -> FillReport | None:
        trade_id_value = str(raw_trade.get("traded_id") or "")
        venue_order_id_value = str(raw_trade.get("order_id") or raw_trade.get("order_sysid") or "")
        if not trade_id_value or not venue_order_id_value:
            return None
        client_order_id_value = str(raw_trade.get("client_order_id") or "")
        price = Decimal(str(raw_trade.get("traded_price", "0") or "0"))
        volume = int(raw_trade.get("traded_volume", 0) or 0)
        if price <= 0 or volume <= 0:
            return None
        commission = Decimal(str(raw_trade.get("commission", "0") or "0"))
        return FillReport(
            account_id=self.account_id,
            instrument_id=qmt_symbol_to_instrument_id(str(raw_trade.get("stock_code", ""))),
            venue_order_id=VenueOrderId(venue_order_id_value),
            trade_id=TradeId(trade_id_value),
            client_order_id=ClientOrderId(client_order_id_value) if client_order_id_value else None,
            order_side=qmt_side_to_nautilus(raw_trade.get("order_type")),
            last_qty=Quantity.from_int(volume),
            last_px=Price.from_str(f"{price:.2f}"),
            commission=Money(commission, CNY),
            liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
            report_id=UUID4(),
            ts_event=millis_to_nanos(raw_trade.get("traded_time_ms")) or self._clock.timestamp_ns(),
            ts_init=self._clock.timestamp_ns(),
        )

    def _order_price(self, order) -> float:
        price = getattr(order, "price", None)
        if price is None:
            return 0.0
        return float(Decimal(str(price)))
