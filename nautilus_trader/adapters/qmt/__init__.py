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
"""
Nautilus Trader adapter for MiniQMT via quant-qmt-proxy.
"""

from nautilus_trader.adapters.qmt.config import QMTDataClientConfig
from nautilus_trader.adapters.qmt.config import QMTExecClientConfig
from nautilus_trader.adapters.qmt.factories import QMTLiveDataClientFactory
from nautilus_trader.adapters.qmt.factories import QMTLiveExecClientFactory
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProvider
from nautilus_trader.adapters.qmt.providers import QMTInstrumentProviderConfig


__all__ = [
    "QMTDataClientConfig",
    "QMTExecClientConfig",
    "QMTInstrumentProvider",
    "QMTInstrumentProviderConfig",
    "QMTLiveDataClientFactory",
    "QMTLiveExecClientFactory",
]
