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

from nautilus_trader.adapters.qmt.common import instrument_id_to_qmt_symbol
from nautilus_trader.adapters.qmt.common import normalize_qmt_symbol
from nautilus_trader.adapters.qmt.common import parse_equity
from nautilus_trader.adapters.qmt.http import QMTHttpClient
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.model.identifiers import InstrumentId


# "沪深京A股" is the QMT whole-market A-share sector (Shanghai + Shenzhen +
# Beijing) — a single sector that enumerates every tradable A-share.
DEFAULT_LOAD_ALL_SECTORS: frozenset[str] = frozenset({"沪深京A股"})

# Max concurrent instrument-detail fetches during startup loading. Kept in step
# with the HTTP client's connection pool so gather() saturates but never
# overruns it.
_LOAD_CONCURRENCY = 32


class QMTInstrumentProviderConfig(InstrumentProviderConfig, frozen=True):
    """
    Configuration for ``QMTInstrumentProvider``.

    For deterministic startup loading, use ``load_ids`` or ``load_symbols``. When
    ``load_all=True`` and neither ``load_symbols`` nor ``filters["symbols"]`` is
    given, the provider enumerates the exchange-wide stock master by aggregating
    the symbols of ``load_all_sectors`` from ``quant-qmt-proxy``'s ``/sectors``
    endpoint (default the whole-market A-share sectors).
    """

    load_symbols: frozenset[str] | None = None
    load_all_sectors: frozenset[str] | None = None
    complete_details: bool = False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QMTInstrumentProviderConfig):
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
        filters = tuple(sorted((key, repr(value)) for key, value in self.filters.items())) if self.filters else None
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


class QMTInstrumentProvider(InstrumentProvider):
    """
    Loads Chinese A-share ``Equity`` instruments from quant-qmt-proxy.
    """

    def __init__(
        self,
        client: QMTHttpClient,
        clock: LiveClock,
        config: QMTInstrumentProviderConfig | None = None,
    ) -> None:
        super().__init__(config=config)
        self._client = client
        self._clock = clock
        self._config_qmt = config or QMTInstrumentProviderConfig()

    async def load_all_async(self, filters: dict | None = None) -> None:
        symbols = self._resolve_load_all_symbols(filters)
        if not symbols:
            symbols = await self._resolve_sector_symbols()
        if not symbols:
            self._log.warning(
                "QMT cannot load all instruments: no explicit symbols configured and "
                "the /sectors endpoint returned no symbols for the configured sectors. "
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
        symbols = [instrument_id_to_qmt_symbol(instrument_id) for instrument_id in instrument_ids]
        await self._load_symbols(symbols)

    async def load_async(self, instrument_id: InstrumentId, filters: dict | None = None) -> None:
        if self.find(instrument_id) is not None:
            return
        await self.load_ids_async([instrument_id], filters)

    def _resolve_load_all_symbols(self, filters: dict | None) -> list[str]:
        filters = filters or self._config_qmt.filters or {}
        raw_symbols: Any = filters.get("symbols") or self._config_qmt.load_symbols or []
        return [normalize_qmt_symbol(str(symbol)) for symbol in raw_symbols if str(symbol).strip()]

    async def _resolve_sector_symbols(self) -> list[str]:
        """
        Enumerate the exchange-wide stock master from the proxy ``/sectors`` endpoint.

        Aggregates and dedupes the symbols of the configured ``load_all_sectors``
        (default the whole-market A-share sectors), preserving first-seen order.
        """
        wanted = self._config_qmt.load_all_sectors or DEFAULT_LOAD_ALL_SECTORS
        try:
            sectors = await self._client.get_sectors()
        except Exception as exc:  # pragma: no cover - network/proxy failure path
            self._log.warning(f"QMT failed to load sectors for load_all: {exc}")
            return []
        seen: set[str] = set()
        symbols: list[str] = []
        for sector in sectors:
            if str(sector.get("sector_name", "")) not in wanted:
                continue
            for raw in sector.get("symbols", []) or []:
                if not str(raw).strip():
                    continue
                symbol = normalize_qmt_symbol(str(raw))
                if symbol not in seen:
                    seen.add(symbol)
                    symbols.append(symbol)
        return symbols

    async def _load_symbols(self, symbols: list[str]) -> None:
        # Fetch instrument details concurrently. Loading the whole-market universe
        # (thousands of names) serially would exceed the node's connection timeout,
        # so bound concurrency to the HTTP connection pool and gather in parallel.
        semaphore = asyncio.Semaphore(_LOAD_CONCURRENCY)

        async def _load_one(symbol: str) -> None:
            async with semaphore:
                detail = await self._client.get_instrument(
                    symbol,
                    complete=self._config_qmt.complete_details,
                )
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
            for symbol, result in zip(symbols, results)
            if isinstance(result, BaseException)
        ]
        if failures:
            sample = ", ".join(f"{symbol}: {exc!r}" for symbol, exc in failures[:5])
            self._log.warning(
                f"QMT failed to load {len(failures)}/{len(symbols)} instrument details "
                f"(showing up to 5): {sample}",
            )
