"""模拟资金账户：现金管理、持仓跟踪、交易执行、费用计算。

费用模型（按基金类型区分）：
  - 指数型 / ETF联接:  申购 0.12%,  赎回 0.50%
  - 混合型 / 股票型:    申购 0.15%,  赎回 0.50%
  - 债券型:            申购 0.08%,  赎回 0.10%
  - QDII:              申购 0.15%,  赎回 0.50%
  - 短期内赎回惩罚（持有 < 7 天）: 1.50%

所有交易以份额为单位，价格以当日净值为准。
"""

from dataclasses import dataclass
from datetime import date as DateType
from typing import Optional

# ── 费率表 ────────────────────────────────────────────
FEE_SCHEDULE = {
    "指数型":    {"subscribe": 0.0012, "redeem": 0.0050},
    "ETF联接":   {"subscribe": 0.0012, "redeem": 0.0050},
    "ETF":       {"subscribe": 0.0012, "redeem": 0.0050},
    "LOF":       {"subscribe": 0.0012, "redeem": 0.0050},
    "联接基金":   {"subscribe": 0.0012, "redeem": 0.0050},
    "混合型":    {"subscribe": 0.0015, "redeem": 0.0050},
    "股票型":    {"subscribe": 0.0015, "redeem": 0.0050},
    "债券型":    {"subscribe": 0.0008, "redeem": 0.0010},
    "QDII":      {"subscribe": 0.0015, "redeem": 0.0050},
}
DEFAULT_FEE = {"subscribe": 0.0015, "redeem": 0.0050}
SHORT_TERM_REDEEM_FEE = 0.015  # 持有 < 7 天赎回费
SHORT_TERM_DAYS = 7

# ── 仓位约束 ─────────────────────────────────────────────
MAX_SINGLE_POSITION_PCT = 0.05   # 单只基金占总资产上限
MAX_TOTAL_POSITION_PCT = 0.80    # 总仓位上限
MAX_HOLDINGS = 8                 # 最多同时持有基金数


@dataclass
class Trade:
    """单笔交易记录。"""
    date: DateType
    code: str
    name: str
    action: str          # "买入" / "卖出"
    nav: float           # 成交净值
    units: float         # 成交份额
    amount: float        # 成交金额（扣费后）
    fee: float           # 手续费
    reason: str = ""     # 触发规则


@dataclass
class DailySnapshot:
    """每日快照。"""
    date: DateType
    cash: float
    position_value: float
    total_value: float
    daily_return: float = 0.0


class Portfolio:
    """模拟资金账户。"""

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        # 持仓: {code: {"units": float, "cost": float, "buy_date": DateType}}
        self.positions: dict = {}
        self.trade_log: list[Trade] = []
        self.snapshots: list[DailySnapshot] = []

    # ── 查询 ──────────────────────────────────────────

    def total_value(self, nav_dict: dict[str, float]) -> float:
        """当前总资产 = 现金 + Σ(份额 × 最新净值)。"""
        pv = sum(
            pos["units"] * nav_dict.get(code, 0.0)
            for code, pos in self.positions.items()
        )
        return self.cash + pv

    def position_count(self) -> int:
        return len(self.positions)

    def holding_days(self, code: str, today: DateType) -> int:
        """持有天数（用于赎回费率判断）。"""
        if code not in self.positions:
            return 0
        return (today - self.positions[code]["buy_date"]).days

    # ── 交易 ──────────────────────────────────────────

    def _get_fee_rate(self, fund_type: str, direction: str) -> float:
        """查询费率。"""
        return FEE_SCHEDULE.get(fund_type, DEFAULT_FEE).get(direction, 0.0015)

    def buy(
        self, date: DateType, code: str, name: str,
        nav: float, amount: float, fund_type: str = "混合型",
        reason: str = "",
    ) -> Optional[Trade]:
        """买入基金。

        Args:
            amount: 买入金额（含手续费前）
        Returns:
            Trade or None（现金不足时返回 None）
        """
        if amount <= 0 or self.cash < amount:
            return None

        fee_rate = self._get_fee_rate(fund_type, "subscribe")
        fee = amount * fee_rate
        net_amount = amount - fee
        units = net_amount / nav if nav > 0 else 0.0

        if units <= 0:
            return None

        self.cash -= amount
        self.positions[code] = {
            "units": units,
            "cost": nav,
            "buy_date": date,
        }
        trade = Trade(
            date=date, code=code, name=name, action="买入",
            nav=nav, units=units, amount=net_amount, fee=fee,
            reason=reason,
        )
        self.trade_log.append(trade)
        return trade

    def sell(
        self, date: DateType, code: str, name: str,
        nav: float, units: float, fund_type: str = "混合型",
        reason: str = "",
    ) -> Optional[Trade]:
        """卖出基金。

        Args:
            units: 卖出份额（若为 1.0 表示全部清仓，按实际持仓算）
        Returns:
            Trade or None
        """
        if code not in self.positions:
            return None

        pos = self.positions[code]
        actual_units = units if units < 1.0 else pos["units"]
        actual_units = min(actual_units, pos["units"])

        if actual_units <= 0:
            return None

        # 赎回费：短期惩罚 vs 正常费率
        hold_days = self.holding_days(code, date)
        if hold_days < SHORT_TERM_DAYS:
            fee_rate = SHORT_TERM_REDEEM_FEE
        else:
            fee_rate = self._get_fee_rate(fund_type, "redeem")

        gross = actual_units * nav
        fee = gross * fee_rate
        net_amount = gross - fee

        self.cash += net_amount
        pos["units"] -= actual_units
        if pos["units"] < 1e-8:
            del self.positions[code]

        trade = Trade(
            date=date, code=code, name=name, action="卖出",
            nav=nav, units=actual_units, amount=net_amount, fee=fee,
            reason=reason,
        )
        self.trade_log.append(trade)
        return trade

    # ── 快照 ──────────────────────────────────────────

    def snapshot(self, date: DateType, nav_dict: dict[str, float]):
        """记录当日资产快照。"""
        pv = sum(
            pos["units"] * nav_dict.get(code, 0.0)
            for code, pos in self.positions.items()
        )
        total = self.cash + pv

        prev_total = self.initial_capital
        if self.snapshots:
            prev_total = self.snapshots[-1].total_value

        daily_ret = (total / prev_total - 1) if prev_total > 0 else 0.0

        self.snapshots.append(DailySnapshot(
            date=date,
            cash=self.cash,
            position_value=pv,
            total_value=total,
            daily_return=daily_ret,
        ))

    # ── 汇总 ──────────────────────────────────────────

    def stats(self) -> dict:
        """账户汇总指标。"""
        if not self.snapshots:
            return {}

        total_ret = (
            self.snapshots[-1].total_value / self.initial_capital - 1
        )
        n_days = len(self.snapshots)
        n_years = n_days / 244

        # 年化
        annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0.0

        # 夏普
        returns = [s.daily_return for s in self.snapshots if s.total_value > 0]
        if len(returns) > 1:
            import numpy as np
            daily_vol = float(np.std(returns))
            annual_vol = daily_vol * np.sqrt(244)
            sharpe = (annual_ret - 0.02) / annual_vol if annual_vol > 0 else 0.0
        else:
            annual_vol = 0.0
            sharpe = 0.0

        # 最大回撤
        values = [s.total_value for s in self.snapshots]
        peak = values[0]
        max_dd = 0.0
        dd_start = dd_end = None
        temp_start = None
        for i, v in enumerate(values):
            if v > peak:
                peak = v
                temp_start = None
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
                if temp_start is None:
                    temp_start = self.snapshots[i - 1].date if i > 0 else self.snapshots[0].date
                dd_start = temp_start
                dd_end = self.snapshots[i].date

        # 交易统计
        buys = [t for t in self.trade_log if t.action == "买入"]
        sells = [t for t in self.trade_log if t.action == "卖出"]
        sell_codes = set(t.code for t in sells)
        profitable = sum(
            1 for code in sell_codes
            for t in sells if t.code == code and _trade_pl(t, self.trade_log) > 0
        )

        return {
            "total_return": total_ret,
            "annual_return": annual_ret,
            "annual_volatility": annual_vol,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "dd_start": dd_start,
            "dd_end": dd_end,
            "total_trades": len(self.trade_log),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "profitable_sells": profitable,
            "win_rate": profitable / len(sells) if sells else 0.0,
            "total_fees": sum(t.fee for t in self.trade_log),
        }


def _trade_pl(trade: Trade, trade_log: list[Trade]) -> float:
    """估算一笔卖出交易的盈亏。"""
    buy_trades = [
        t for t in trade_log
        if t.action == "买入" and t.code == trade.code and t.date <= trade.date
    ]
    if not buy_trades:
        return 0.0
    avg_buy_nav = sum(t.nav * t.units for t in buy_trades) / sum(t.units for t in buy_trades)
    return (trade.nav - avg_buy_nav) * trade.units