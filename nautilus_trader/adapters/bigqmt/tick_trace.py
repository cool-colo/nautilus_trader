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
"""Local diagnostics for a raw BigQMT tick, without modifying market data types."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BigQMTTickTrace:
    """
    Published to ``bigqmt.tick_trace.{instrument_id}``, in this process only.

    ``ts_event`` and ``ts_init`` are copied verbatim from the delivered market
    data object. Missing or malformed bridge stages remain None. All timestamps
    are UNIX nanoseconds; cross-host differences require synchronized clocks.
    """

    instrument_id: str
    data_kind: str
    ts_event: int
    ts_init: int
    qmt_callback_ns: int | None = None
    pipe_publish_ns: int | None = None
    proxy_pipe_received_ns: int | None = None
    redis_publish_ns: int | None = None
    adapter_redis_received_ns: int | None = None
    adapter_callback_ns: int | None = None


def trace_timestamp(trace: dict, name: str) -> int | None:
    value = trace.get(name)
    return value if type(value) is int and value > 0 else None
