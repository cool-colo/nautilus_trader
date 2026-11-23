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

from enum import IntEnum
from enum import unique


@unique
class EBrokerPriceType(IntEnum):
    """BigQMT query/callback price types (m_nOrderPriceType).

    Values follow the QMT inner Python API's enum_EBrokerPriceType:
    https://dict.thinktrader.net/innerApi/
    These are distinct from the submission API's prType values.
    """

    MARKET = 49  # 市价
    LIMIT = 50  # 限价
    BEST = 51  # 最优价
    RIGHTS_ISSUE = 52  # 配股
    TRANSFER_CUSTODY = 53  # 转托
    SUBSCRIPTION = 54  # 申购
    REPO = 55  # 回购
    ALLOTMENT = 56  # 配售
    DESIGNATION = 57  # 指定
    CONVERT_SHARES = 58  # 转股
    REDEMPTION = 59  # 回售
    DIVIDEND = 60  # 股息
    SHENZHEN_ALLOTMENT_CONFIRMATION = 68  # 深圳配售确认
    ALLOTMENT_WAIVER = 69  # 配售放弃
    PLEDGE_WITHOUT_FREEZE = 70  # 无冻质押
    PLEDGE_WITH_FREEZE = 71  # 冻结质押
    RELEASE_PLEDGE_WITHOUT_FREEZE = 72  # 无冻解押
    RELEASE_PLEDGE_WITH_FREEZE = 73  # 解冻解押
    VOTE = 75  # 投票
    RELEASE_PRESELL_OFFER = 77  # 预售要约解除
    FUND_DIVIDEND = 78  # 基金设红
    FUND_SUBSCRIPTION_REDEMPTION = 79  # 基金申赎
    CROSS_MARKET_TRANSFER_CUSTODY = 80  # 跨市转托
    ETF_SUBSCRIPTION = 81  # ETF 申购
    WARRANT_EXERCISE = 83  # 权证行权
    COUNTERPARTY_BEST = 84  # 对手方最优价格
    BEST_FIVE_IOC_TO_LIMIT = 85  # 最优五档即时成交剩余转限价
    OWN_BEST = 86  # 本方最优价格 (MIME_PRICE_FIRST in the source)
    IOC = 87  # 即时成交剩余撤销
    BEST_FIVE_IOC = 88  # 最优五档即时成交剩余撤销
    FOK = 89  # 全额成交并撤单
    FUND_SPLIT_MERGE = 90  # 基金拆合
    BOND_TO_SHARES = 91  # 债转股
    HK_CONNECT_AUCTION_LIMIT = 92  # 港股通竞价限价
    HK_CONNECT_ENHANCED_LIMIT = 93  # 港股通增强限价
    HK_CONNECT_ODD_LOT_LIMIT = 94  # 港股通零股限价
    DIRECT_SECURITY_RETURN = 101  # 直接还券
    COLLATERAL_TRANSFER = 107  # 担保品划转
    ADDITIONAL_ISSUE = ord("j")  # 增发
    NEEQ_FIXED_PRICE = ord("w")  # 全国股转定价
    NEEQ_TRADE_CONFIRMATION = ord("x")  # 全国股转成交确认
    NEEQ_MUTUAL_TRADE_CONFIRMATION = ord("y")  # 全国股转互报成交确认
    NEEQ_LIMIT = ord("z")  # 全国股转限价
