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

from nautilus_trader.model.identifiers import Venue


QMT_VENUE = Venue("QMT")
QMT_DEFAULT_HTTP_URL = "http://127.0.0.1:8000"
QMT_DEFAULT_WS_URL = "ws://127.0.0.1:8000"

QMT_PRICE_TYPE_LATEST_PRICE = 5
QMT_PRICE_TYPE_FIX_PRICE = 11

QMT_ORDER_SIDE_BUY = 23
QMT_ORDER_SIDE_SELL = 24

QMT_LIFECYCLE_SUBMITTED = "SUBMITTED"
QMT_LIFECYCLE_ACCEPTED = "ACCEPTED"
QMT_LIFECYCLE_PARTIALLY_FILLED = "PARTIALLY_FILLED"
QMT_LIFECYCLE_FILLED = "FILLED"
QMT_LIFECYCLE_CANCELED = "CANCELED"
QMT_LIFECYCLE_REJECTED = "REJECTED"
QMT_LIFECYCLE_EXPIRED = "EXPIRED"
