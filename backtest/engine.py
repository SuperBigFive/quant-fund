"""回测引擎：逐日推进，无未来信息泄露。

核心流程:
  对于每一天 t:
    1. 截断 nav_data 只保留 t 及之前的数据
    2. 调用 indicators.compute_indicators()
    3. 调用 scorer.select_sell_candidates() / select_buy_candidates()
    4. 执行卖出 → 释放现金 → 执行买入
    5. 记录当日快照

复用所有现有模块（indicators/scorer/holdings），保证回测与实盘
使用相同的决策逻辑。
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from .portfolio import Portfolio, Trade, DailySnapshot
from .portfolio import (
    MAX_SINGLE_POSITION_PCT,
    MAX_TOTAL_POSITION_PCT,
    MAX_HOLDINGS,
)

logger = logging.getLogger(__name__)


class BacktestEngine:
    """基金策略回测引擎。

    Args:
        config:         完整配置字典
        holding_codes:  初始持仓基金代码列表（仅用于策略的 holding 识别，
                        回测从纯现金开始）
        nav_data:       净值数据 {code: [{date, nav}, ...]}
        universe:       基金池 [{code, name, type, ...}, ...]
        start_date:     回测起点（None = 自动：最早可用数据 + 200天）
        end_date:       回测终点（None = 昨天）
        initial_capital:初始资金
    """

    def __init__(
        self,
        config: dict,
        holding_codes: list[str],
        nav_data: dict,
        universe: list[dict],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        initial_capital: float = 100000.0,
    ):
        self.config = config
        self.strategy = config["strategy"]
        self.holding_codes = holding_codes
        self.nav_data = nav_data
        self.universe = universe
        self.initial_capital = initial_capital

        # 构建辅助索引
        self._code_name = {f["code"]: f["name"] for f in universe}
        self._code_type = {f["code"]: f.get("type", "混合型") for f in universe}

        # 日期范围
        self.start_date = start_date
        self.end_date = end_date or datetime.now()
        self._trading_dates: list[datetime] = []
        self._determine_date_range()

        # 结果
        self.portfolio = Portfolio(initial_capital=initial_capital)
        self.rule_stats: dict = defaultdict(lambda: {"triggers": 0, "wins": 0})
        self.errors: list[str] = []

    # ── 日期范围 ──────────────────────────────────────

    def _determine_date_range(self):
        """确定回测日期范围，确保有足够的历史数据做 MA200。"""
        # 收集所有 NAV 日期
        all_dates: set[datetime] = set()
        for records in self.nav_data.values():
            for r in records:
                d = r["date"]
                if isinstance(d, str):
                    d = datetime.strptime(d[:10], "%Y-%m-%d")
                all_dates.add(d)

        if not all_dates:
            raise ValueError("nav_data 为空，无法确定日期范围")

        sorted_dates = sorted(all_dates)

        # 起点：至少需要 200 天数据给 MA200
        earliest_possible = sorted_dates[200] if len(sorted_dates) > 200 else sorted_dates[0]
        if self.start_date is None:
            self.start_date = earliest_possible
        else:
            self.start_date = max(self.start_date, earliest_possible)

        # 终点
        self.end_date = min(self.end_date, sorted_dates[-1])

        # 交易日列表
        self._trading_dates = [
            d for d in sorted_dates
            if self.start_date <= d <= self.end_date
        ]

        if len(self._trading_dates) < 30:
            raise ValueError(
                f"交易日不足（{len(self._trading_dates)}天），无法回测"
            )

        logger.info(
            "回测区间: %s ~ %s (%d 个交易日)",
            self.start_date.strftime("%Y-%m-%d"),
            self.end_date.strftime("%Y-%m-%d"),
            len(self._trading_dates),
        )

    # ── 数据截断（核心：无未来信息）──────────────────

    def _truncate_nav(self, cutoff_date: datetime) -> dict:
        """截断净值数据：只保留 cutoff_date 及之前的记录。"""
        truncated = {}
        for code, records in self.nav_data.items():
            filtered = []
            for r in records:
                d = r["date"]
                if isinstance(d, str):
                    d = datetime.strptime(d[:10], "%Y-%m-%d")
                if d <= cutoff_date:
                    filtered.append(r)
            if filtered:
                truncated[code] = filtered
        return truncated

    # ── 主循环 ────────────────────────────────────────

    def run(self) -> dict:
        """执行回测，返回汇总结果。"""
        from indicators import compute_indicators
        from scorer import select_buy_candidates, select_sell_candidates
        from holdings import analyze_holdings

        total = len(self._trading_dates)
        from datetime import date as DateType

        for idx, today in enumerate(self._trading_dates):
            if idx % 50 == 0:
                logger.info(
                    "  回测进度: %d/%d (%.0f%%)",
                    idx, total, idx / total * 100,
                )

            # 1. 截断数据
            truncated_nav = self._truncate_nav(today)

            # 2. 当前持仓代码
            my_codes = list(self.portfolio.positions.keys())

            # 3. 计算指标
            equity_codes = {
                code for code, ftype in self._code_type.items()
                if ftype in ("股票型", "混合型", "指数型", "QDII")
            }
            indicators = compute_indicators(
                truncated_nav, self.strategy, equity_codes,
                set(my_codes),
            )

            # 4. 生成信号
            rules_cfg = self.strategy.get("rules")
            if my_codes:
                sell_candidates = select_sell_candidates(
                    indicators, holding_codes=my_codes, rules=rules_cfg,
                )
            else:
                sell_candidates = []

            buy_candidates = select_buy_candidates(indicators, rules=rules_cfg)
            # 过滤掉已持仓的
            buy_candidates = [
                c for c in buy_candidates
                if c["code"] not in my_codes
            ]

            # 5. 执行交易
            self._execute_sells(sell_candidates, indicators, today)
            self._execute_buys(buy_candidates, today)

            # 6. 快照
            nav_snapshot = {}
            for code, records in truncated_nav.items():
                if records:
                    nav_snapshot[code] = records[-1]["nav"]
            self.portfolio.snapshot(today, nav_snapshot)

        logger.info("  回测完成")
        return self._build_result()

    # ── 卖出执行 ──────────────────────────────────────

    def _execute_sells(
        self, sell_candidates: list[dict],
        indicators: dict, today: datetime,
    ):
        """执行卖出：按 severity 排序，逐个执行。"""
        # 按严重程度排序
        sell_candidates.sort(key=lambda x: x.get("severity", 0), reverse=True)

        for candidate in sell_candidates:
            code = candidate["code"]
            if code not in self.portfolio.positions:
                continue

            sig = indicators.get(code, {})
            name = self._code_name.get(code, code)
            ftype = self._code_type.get(code, "混合型")
            reason = candidate.get("reason", "")

            # 确定卖出比例
            # 使用 holdings.classify_holding 判断动作
            from holdings import analyze_holdings
            rules_cfg = self.strategy.get("rules")
            advice = analyze_holdings([code], indicators, self.strategy, rules=rules_cfg)
            if not advice:
                continue

            action = advice[0].get("action", "持有")
            if action == "清仓":
                sell_units = 1.0   # 1.0 = 全部
            elif action == "减仓":
                sell_units = 0.5   # 50%
            else:
                continue  # 持有/加仓 → 不卖

            nav = sig.get("current_nav", 0)
            if nav <= 0:
                continue

            trade = self.portfolio.sell(
                date=today, code=code, name=name,
                nav=nav, units=sell_units, fund_type=ftype,
                reason=reason,
            )

            # 记录规则统计
            if trade:
                for rule_token in reason.split(" | "):
                    rule_id = rule_token.split(" ")[0] if rule_token else ""
                    if rule_id:
                        self.rule_stats[rule_id]["triggers"] += 1
                        # 盈亏在卖出时无法立即判断，在 _build_result 时统一计算

    # ── 买入执行 ──────────────────────────────────────

    def _execute_buys(
        self, buy_candidates: list[dict], today: datetime,
    ):
        """执行买入：取 Top N，等权重分配。"""
        # 按反弹预期排序
        buy_candidates.sort(key=lambda x: x.get("rebound", 0), reverse=True)

        # 当前可用仓位
        slots = MAX_HOLDINGS - self.portfolio.position_count()
        if slots <= 0:
            return

        # 可用现金的 80% 用于买入
        available = self.portfolio.cash * 0.8
        if available < 1000:  # 至少 1000 元
            return

        candidates = buy_candidates[:slots]
        if not candidates:
            return

        # 等权重分配
        per_fund = available / len(candidates)
        total_value = self.portfolio.total_value({}) or self.initial_capital
        per_fund = min(per_fund, total_value * MAX_SINGLE_POSITION_PCT)

        for candidate in candidates:
            code = candidate["code"]
            nav = candidate.get("nav", 0)
            if nav <= 0 or per_fund < 500:  # 最小 500 元
                continue

            name = self._code_name.get(code, code)
            ftype = self._code_type.get(code, "混合型")
            reason = candidate.get("reason", "")

            trade = self.portfolio.buy(
                date=today, code=code, name=name,
                nav=nav, amount=per_fund, fund_type=ftype,
                reason=reason,
            )

            if trade:
                for rule_token in reason.split(" | "):
                    rule_id = rule_token.split(" ")[0] if rule_token else ""
                    if rule_id:
                        self.rule_stats[rule_id]["triggers"] += 1

    # ── 结果 ──────────────────────────────────────────

    def _build_result(self) -> dict:
        """构建回测结果字典。"""
        stats = self.portfolio.stats()

        # 计算规则胜率
        # 买入规则胜率 = 买入后 20 日涨幅 > 0
        # 卖出规则胜率 = 卖出后 20 日下跌（避免了进一步亏损）
        LOOKAHEAD = 20  # 前瞻窗口（交易日）

        # 收集所有被规则触发的交易，按规则分组
        rule_buy_trades: dict[str, list[Trade]] = defaultdict(list)
        rule_sell_trades: dict[str, list[Trade]] = defaultdict(list)

        for trade in self.portfolio.trade_log:
            for rule_token in trade.reason.split(" | "):
                rule_id = rule_token.split(" ")[0] if rule_token else ""
                if not rule_id:
                    continue
                if trade.action == "买入":
                    rule_buy_trades[rule_id].append(trade)
                elif trade.action == "卖出":
                    rule_sell_trades[rule_id].append(trade)

        # 卖出规则：看卖出后 LOOKAHEAD 天 NAV 是否下跌
        for rule_id, trades in rule_sell_trades.items():
            self.rule_stats[rule_id]["triggers"] = len(trades)
            for trade in trades:
                is_win = self._post_sell_declined(trade, LOOKAHEAD)
                if is_win:
                    self.rule_stats[rule_id]["wins"] += 1

        # 买入规则：看买入后 LOOKAHEAD 天 NAV 是否上涨
        for rule_id, trades in rule_buy_trades.items():
            # triggers 在 _execute_buys 中已统计，这里只算胜率
            for trade in trades:
                is_win = self._post_buy_rose(trade, LOOKAHEAD)
                if is_win:
                    self.rule_stats[rule_id]["wins"] += 1

        # 规则有效性
        rule_effectiveness = {}
        for rule_id, data in self.rule_stats.items():
            total = data["triggers"]
            wins = data["wins"]
            rule_effectiveness[rule_id] = {
                "triggers": total,
                "wins": wins,
                "win_rate": wins / total if total > 0 else 0.0,
            }

        # 交易过的基金表现（只展示策略实际交易的基金）
        traded_codes: set[str] = set()
        for trade in self.portfolio.trade_log:
            traded_codes.add(trade.code)

        fund_returns = {}
        for code in traded_codes:
            records = self.nav_data.get(code, [])
            if len(records) >= 2:
                start_nav = records[0]["nav"]
                end_nav = records[-1]["nav"]
                if start_nav > 0:
                    fund_returns[code] = end_nav / start_nav - 1

        top_funds = sorted(
            fund_returns.items(), key=lambda x: x[1], reverse=True
        )[:5]
        bottom_funds = sorted(
            fund_returns.items(), key=lambda x: x[1]
        )[:5]

        return {
            "stats": stats,
            "rule_effectiveness": rule_effectiveness,
            "top_performers": [
                {"code": c, "name": self._code_name.get(c, c), "return": r}
                for c, r in top_funds
            ],
            "worst_performers": [
                {"code": c, "name": self._code_name.get(c, c), "return": r}
                for c, r in bottom_funds
            ],
            "trade_log": self.portfolio.trade_log,
            "snapshots": self.portfolio.snapshots,
            "errors": self.errors,
            "initial_capital": self.initial_capital,
        }

    def _post_sell_declined(self, trade: Trade, lookahead: int) -> bool:
        """卖出后 lookahead 日内基金是否下跌（卖出正确）。"""
        records = self.nav_data.get(trade.code, [])
        sell_nav = trade.nav
        # 找到卖出日之后的数据点
        future_navs = []
        for r in records:
            d = r["date"]
            if isinstance(d, str):
                d = datetime.strptime(d[:10], "%Y-%m-%d")
            if d > trade.date:
                future_navs.append(r["nav"])

        if len(future_navs) < lookahead:
            # 数据不足，用最近可用数据
            if future_navs:
                return future_navs[-1] < sell_nav
            return False

        # 取 lookahead 天后的净值
        future_nav = future_navs[min(lookahead - 1, len(future_navs) - 1)]
        return future_nav < sell_nav

    def _post_buy_rose(self, trade: Trade, lookahead: int) -> bool:
        """买入后 lookahead 日内基金是否上涨（买入正确）。"""
        records = self.nav_data.get(trade.code, [])
        buy_nav = trade.nav
        future_navs = []
        for r in records:
            d = r["date"]
            if isinstance(d, str):
                d = datetime.strptime(d[:10], "%Y-%m-%d")
            if d > trade.date:
                future_navs.append(r["nav"])

        if len(future_navs) < lookahead:
            if future_navs:
                return future_navs[-1] > buy_nav
            return False

        future_nav = future_navs[min(lookahead - 1, len(future_navs) - 1)]
        return future_nav > buy_nav