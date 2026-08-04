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
Nautilus Trader adapter for Big QMT (大QMT) via the xtquant_big_convert Redis RPC bridge.
"""

from nautilus_trader.adapters.bigqmt.config import BigQMTDataClientConfig
from nautilus_trader.adapters.bigqmt.config import BigQMTExecClientConfig
from nautilus_trader.adapters.bigqmt.factories import BigQMTLiveDataClientFactory
from nautilus_trader.adapters.bigqmt.factories import BigQMTLiveExecClientFactory
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProvider
from nautilus_trader.adapters.bigqmt.providers import BigQMTInstrumentProviderConfig


__all__ = [
    "BigQMTDataClientConfig",
    "BigQMTExecClientConfig",
    "BigQMTInstrumentProvider",
    "BigQMTInstrumentProviderConfig",
    "BigQMTLiveDataClientFactory",
    "BigQMTLiveExecClientFactory",
]
