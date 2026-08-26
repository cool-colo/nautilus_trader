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

from nautilus_trader.adapters.bigqmt.client import BigQMTClient
from nautilus_trader.adapters.bigqmt.common import bar_type_to_qmt_period
from nautilus_trader.adapters.bigqmt.common import bigqmt_symbol_to_instrument_id
from nautilus_trader.adapters.bigqmt.common import instrument_id_to_bigqmt_symbol
from nautilus_trader.adapters.bigqmt.common import parse_bar
from nautilus_trader.adapters.bigqmt.common import parse_full_tick_as_order_book_depth10
from nautilus_trader.adapters.bigqmt.common import parse_full_tick_as_quote_tick
from nautilus_trader.adapters.bigqmt.config import BigQMTDataClientConfig
from nautilus_trader.adapters.bigqmt.constants import BIG_QMT_VENUE
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProvider
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.enums import LogColor
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.data.messages import RequestData
from nautilus_trader.data.messages import RequestInstrument
from nautilus_trader.data.messages import RequestInstruments
from nautilus_trader.data.messages import SubscribeBars
from nautilus_trader.data.messages import SubscribeData
from nautilus_trader.data.messages import SubscribeOrderBook
from nautilus_trader.data.messages import SubscribeQuoteTicks
from nautilus_trader.data.messages import UnsubscribeBars
from nautilus_trader.data.messages import UnsubscribeData
from nautilus_trader.data.messages import UnsubscribeOrderBook
from nautilus_trader.data.messages import UnsubscribeQuoteTicks
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId


class BigQMTDataClient(LiveMarketDataClient):
    """
    Live market data client for Chinese A-shares via the Big QMT RPC bridge.

    Big QMT has no true streaming feed, so quote and bar subscriptions run a
    polling loop against ``get_full_tick`` / ``get_market_data_ex`` at
    ``poll_interval_secs``.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: BigQMTClient,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: BigQMTInstrumentProvider,
        config: BigQMTDataClientConfig,
        name: str | None,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(name or BIG_QMT_VENUE.value),
            venue=BIG_QMT_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )
        self._client = client
        self._config = config
        self._subscription_tasks: dict[object, asyncio.Task] = {}

        # Whole-quote push state. ``_push_codes`` is the union of bigqmt symbol
        # strings wanted by quote+depth subscribers; ``_push_sub_id`` is the
        # current session subscription covering that union (re-created on change).
        # ``_push_quote_ids`` / ``_push_depth_ids`` route an incoming push to the
        # right converter(s) per instrument.
        self._quote_push_supported = False
        self._push_codes: set[str] = set()
        self._push_sub_id: int | None = None
        self._push_quote_ids: set[InstrumentId] = set()
        self._push_depth_ids: set[InstrumentId] = set()

        self._log.info(f"{config.redis_host=}", LogColor.BLUE)
        self._log.info(f"{config.redis_port=}", LogColor.BLUE)
        self._log.info(f"{config.transport=}", LogColor.BLUE)
        self._log.info(f"{config.poll_interval_secs=}", LogColor.BLUE)
        self._log.info(f"{config.adjust_type=}", LogColor.BLUE)
        self._log.info(f"{config.use_quote_push=}", LogColor.BLUE)
        self._log.info(f"{config.poll_enabled=}", LogColor.BLUE)

    async def _connect(self) -> None:
        await self._client.connect()
        await self._instrument_provider.initialize()
        self._send_all_instruments_to_data_engine()
        self._quote_push_supported = bool(
            self._config.use_quote_push and self._client.is_quote_push_supported(),
        )
        if self._config.use_quote_push and not self._quote_push_supported:
            self._log.warning(
                "BigQMT service does not support whole-quote push; falling back to polling",
            )
        self._log.info(f"{self._quote_push_supported=}", LogColor.BLUE)

    async def _disconnect(self) -> None:
        for task in list(self._subscription_tasks.values()):
            task.cancel()
        self._subscription_tasks.clear()
        if self._push_sub_id is not None:
            try:
                await self._client.unsubscribe_whole_quote(self._push_sub_id)
            except Exception as exc:
                self._log.warning(f"BigQMT whole-quote unsubscribe failed on disconnect: {exc}")
            self._push_sub_id = None
        self._push_codes.clear()
        self._push_quote_ids.clear()
        self._push_depth_ids.clear()
        await self._client.close()

    async def _subscribe(self, command: SubscribeData) -> None:
        raise NotImplementedError("BigQMT custom data subscriptions are not implemented")

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        raise NotImplementedError("BigQMT custom data subscriptions are not implemented")

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        instrument_id = command.instrument_id
        if self._quote_push_supported:
            self._push_quote_ids.add(instrument_id)
            await self._add_push_code(instrument_id)
        if self._should_poll():
            key = ("quote", instrument_id)
            task = self.create_task(
                self._poll_quote_tick(instrument_id),
                log_msg=f"bigqmt_quote_poll: {instrument_id}",
            )
            if task is not None:
                self._subscription_tasks[key] = task

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        instrument_id = command.instrument_id
        self._cancel_task(("quote", instrument_id))
        if self._quote_push_supported:
            self._push_quote_ids.discard(instrument_id)
            await self._maybe_drop_push_code(instrument_id)

    async def _subscribe_order_book_depth(self, command: SubscribeOrderBook) -> None:
        instrument_id = command.instrument_id
        if self._quote_push_supported:
            self._push_depth_ids.add(instrument_id)
            await self._add_push_code(instrument_id)
        if self._should_poll():
            key = ("depth", instrument_id)
            task = self.create_task(
                self._poll_order_book_depth(instrument_id),
                log_msg=f"bigqmt_depth_poll: {instrument_id}",
            )
            if task is not None:
                self._subscription_tasks[key] = task

    async def _unsubscribe_order_book_depth(self, command: UnsubscribeOrderBook) -> None:
        instrument_id = command.instrument_id
        self._cancel_task(("depth", instrument_id))
        if self._quote_push_supported:
            self._push_depth_ids.discard(instrument_id)
            await self._maybe_drop_push_code(instrument_id)

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        bar_type = command.bar_type
        key = ("bar", bar_type)
        task = self.create_task(
            self._poll_bars(bar_type),
            log_msg=f"bigqmt_bar_poll: {bar_type}",
        )
        if task is not None:
            self._subscription_tasks[key] = task

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        self._cancel_task(("bar", command.bar_type))

    async def _request(self, request: RequestData) -> None:
        raise NotImplementedError("BigQMT custom data requests are not implemented")

    async def _request_instrument(self, request: RequestInstrument) -> None:
        await self._instrument_provider.load_async(request.instrument_id)
        instrument = self._instrument_provider.find(request.instrument_id)
        if instrument is None:
            self._log.warning(f"No BigQMT instrument found for {request.instrument_id}")
            return
        self._handle_instrument(instrument, request.id, request.start, request.end, request.params)

    async def _request_instruments(self, request: RequestInstruments) -> None:
        await self._instrument_provider.initialize()
        self._handle_instruments(
            request.venue,
            self._instrument_provider.list_all(),
            request.id,
            request.start,
            request.end,
            request.params,
        )

    async def _request_bars(self, request: RequestBars) -> None:
        bar_type = request.bar_type
        symbol = instrument_id_to_bigqmt_symbol(bar_type.instrument_id)
        period = bar_type_to_qmt_period(bar_type)
        data = await self._client.get_market_data_ex(
            stock_list=[symbol],
            period=period,
            dividend_type=self._config.adjust_type,
        )
        bars = self._dataframe_to_bars(bar_type, symbol, data)
        self._handle_bars(
            bar_type,
            bars,
            request.id,
            request.start,
            request.end,
            request.params,
        )

    async def _poll_quote_tick(self, instrument_id: InstrumentId) -> None:
        symbol = instrument_id_to_bigqmt_symbol(instrument_id)
        failure_streak = 0
        while True:
            try:
                data = await self._client.get_full_tick([symbol])
                tick_data = _lookup_symbol(data, symbol)
                if tick_data:
                    tick = parse_full_tick_as_quote_tick(
                        instrument_id=instrument_id,
                        payload=tick_data,
                        ts_init=self._clock.timestamp_ns(),
                    )
                    if tick is not None:
                        self._handle_data(tick)
                failure_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"BigQMT quote poll for {instrument_id} failed "
                        f"(streak={failure_streak}): {exc}",
                    )
            await asyncio.sleep(self._config.poll_interval_secs)

    async def _poll_order_book_depth(self, instrument_id: InstrumentId) -> None:
        symbol = instrument_id_to_bigqmt_symbol(instrument_id)
        failure_streak = 0
        while True:
            try:
                data = await self._client.get_full_tick([symbol])
                tick_data = _lookup_symbol(data, symbol)
                if tick_data:
                    depth = parse_full_tick_as_order_book_depth10(
                        instrument_id=instrument_id,
                        payload=tick_data,
                        ts_init=self._clock.timestamp_ns(),
                    )
                    if depth is not None:
                        self._handle_data(depth)
                failure_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"BigQMT depth poll for {instrument_id} failed "
                        f"(streak={failure_streak}): {exc}",
                    )
            await asyncio.sleep(self._config.poll_interval_secs)

    async def _poll_bars(self, bar_type: BarType) -> None:
        symbol = instrument_id_to_bigqmt_symbol(bar_type.instrument_id)
        period = bar_type_to_qmt_period(bar_type)
        last_ts_event: int | None = None
        failure_streak = 0
        while True:
            try:
                data = await self._client.get_market_data_ex(
                    stock_list=[symbol],
                    period=period,
                    count=1,
                    dividend_type=self._config.adjust_type,
                )
                bars = self._dataframe_to_bars(bar_type, symbol, data)
                for bar in bars:
                    # Only forward a newly-closed bar (dedupe by ts_event).
                    if last_ts_event is None or bar.ts_event > last_ts_event:
                        last_ts_event = bar.ts_event
                        self._handle_data(bar)
                failure_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"BigQMT bar poll for {bar_type} failed "
                        f"(streak={failure_streak}): {exc}",
                    )
            await asyncio.sleep(self._config.poll_interval_secs)

    def _should_poll(self) -> bool:
        """
        Return True if a poll task should back a new subscription.

        When push is unsupported, polling is the only feed so it always runs.
        When push is active, polling only runs as a fallback if ``poll_enabled``.
        """
        if not self._quote_push_supported:
            return True
        return self._config.poll_enabled

    async def _add_push_code(self, instrument_id: InstrumentId) -> None:
        symbol = instrument_id_to_bigqmt_symbol(instrument_id)
        if symbol in self._push_codes:
            return
        self._push_codes.add(symbol)
        await self._sync_quote_subscription()

    async def _maybe_drop_push_code(self, instrument_id: InstrumentId) -> None:
        # Keep the code while any quote OR depth subscriber still wants it.
        if instrument_id in self._push_quote_ids or instrument_id in self._push_depth_ids:
            return
        symbol = instrument_id_to_bigqmt_symbol(instrument_id)
        if symbol not in self._push_codes:
            return
        self._push_codes.discard(symbol)
        await self._sync_quote_subscription()

    async def _sync_quote_subscription(self) -> None:
        """
        (Re)subscribe the whole-quote push feed to cover the current union.

        The service ref-counts/dedups per code-combination, so re-subscribing the
        full union and retiring the previous sub id is idempotent server-side.
        """
        if not self._quote_push_supported:
            return
        prev_sub_id = self._push_sub_id
        if not self._push_codes:
            self._push_sub_id = None
            if prev_sub_id is not None:
                try:
                    await self._client.unsubscribe_whole_quote(prev_sub_id)
                except Exception as exc:
                    self._log.warning(f"BigQMT whole-quote unsubscribe failed: {exc}")
            return
        codes = sorted(self._push_codes)
        try:
            sub_id = await self._client.subscribe_whole_quote(codes, self._on_quote_push)
        except Exception as exc:
            self._log.error(f"BigQMT whole-quote subscribe failed for {codes}: {exc}")
            return
        self._push_sub_id = sub_id
        if prev_sub_id is not None and prev_sub_id != sub_id:
            try:
                await self._client.unsubscribe_whole_quote(prev_sub_id)
            except Exception as exc:
                self._log.warning(f"BigQMT whole-quote unsubscribe failed: {exc}")

    def _on_quote_push(self, data) -> None:
        # Invoked on the service's push subscriber thread (and once, inline, on
        # the RPC executor thread for the prime snapshot). Marshal onto the loop.
        if not isinstance(data, dict):
            return
        self._loop.call_soon_threadsafe(self._handle_quote_push, dict(data))

    def _handle_quote_push(self, data: dict) -> None:
        ts_init = self._clock.timestamp_ns()
        for code, tick_data in data.items():
            if not tick_data:
                continue
            try:
                instrument_id = bigqmt_symbol_to_instrument_id(str(code))
            except Exception as exc:
                self._log.warning(f"BigQMT quote push: cannot map code {code!r}: {exc}")
                continue
            if instrument_id in self._push_quote_ids:
                tick = parse_full_tick_as_quote_tick(
                    instrument_id=instrument_id,
                    payload=tick_data,
                    ts_init=ts_init,
                )
                if tick is not None:
                    self._handle_data(tick)
            if instrument_id in self._push_depth_ids:
                depth = parse_full_tick_as_order_book_depth10(
                    instrument_id=instrument_id,
                    payload=tick_data,
                    ts_init=ts_init,
                )
                if depth is not None:
                    self._handle_data(depth)

    def _dataframe_to_bars(self, bar_type: BarType, symbol: str, data: dict) -> list:
        df = _lookup_symbol(data, symbol)
        if df is None:
            return []
        # get_market_data_ex returns {code: DataFrame}. Iterate rows to Bars.
        try:
            records = df.to_dict("records")
            index = list(df.index)
        except AttributeError:
            # Already a list of dicts (pandas unavailable on the deserializer path).
            records = list(df) if isinstance(df, list) else []
            index = [row.get("time") for row in records]
        bars = []
        for i, row in enumerate(records):
            payload = dict(row)
            # QMT bar time is in the DataFrame index (timetag ms) when not a column.
            if "time" not in payload and "time_ms" not in payload and i < len(index):
                payload["time_ms"] = index[i]
            elif "time" in payload and "time_ms" not in payload:
                payload["time_ms"] = payload["time"]
            bar = parse_bar(
                bar_type=bar_type,
                payload=payload,
                ts_init=self._clock.timestamp_ns(),
            )
            if bar is not None:
                bars.append(bar)
        return bars

    def _cancel_task(self, key: object) -> None:
        task = self._subscription_tasks.pop(key, None)
        if task is not None:
            task.cancel()

    def _send_all_instruments_to_data_engine(self) -> None:
        for instrument in self._instrument_provider.get_all().values():
            self._handle_data(instrument)
        for currency in self._instrument_provider.currencies().values():
            self._cache.add_currency(currency)


def _lookup_symbol(data: dict, symbol: str):
    if not isinstance(data, dict):
        return None
    if symbol in data:
        return data[symbol]
    upper = symbol.upper()
    for key, value in data.items():
        if str(key).upper() == upper:
            return value
    return None
