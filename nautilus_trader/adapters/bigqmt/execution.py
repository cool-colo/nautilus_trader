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
from decimal import Decimal
from typing import Any

from nautilus_trader.adapters.bigqmt.client import BigQMTClient
from nautilus_trader.adapters.bigqmt.common import bigqmt_action_to_side
from nautilus_trader.adapters.bigqmt.common import bigqmt_status_to_order_status
from nautilus_trader.adapters.bigqmt.common import bigqmt_symbol_to_instrument_id
from nautilus_trader.adapters.bigqmt.common import bigqmt_traded_at_to_nanos
from nautilus_trader.adapters.bigqmt.common import instrument_id_to_bigqmt_symbol
from nautilus_trader.adapters.bigqmt.common import millis_to_nanos
from nautilus_trader.adapters.bigqmt.common import qmt_order_type_from_price_type
from nautilus_trader.adapters.bigqmt.common import quantity_to_int
from nautilus_trader.adapters.bigqmt.config import BigQMTExecClientConfig
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_SIDE_BUY
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_ORDER_SIDE_SELL
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProvider
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


_BIG_QMT_OPEN_ORDER_STATUSES = {
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.PENDING_CANCEL,
    OrderStatus.PENDING_UPDATE,
    OrderStatus.SUBMITTED,
}


class _BigQMTTraderCallback:
    """
    Adapter between the Big QMT ``XtQuantTraderCallback`` protocol and the
    execution client's asyncio-loop-safe handlers.

    The Big QMT event thread calls these from a background daemon thread, so each
    handler is marshalled back onto the client's event loop.
    """

    def __init__(self, client: BigQMTExecutionClient, loop: asyncio.AbstractEventLoop) -> None:
        self._client = client
        self._loop = loop

    def on_disconnected(self) -> None:
        pass

    def on_stock_order(self, order: Any) -> None:
        raw = dict(getattr(order, "__dict__", {})) if not isinstance(order, dict) else order
        self._loop.call_soon_threadsafe(self._client._handle_order_update, raw)

    def on_stock_trade(self, trade: Any) -> None:
        raw = dict(getattr(trade, "__dict__", {})) if not isinstance(trade, dict) else trade
        self._loop.call_soon_threadsafe(self._client._handle_trade_update, raw)

    def on_order_error(self, order_error: Any) -> None:
        pass

    def on_cancel_error(self, cancel_error: Any) -> None:
        pass

    def on_order_stock_async_response(self, response: Any) -> None:
        pass

    def on_account_status(self, status: Any) -> None:
        pass


class BigQMTExecutionClient(LiveExecutionClient):
    """
    Live execution client for Chinese A-shares via the Big QMT RPC bridge.

    Real-time order and trade events arrive via a Redis Pub/Sub callback; a
    polling loop reconciles account, orders and trades as a fallback.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: BigQMTClient,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: BigQMTInstrumentProvider,
        config: BigQMTExecClientConfig,
        name: str | None,
    ) -> None:
        client_id = ClientId(name or BIG_QMT_VENUE.value)
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=BIG_QMT_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=CNY,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._client = client
        self._config = config
        self._poll_task: asyncio.Task | None = None
        self._known_order_status: dict[VenueOrderId, OrderStatus] = {}
        self._known_client_order_ids: dict[VenueOrderId, ClientOrderId] = {}
        self._seen_trade_ids: set[TradeId] = set()
        self._terminal_events: set[VenueOrderId] = set()
        self._last_account_key: tuple[Decimal, Decimal] | None = None

        self._set_account_id(AccountId(f"{client_id.value}-{config.account_id}"))
        self._log.info(f"{config.redis_host=}", LogColor.BLUE)
        self._log.info(f"{config.redis_port=}", LogColor.BLUE)
        self._log.info(f"{config.transport=}", LogColor.BLUE)
        self._log.info(f"{config.account_id=}", LogColor.BLUE)
        self._log.info(f"{config.account_type=}", LogColor.BLUE)
        self._log.info(f"{config.poll_interval_secs=}", LogColor.BLUE)

    async def _connect(self) -> None:
        await self._client.connect()
        await self._instrument_provider.initialize()
        # Register the real-time exec-event callback (Redis Pub/Sub background thread).
        callback = _BigQMTTraderCallback(self, self._loop)
        self._client.register_callback(callback)
        await self._refresh_account_state(force=True)
        self._poll_task = self.create_task(self._poll_loop(), log_msg="bigqmt_execution_poll")

    async def _disconnect(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        await self._client.close()

    async def _submit_order(self, command: SubmitOrder) -> None:
        await self._submit_nautilus_order(command.order)

    async def _submit_nautilus_order(self, order) -> None:
        if order.order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            self.generate_order_denied(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"BigQMT adapter supports MARKET and LIMIT orders only, got {order.order_type}",
                ts_event=self._clock.timestamp_ns(),
            )
            return

        price_type = (
            self._config.default_limit_price_type
            if order.order_type == OrderType.LIMIT
            else self._config.default_market_price_type
        )
        price = self._order_price(order)
        order_type_code = (
            BIG_QMT_ORDER_SIDE_BUY if order.side == OrderSide.BUY else BIG_QMT_ORDER_SIDE_SELL
        )

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
                        f"BigQMT sellable volume is {sellable_volume}, "
                        f"requested SELL volume is {requested_volume}; "
                        f"position={raw_position or {}}"
                    ),
                    ts_event=self._clock.timestamp_ns(),
                )
                return

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )
        try:
            response = await self._client.submit_order(
                stock_code=instrument_id_to_bigqmt_symbol(order.instrument_id),
                order_type=order_type_code,
                volume=quantity_to_int(order.quantity),
                price_type=price_type,
                price=price,
                strategy_name=self._config.strategy_name,
                order_remark=order.client_order_id.value,
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

        order_sys_id = str(response.get("order_sys_id") or response.get("order_sysid") or "")
        if not order_sys_id or order_sys_id == "-1":
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(response.get("message") or "BigQMT order submission failed"),
                ts_event=self._clock.timestamp_ns(),
            )
            return

        venue_order_id = VenueOrderId(order_sys_id)
        self._known_client_order_ids[venue_order_id] = order.client_order_id
        self.generate_order_accepted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=venue_order_id,
            ts_event=self._clock.timestamp_ns(),
        )
        self._known_order_status[venue_order_id] = OrderStatus.ACCEPTED

    async def _get_sellable_volume(self, instrument_id) -> tuple[int, dict[str, Any] | None]:
        symbol = instrument_id_to_bigqmt_symbol(instrument_id)
        for raw_position in await self._client.get_positions():
            if str(raw_position.get("stock_code", "")).upper() != symbol:
                continue
            if raw_position.get("can_use_volume") is not None:
                return int(raw_position.get("can_use_volume", 0) or 0), raw_position
            if raw_position.get("available_amount") is not None:
                return int(raw_position.get("available_amount", 0) or 0), raw_position
            return 0, raw_position
        return 0, None

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        for order in command.order_list.orders:
            await self._submit_nautilus_order(order)

    async def _modify_order(self, command: ModifyOrder) -> None:
        self._log.warning("BigQMT order modification is not supported; cancel and replace the order")

    async def _cancel_order(self, command: CancelOrder) -> None:
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
                reason="No BigQMT order_id available for cancel",
                ts_event=self._clock.timestamp_ns(),
            )
            return
        success = await self._client.cancel_order(venue_order_id.value)
        if not success:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason="BigQMT returned cancel success=false",
                ts_event=self._clock.timestamp_ns(),
            )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        for raw_order in await self._client.get_orders(cancelable_only=True):
            order_id = raw_order.get("order_sysid") or raw_order.get("order_id")
            if order_id:
                await self._client.cancel_order(str(order_id))

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
        reports = []
        for raw_order in await self._client.get_orders(cancelable_only=False):
            status = bigqmt_status_to_order_status(raw_order.get("order_status"))
            if command.open_only and status not in _BIG_QMT_OPEN_ORDER_STATUSES:
                continue
            report = self._parse_order_status_report(raw_order)
            if report is not None and (
                command.instrument_id is None or report.instrument_id == command.instrument_id
            ):
                reports.append(report)
        return reports

    async def generate_fill_reports(self, command: GenerateFillReports) -> list[FillReport]:
        reports = []
        for raw_trade in await self._client.get_trades():
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
        reports = []
        ts_init = self._clock.timestamp_ns()
        for raw_position in await self._client.get_positions():
            try:
                instrument_id = bigqmt_symbol_to_instrument_id(
                    str(raw_position.get("stock_code", "")),
                )
                if command.instrument_id is not None and instrument_id != command.instrument_id:
                    continue
                volume = int(raw_position.get("volume", 0) or 0)
                quantity = Quantity.from_int(volume)
                side = PositionSide.LONG if volume > 0 else PositionSide.FLAT
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
                    "Cannot generate BigQMT position status report: "
                    f"{exc}; raw_position={raw_position!r}",
                )
                continue
        return reports

    async def _poll_loop(self) -> None:
        base_interval = self._config.poll_interval_secs
        max_backoff = 30.0
        failure_streak = 0
        while True:
            try:
                await self._refresh_account_state()
                for raw_order in await self._client.get_orders():
                    self._handle_order_update(raw_order)
                for raw_trade in await self._client.get_trades():
                    self._handle_trade_update(raw_trade)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"BigQMT execution poll failed (streak={failure_streak}): {exc}",
                    )
                backoff = min(base_interval * (2 ** min(failure_streak, 5)), max_backoff)
                await asyncio.sleep(backoff)
                continue

            if failure_streak:
                self._log.info(
                    f"BigQMT execution poll recovered after {failure_streak} failure(s)",
                )
                failure_streak = 0
            await asyncio.sleep(base_interval)

    async def _refresh_account_state(self, force: bool = False) -> None:
        asset = await self._client.get_asset()
        self._handle_asset_update(asset, force=force)

    def _handle_asset_update(self, asset: dict[str, Any], force: bool = False) -> None:
        cash = Decimal(str(asset.get("cash", "0") or "0"))
        frozen_cash = Decimal(str(asset.get("frozen_cash", "0") or "0"))
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
        venue_order_id_value = str(
            raw_order.get("order_sysid")
            or raw_order.get("order_sys_id")
            or raw_order.get("order_id")
            or "",
        )
        if not venue_order_id_value:
            return
        venue_order_id = VenueOrderId(venue_order_id_value)
        client_order_id = self._resolve_client_order_id(venue_order_id, raw_order)

        status = bigqmt_status_to_order_status(
            raw_order.get("order_status", raw_order.get("status")),
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

        ts_event = self._clock.timestamp_ns()
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

        self._handle_terminal_order_update(
            order,
            raw_order,
            venue_order_id,
            client_order_id,
            status,
            ts_event,
        )

    def _handle_terminal_order_update(
        self,
        order,
        raw_order: dict[str, Any],
        venue_order_id: VenueOrderId,
        client_order_id: ClientOrderId,
        status: OrderStatus,
        ts_event: int,
    ) -> None:
        if venue_order_id in self._terminal_events:
            return
        if status == OrderStatus.CANCELED:
            self.generate_order_canceled(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                ts_event=ts_event,
            )
            self._terminal_events.add(venue_order_id)
        elif status == OrderStatus.REJECTED:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                reason=str(raw_order.get("status_msg") or "BigQMT order rejected"),
                ts_event=ts_event,
            )
            self._terminal_events.add(venue_order_id)

    def _resolve_client_order_id(
        self,
        venue_order_id: VenueOrderId,
        raw_order: dict[str, Any],
    ) -> ClientOrderId | None:
        client_order_id_value = str(
            raw_order.get("order_remark") or raw_order.get("client_order_id") or "",
        )
        if client_order_id_value:
            client_order_id = ClientOrderId(client_order_id_value)
            self._known_client_order_ids[venue_order_id] = client_order_id
            return client_order_id
        return self._known_client_order_ids.get(venue_order_id)

    def _handle_trade_update(self, raw_trade: dict[str, Any]) -> None:
        report = self._parse_fill_report(raw_trade)
        if report is None or report.trade_id in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(report.trade_id)
        client_order_id = report.client_order_id
        if client_order_id is None and report.venue_order_id is not None:
            client_order_id = self._known_client_order_ids.get(report.venue_order_id)
        if client_order_id is None:
            return
        order = self._cache.order(client_order_id)
        if order is None:
            return
        self.generate_order_filled(
            strategy_id=order.strategy_id,
            instrument_id=report.instrument_id,
            client_order_id=client_order_id,
            venue_order_id=report.venue_order_id,
            venue_position_id=None,
            trade_id=report.trade_id,
            order_side=report.order_side,
            order_type=order.order_type,
            last_qty=report.last_qty,
            last_px=report.last_px,
            quote_currency=CNY,
            commission=report.commission,
            liquidity_side=report.liquidity_side,
            ts_event=report.ts_event,
        )

    def _parse_order_status_report(self, raw_order: dict[str, Any]) -> OrderStatusReport | None:
        venue_order_id_value = str(
            raw_order.get("order_sysid") or raw_order.get("order_id") or "",
        )
        if not venue_order_id_value:
            return None
        client_order_id_value = str(
            raw_order.get("order_remark") or raw_order.get("client_order_id") or "",
        )
        client_order_id = ClientOrderId(client_order_id_value) if client_order_id_value else None
        price = Decimal(str(raw_order.get("price", "0") or "0"))
        order_volume = int(raw_order.get("order_volume", 0) or 0)
        if order_volume <= 0:
            return None
        status = bigqmt_status_to_order_status(raw_order.get("order_status"))
        ts = self._clock.timestamp_ns()
        return OrderStatusReport(
            account_id=self.account_id,
            instrument_id=bigqmt_symbol_to_instrument_id(str(raw_order.get("stock_code", ""))),
            venue_order_id=VenueOrderId(venue_order_id_value),
            client_order_id=client_order_id,
            order_side=self._order_side(raw_order),
            order_type=qmt_order_type_from_price_type(int(raw_order.get("price_type", 0) or 0)),
            time_in_force=TimeInForce.DAY,
            order_status=status,
            quantity=Quantity.from_int(order_volume),
            filled_qty=Quantity.from_int(int(raw_order.get("traded_volume", 0) or 0)),
            price=Price.from_str(f"{price:.2f}") if price > 0 else None,
            avg_px=Decimal(str(raw_order.get("traded_price", "0") or "0")),
            report_id=UUID4(),
            ts_accepted=ts,
            ts_last=ts,
            ts_init=ts,
            cancel_reason=raw_order.get("status_msg")
            if status == OrderStatus.CANCELED
            else None,
        )

    def _parse_fill_report(self, raw_trade: dict[str, Any]) -> FillReport | None:
        trade_id_value = str(raw_trade.get("trade_id") or raw_trade.get("traded_id") or "")
        venue_order_id_value = str(
            raw_trade.get("order_sysid") or raw_trade.get("order_sys_id") or raw_trade.get("order_id") or "",
        )
        if not trade_id_value or not venue_order_id_value:
            return None
        client_order_id_value = str(
            raw_trade.get("order_remark") or raw_trade.get("client_order_id") or "",
        )
        price = Decimal(str(raw_trade.get("traded_price", raw_trade.get("price", "0")) or "0"))
        volume = int(raw_trade.get("traded_volume", raw_trade.get("volume", 0)) or 0)
        if price <= 0 or volume <= 0:
            return None
        commission = Decimal(str(raw_trade.get("commission", "0") or "0"))
        return FillReport(
            account_id=self.account_id,
            instrument_id=bigqmt_symbol_to_instrument_id(str(raw_trade.get("stock_code", ""))),
            venue_order_id=VenueOrderId(venue_order_id_value),
            trade_id=TradeId(trade_id_value),
            client_order_id=ClientOrderId(client_order_id_value) if client_order_id_value else None,
            order_side=self._order_side(raw_trade),
            last_qty=Quantity.from_int(volume),
            last_px=Price.from_str(f"{price:.2f}"),
            commission=Money(commission, CNY),
            liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
            report_id=UUID4(),
            ts_event=(
                bigqmt_traded_at_to_nanos(raw_trade.get("traded_at"))
                or millis_to_nanos(raw_trade.get("traded_time_ms"))
                or self._clock.timestamp_ns()
            ),
            ts_init=self._clock.timestamp_ns(),
        )

    def _order_side(self, raw: dict[str, Any]) -> OrderSide:
        # BigQMT CompatObject exposes either an "action" (BUY/SELL) or an
        # "order_type" (23/24). Prefer action; fall back to order_type.
        if raw.get("action") is not None:
            return bigqmt_action_to_side(raw.get("action"))
        return bigqmt_action_to_side(raw.get("order_type"))

    def _order_price(self, order) -> float:
        price = getattr(order, "price", None)
        if price is None:
            return 0.0
        return float(Decimal(str(price)))
