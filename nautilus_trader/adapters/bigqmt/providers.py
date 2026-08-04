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
from typing import Any

from nautilus_trader.adapters.bigqmt.client import BigQMTClient
from nautilus_trader.adapters.bigqmt.common import instrument_id_to_bigqmt_symbol
from nautilus_trader.adapters.bigqmt.common import normalize_qmt_symbol
from nautilus_trader.adapters.bigqmt.common import parse_equity
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.model.identifiers import InstrumentId


# "沪深A股" is the Big QMT whole-market A-share sector (Shanghai + Shenzhen),
# enumerating every tradable A-share via get_stock_list_in_sector.
DEFAULT_LOAD_ALL_SECTORS: frozenset[str] = frozenset({"沪深A股"})

# Max concurrent instrument-detail RPCs during startup loading. Kept in step
# with the client's RPC thread pool so gather() saturates but never overruns it.
_LOAD_CONCURRENCY = 32


class BigQMTInstrumentProviderConfig(InstrumentProviderConfig, frozen=True):
    """
    Configuration for ``BigQMTInstrumentProvider``.

    For deterministic startup loading, use ``load_ids`` or ``load_symbols``. When
    ``load_all=True`` and neither ``load_symbols`` nor ``filters["symbols"]`` is
    given, the provider enumerates the exchange-wide stock master by aggregating
    the symbols of ``load_all_sectors`` via ``get_stock_list_in_sector`` (default
    the whole-market A-share sector).
    """

    load_symbols: frozenset[str] | None = None
    load_all_sectors: frozenset[str] | None = None
    complete_details: bool = False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BigQMTInstrumentProviderConfig):
            return False
        return (
            self.load_all == other.load_all
            and self.load_ids == other.load_ids
            and self.filters == other.filters
            and self.load_symbols == other.load_symbols
            and self.load_all_sectors == other.load_all_sectors
            and self.complete_details == other.complete_details
        )

    def __hash__(self) -> int:
        filters = (
            tuple(sorted((key, repr(value)) for key, value in self.filters.items()))
            if self.filters
            else None
        )
        return hash(
            (
                self.load_all,
                self.load_ids,
                filters,
                self.load_symbols,
                self.load_all_sectors,
                self.complete_details,
            ),
        )


class BigQMTInstrumentProvider(InstrumentProvider):
    """
    Loads Chinese A-share ``Equity`` instruments from the Big QMT RPC bridge.
    """

    def __init__(
        self,
        client: BigQMTClient,
        clock: LiveClock,
        config: BigQMTInstrumentProviderConfig | None = None,
    ) -> None:
        super().__init__(config=config)
        self._client = client
        self._clock = clock
        self._config_bigqmt = config or BigQMTInstrumentProviderConfig()

    async def load_all_async(self, filters: dict | None = None) -> None:
        symbols = self._resolve_load_all_symbols(filters)
        if not symbols:
            symbols = await self._resolve_sector_symbols()
        if not symbols:
            self._log.warning(
                "BigQMT cannot load all instruments: no explicit symbols configured and "
                "get_stock_list_in_sector returned no symbols for the configured sectors. "
                "Set load_ids, load_symbols, filters={'symbols': [...]}, or load_all_sectors.",
            )
            return
        await self._load_symbols(symbols)

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        if not instrument_ids:
            self._log.info("No instrument IDs given for loading.")
            return
        symbols = [instrument_id_to_bigqmt_symbol(instrument_id) for instrument_id in instrument_ids]
        await self._load_symbols(symbols)

    async def load_async(self, instrument_id: InstrumentId, filters: dict | None = None) -> None:
        if self.find(instrument_id) is not None:
            return
        await self.load_ids_async([instrument_id], filters)

    def _resolve_load_all_symbols(self, filters: dict | None) -> list[str]:
        filters = filters or self._config_bigqmt.filters or {}
        raw_symbols: Any = filters.get("symbols") or self._config_bigqmt.load_symbols or []
        return [normalize_qmt_symbol(str(symbol)) for symbol in raw_symbols if str(symbol).strip()]

    async def _resolve_sector_symbols(self) -> list[str]:
        """
        Enumerate the exchange-wide stock master via ``get_stock_list_in_sector``.

        Aggregates and dedupes the symbols of the configured ``load_all_sectors``
        (default the whole-market A-share sector), preserving first-seen order.
        """
        wanted = self._config_bigqmt.load_all_sectors or DEFAULT_LOAD_ALL_SECTORS
        seen: set[str] = set()
        symbols: list[str] = []
        for sector_name in wanted:
            try:
                raw_symbols = await self._client.get_stock_list_in_sector(sector_name)
            except Exception as exc:  # pragma: no cover - network/RPC failure path
                self._log.warning(f"BigQMT failed to load sector {sector_name!r}: {exc}")
                continue
            for raw in raw_symbols or []:
                if not str(raw).strip():
                    continue
                symbol = normalize_qmt_symbol(str(raw))
                if symbol not in seen:
                    seen.add(symbol)
                    symbols.append(symbol)
        return symbols

    async def _load_symbols(self, symbols: list[str]) -> None:
        # Fetch instrument details concurrently. Loading the whole-market universe
        # (thousands of names) serially would exceed timeouts, so bound concurrency
        # and gather in parallel.
        semaphore = asyncio.Semaphore(_LOAD_CONCURRENCY)

        async def _load_one(symbol: str) -> None:
            async with semaphore:
                detail = await self._client.get_instrument_detail(symbol)
            fields = detail.get("fields", detail)
            now = self._clock.timestamp_ns()
            instrument = parse_equity(
                symbol=detail.get("symbol", symbol),
                fields=fields,
                ts_event=now,
                ts_init=now,
            )
            self.add(instrument)

        results = await asyncio.gather(
            *(_load_one(symbol) for symbol in symbols),
            return_exceptions=True,
        )
        failures = [
            (symbol, result)
            for symbol, result in zip(symbols, results, strict=False)
            if isinstance(result, BaseException)
        ]
        if failures:
            sample = ", ".join(f"{symbol}: {exc!r}" for symbol, exc in failures[:5])
            self._log.warning(
                f"BigQMT failed to load {len(failures)}/{len(symbols)} instrument details "
                f"(showing up to 5): {sample}",
            )
