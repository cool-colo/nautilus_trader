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

        self._log.info(f"{config.redis_host=}", LogColor.BLUE)
        self._log.info(f"{config.redis_port=}", LogColor.BLUE)
        self._log.info(f"{config.transport=}", LogColor.BLUE)
        self._log.info(f"{config.poll_interval_secs=}", LogColor.BLUE)
        self._log.info(f"{config.adjust_type=}", LogColor.BLUE)

    async def _connect(self) -> None:
        await self._client.connect()
        await self._instrument_provider.initialize()
        self._send_all_instruments_to_data_engine()

    async def _disconnect(self) -> None:
        for task in list(self._subscription_tasks.values()):
            task.cancel()
        self._subscription_tasks.clear()
        await self._client.close()

    async def _subscribe(self, command: SubscribeData) -> None:
        raise NotImplementedError("BigQMT custom data subscriptions are not implemented")

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        raise NotImplementedError("BigQMT custom data subscriptions are not implemented")

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        instrument_id = command.instrument_id
        key = ("quote", instrument_id)
        task = self.create_task(
            self._poll_quote_tick(instrument_id),
            log_msg=f"bigqmt_quote_poll: {instrument_id}",
        )
        if task is not None:
            self._subscription_tasks[key] = task

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        self._cancel_task(("quote", command.instrument_id))

    async def _subscribe_order_book_depth(self, command: SubscribeOrderBook) -> None:
        instrument_id = command.instrument_id
        key = ("depth", instrument_id)
        task = self.create_task(
            self._poll_order_book_depth(instrument_id),
            log_msg=f"bigqmt_depth_poll: {instrument_id}",
        )
        if task is not None:
            self._subscription_tasks[key] = task

    async def _unsubscribe_order_book_depth(self, command: UnsubscribeOrderBook) -> None:
        self._cancel_task(("depth", command.instrument_id))

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
