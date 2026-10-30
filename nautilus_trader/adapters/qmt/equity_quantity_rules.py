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

"""Exchange quantity rules for continuous-auction A-share limit orders."""

from __future__ import annotations


def a_share_limit_order_quantity_rules(symbol: str) -> dict[str, int | bool] | None:
    """Return regular-order limits; an entire remaining sell balance may be odd-sized.

    QMT does not supply these fields, so the adapter attaches the exchange rules
    to Instrument.info. Limits apply to continuous-auction limit orders only.
    Non-A-share instruments are outside this rule table.
    """
    code, _, exchange = symbol.rpartition(".")
    if len(code) != 6 or not code.isdigit():
        return None

    if exchange == "SH" and code.startswith(("688", "689")):
        minimum, maximum, increment = 200, 100_000, 1
    elif exchange == "SZ" and code.startswith(("300", "301")):
        minimum, maximum, increment = 100, 300_000, 100
    elif (
        exchange == "SH" and code.startswith(("600", "601", "603", "605"))
    ) or (
        exchange == "SZ" and code.startswith(("000", "001", "002", "003"))
    ):
        minimum, maximum, increment = 100, 1_000_000, 100
    elif exchange == "BJ" and code.startswith(("43", "83", "87", "88", "920")):
        minimum, maximum, increment = 100, 1_000_000, 1
    else:
        return None

    return {
        "min_quantity": minimum,
        "max_quantity": maximum,
        "quantity_increment": increment,
        "allow_odd_lot_sell": True,
    }
