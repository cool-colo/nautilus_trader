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

from nautilus_trader.adapters.qmt.common import instrument_id_to_qmt_symbol
from nautilus_trader.adapters.qmt.common import normalize_qmt_symbol
from nautilus_trader.adapters.qmt.common import parse_equity
from nautilus_trader.adapters.qmt.http import QMTHttpClient
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.model.identifiers import InstrumentId


class QMTInstrumentProviderConfig(InstrumentProviderConfig, frozen=True):
    """
    Configuration for ``QMTInstrumentProvider``.

    ``quant-qmt-proxy`` does not expose an exchange-wide stock master endpoint, so
    use ``load_ids`` or ``load_symbols`` for deterministic startup loading. If
    ``load_all=True`` is set, ``load_symbols`` or ``filters["symbols"]`` is required.
    """

    load_symbols: frozenset[str] | None = None
    complete_details: bool = False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QMTInstrumentProviderConfig):
            return False
        return (
            self.load_all == other.load_all
            and self.load_ids == other.load_ids
            and self.filters == other.filters
            and self.load_symbols == other.load_symbols
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
            self._log.warning(
                "QMT cannot load all instruments without configured symbols; "
                "set load_ids, load_symbols, or filters={'symbols': [...]}.",
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

    async def _load_symbols(self, symbols: list[str]) -> None:
        for symbol in symbols:
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
