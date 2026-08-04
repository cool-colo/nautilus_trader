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


BIG_QMT_VENUE = Venue("BIGQMT")

BIG_QMT_PRICE_TYPE_LATEST_PRICE = 5
BIG_QMT_PRICE_TYPE_FIX_PRICE = 11

BIG_QMT_ORDER_SIDE_BUY = 23
BIG_QMT_ORDER_SIDE_SELL = 24

# BigQMT (Big QMT / 国金证券大QMT) integer order status codes
# from xtquant_compat.py ORDER_* constants.
BIG_QMT_ORDER_UNREPORTED = 48
BIG_QMT_ORDER_WAIT_REPORTING = 49
BIG_QMT_ORDER_REPORTED = 50
BIG_QMT_ORDER_REPORTED_CANCEL = 51
BIG_QMT_ORDER_PARTSUCC_CANCEL = 52
BIG_QMT_ORDER_PART_CANCEL = 53
BIG_QMT_ORDER_CANCELED = 54
BIG_QMT_ORDER_PART_SUCC = 55
BIG_QMT_ORDER_SUCCEEDED = 56
BIG_QMT_ORDER_JUNK = 57
BIG_QMT_ORDER_UNKNOWN = 255

# Redis Pub/Sub channel templates for execution events (server-side exec_events.py)
BIG_QMT_ORDER_CHANNEL_TPL = "bigqmt:order_events:{account_id}"
BIG_QMT_TRADE_CHANNEL_TPL = "bigqmt:trade_events:{account_id}"
# Quote events channel (optional; server must be configured to publish there)
BIG_QMT_QUOTE_CHANNEL_TPL = "bigqmt:quote_events:{account_id}"
