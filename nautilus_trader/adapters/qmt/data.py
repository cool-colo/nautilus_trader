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
from urllib.parse import urlencode

import aiohttp

from nautilus_trader.adapters.qmt.common import bar_type_to_qmt_period
from nautilus_trader.adapters.qmt.common import instrument_id_to_qmt_symbol
from nautilus_trader.adapters.qmt.common import parse_bar
from nautilus_trader.adapters.qmt.common import parse_quote_tick
from nautilus_trader.adapters.qmt.common import timestamp_to_qmt_str
from nautilus_trader.adapters.qmt.config import QMTDataClientConfig
from nautilus_trader.adapters.qmt.constants import QMT_VENUE
from nautilus_trader.adapters.qmt.http import QMTHttpClient
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProvider
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.enums import LogColor
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.data.messages import RequestData
from nautilus_trader.data.messages import RequestInstrument
from nautilus_trader.data.messages import RequestInstruments
from nautilus_trader.data.messages import RequestQuoteTicks
from nautilus_trader.data.messages import SubscribeBars
from nautilus_trader.data.messages import SubscribeData
from nautilus_trader.data.messages import SubscribeQuoteTicks
from nautilus_trader.data.messages import UnsubscribeBars
from nautilus_trader.data.messages import UnsubscribeData
from nautilus_trader.data.messages import UnsubscribeQuoteTicks
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId


class QMTDataClient(LiveMarketDataClient):
    """
    Live market data client for Chinese A-shares via quant-qmt-proxy.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        http_client: QMTHttpClient,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: QMTInstrumentProvider,
        config: QMTDataClientConfig,
        name: str | None,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(name or QMT_VENUE.value),
            venue=QMT_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )
        self._http_client = http_client
        self._config = config
        self._ws_base_url = config.base_url_ws.rstrip("/")
        self._subscription_ids: dict[object, str] = {}
        self._subscription_tasks: dict[object, asyncio.Task] = {}

        self._log.info(f"{config.base_url_http=}", LogColor.BLUE)
        self._log.info(f"{config.base_url_ws=}", LogColor.BLUE)
        self._log.info(f"{config.adjust_type=}", LogColor.BLUE)

    async def _connect(self) -> None:
        await self._http_client.connect()
        await self._instrument_provider.initialize()
        self._send_all_instruments_to_data_engine()

    async def _disconnect(self) -> None:
        for task in list(self._subscription_tasks.values()):
            task.cancel()
        self._subscription_tasks.clear()
        for subscription_id in list(self._subscription_ids.values()):
            try:
                await self._http_client.delete_subscription(subscription_id)
            except Exception as exc:
                self._log.warning(f"Failed deleting QMT subscription {subscription_id}: {exc}")
        self._subscription_ids.clear()
        await self._http_client.close()

    async def _subscribe(self, command: SubscribeData) -> None:
        raise NotImplementedError("QMT custom data subscriptions are not implemented")

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        raise NotImplementedError("QMT custom data subscriptions are not implemented")

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        instrument_id = command.instrument_id
        symbol = instrument_id_to_qmt_symbol(instrument_id)
        info = await self._http_client.create_quote_subscription(
            symbols=[symbol],
            period="tick",
            adjust_type=self._config.adjust_type,
            count=0,
        )
        subscription_id = info["subscription_id"]
        self._subscription_ids[instrument_id] = subscription_id
        task = self.create_task(
            self._stream_subscription(subscription_id, instrument_id=instrument_id),
            log_msg=f"qmt_quote_stream: {instrument_id}",
        )
        if task is not None:
            self._subscription_tasks[instrument_id] = task

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        await self._unsubscribe_key(command.instrument_id)

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        bar_type = command.bar_type
        symbol = instrument_id_to_qmt_symbol(bar_type.instrument_id)
        period = bar_type_to_qmt_period(bar_type)
        info = await self._http_client.create_quote_subscription(
            symbols=[symbol],
            period=period,
            adjust_type=self._config.adjust_type,
            count=0,
        )
        subscription_id = info["subscription_id"]
        self._subscription_ids[bar_type] = subscription_id
        task = self.create_task(
            self._stream_subscription(subscription_id, bar_type=bar_type),
            log_msg=f"qmt_bar_stream: {bar_type}",
        )
        if task is not None:
            self._subscription_tasks[bar_type] = task

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        await self._unsubscribe_key(command.bar_type)

    async def _request(self, request: RequestData) -> None:
        raise NotImplementedError("QMT custom data requests are not implemented")

    async def _request_instrument(self, request: RequestInstrument) -> None:
        await self._instrument_provider.load_async(request.instrument_id)
        instrument = self._instrument_provider.find(request.instrument_id)
        if instrument is None:
            self._log.warning(f"No QMT instrument found for {request.instrument_id}")
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

    async def _request_quote_ticks(self, request: RequestQuoteTicks) -> None:
        symbol = instrument_id_to_qmt_symbol(request.instrument_id)
        items = await self._http_client.get_tick_history(
            symbols=[symbol],
            start_time=timestamp_to_qmt_str(request.start),
            end_time=timestamp_to_qmt_str(request.end),
            adjust_type=self._config.adjust_type,
        )
        ticks = []
        for item in items:
            if item.get("symbol") != symbol:
                continue
            for payload in item.get("ticks", []):
                tick = parse_quote_tick(
                    instrument_id=request.instrument_id,
                    payload=payload,
                    ts_init=self._clock.timestamp_ns(),
                )
                if tick is not None:
                    ticks.append(tick)
        self._handle_quote_ticks(
            request.instrument_id,
            ticks,
            request.id,
            request.start,
            request.end,
            request.params,
        )

    async def _request_bars(self, request: RequestBars) -> None:
        symbol = instrument_id_to_qmt_symbol(request.bar_type.instrument_id)
        period = bar_type_to_qmt_period(request.bar_type)
        items = await self._http_client.get_kline_history(
            symbols=[symbol],
            period=period,
            start_time=timestamp_to_qmt_str(request.start),
            end_time=timestamp_to_qmt_str(request.end),
            adjust_type=self._config.adjust_type,
            fill_data=True,
        )
        bars = []
        for item in items:
            if item.get("symbol") != symbol:
                continue
            for payload in item.get("bars", []):
                bar = parse_bar(
                    bar_type=request.bar_type,
                    payload=payload,
                    ts_init=self._clock.timestamp_ns(),
                )
                if bar is not None:
                    bars.append(bar)
        self._handle_bars(
            request.bar_type,
            bars,
            request.id,
            request.start,
            request.end,
            request.params,
        )

    async def _unsubscribe_key(self, key: object) -> None:
        task = self._subscription_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        subscription_id = self._subscription_ids.pop(key, None)
        if subscription_id:
            await self._http_client.delete_subscription(subscription_id)

    async def _stream_subscription(
        self,
        subscription_id: str,
        instrument_id: InstrumentId | None = None,
        bar_type=None,
    ) -> None:
        url = f"{self._ws_base_url}/ws/quote/{subscription_id}"
        if self._config.api_key:
            url = f"{url}?{urlencode({'token': self._config.api_key})}"

        label = bar_type if bar_type is not None else instrument_id
        backoff = 1.0
        max_backoff = 30.0
        failure_streak = 0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url) as ws:
                        if failure_streak:
                            self._log.info(
                                f"QMT quote stream reconnected for {label} "
                                f"after {failure_streak} failure(s)",
                            )
                            failure_streak = 0
                            backoff = 1.0
                        await self._consume_ws(ws, instrument_id=instrument_id, bar_type=bar_type)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_streak += 1
                if failure_streak == 1 or failure_streak % 30 == 0:
                    self._log.warning(
                        f"QMT quote stream for {label} failed "
                        f"(streak={failure_streak}): {exc}",
                    )
            else:
                # ws_connect exited cleanly (server closed). Treat as a drop and reconnect.
                failure_streak += 1
                if failure_streak == 1:
                    self._log.warning(f"QMT quote stream for {label} closed; reconnecting")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _consume_ws(
        self,
        ws,
        instrument_id: InstrumentId | None = None,
        bar_type=None,
    ) -> None:
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                payload = message.json()
                msg_type = payload.get("type")
                if msg_type == "error":
                    # e.g. "missing-subscription" if the proxy lost the subscription
                    # (proxy restart). Surface it; the reconnect loop will keep retrying.
                    self._log.warning(f"QMT quote stream error message: {payload.get('message')}")
                    continue
                if msg_type != "quote":
                    continue
                event = payload.get("data") or {}
                data = event.get("data") or {}
                if event.get("payload_type") == "tick" and instrument_id is not None:
                    tick = parse_quote_tick(
                        instrument_id=instrument_id,
                        payload=data,
                        ts_init=self._clock.timestamp_ns(),
                    )
                    if tick is not None:
                        self._handle_data(tick)
                elif event.get("payload_type") == "kline" and bar_type is not None:
                    bar = parse_bar(
                        bar_type=bar_type,
                        payload=data,
                        ts_init=self._clock.timestamp_ns(),
                    )
                    if bar is not None:
                        self._handle_data(bar)
            elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                break

    def _send_all_instruments_to_data_engine(self) -> None:
        for instrument in self._instrument_provider.get_all().values():
            self._handle_data(instrument)
        for currency in self._instrument_provider.currencies().values():
            self._cache.add_currency(currency)
