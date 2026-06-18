import numpy as np

# 单只基金最大仓位占总资金比例
MAX_SINGLE_POSITION = 0.05
# 总仓位上限
MAX_TOTAL_POSITION = 0.80
# 最少需要的历史数据天数
MIN_HISTORY_DAYS = 60


def compute_position_advice(indicators, nav_data, buy_top10, sell_top10,
                            holdings_advice, target_vol=0.10):
    """对买入候选和持仓基金计算精确的买卖比例。

    买入比例: Kelly + volatility adjustment + drawdown scaling (纯统计)
    卖出比例: Vol Targeting (Moreira & Muir 2017) + DD Constraint (Grossman & Zhou 1993)
    """
    buy_positions = []
    for item in buy_top10:
        code = item["code"]
        if code not in nav_data or code not in indicators:
            buy_positions.append({
                **item, "action": "买入", "pct": 0,
                "reason_detail": "数据不足",
            })
            continue

        records = nav_data[code]
        stats = _compute_fund_stats(records)
        if stats is None:
            buy_positions.append({
                **item, "action": "买入", "pct": 0,
                "reason_detail": "历史数据不足",
            })
            continue

        kelly_pct = _half_kelly(stats["win_rate"], stats["payoff_ratio"])
        vol_adjusted = _volatility_adjust(kelly_pct, stats["annual_vol"], target_vol)
        dd_scale = _drawdown_scale(stats["max_drawdown"])
        raw_pct = vol_adjusted * dd_scale
        final_pct = min(raw_pct, MAX_SINGLE_POSITION)
        final_pct = max(final_pct, 0.005)

        reason_parts = [
            f"Kelly {kelly_pct:.1%}",
            f"vol-adj {vol_adjusted:.1%}",
        ]

        buy_positions.append({
            **item,
            "action": "买入",
            "pct": round(final_pct, 4),
            "reason_detail": " | ".join(reason_parts),
            "stats": stats,
        })

    # 买入总仓位约束（不超过 40%）
    total_buy = sum(p["pct"] for p in buy_positions)
    if total_buy > MAX_TOTAL_POSITION * 0.5:
        scale = (MAX_TOTAL_POSITION * 0.5) / total_buy
        for p in buy_positions:
            p["pct"] = round(p["pct"] * scale, 4)

    hold_positions = []
    for item in holdings_advice:
        code = item["code"]
        if code not in nav_data or code not in indicators:
            hold_positions.append({
                **item, "pct": 0,
                "reason_detail": "数据不足，无法计算比例",
            })
            continue

        records = nav_data[code]
        sig = indicators[code]
        stats = _compute_fund_stats(records)
        if stats is None:
            hold_positions.append({
                **item, "pct": 0,
                "reason_detail": "历史数据不足",
            })
            continue

        action, pct, detail = _decide_holding_action(
            item["action"], sig, stats, target_vol
        )

        hold_positions.append({
            **item,
            "action": action,
            "pct": round(pct, 4),
            "reason_detail": detail,
            "stats": stats,
        })

    # 加仓总额约束（不超过 20%）
    total_add = sum(p["pct"] for p in hold_positions if p["action"] == "加仓")
    if total_add > 0.20:
        scale = 0.20 / total_add
        for p in hold_positions:
            if p["action"] == "加仓":
                p["pct"] = round(p["pct"] * scale, 4)

    return buy_positions, hold_positions


def _compute_fund_stats(records):
    if len(records) < MIN_HISTORY_DAYS:
        return None

    navs = np.array([r["nav"] for r in records])
    daily_returns = np.diff(navs) / navs[:-1]

    if len(daily_returns) < MIN_HISTORY_DAYS:
        return None

    positive_returns = daily_returns[daily_returns > 0]
    negative_returns = daily_returns[daily_returns < 0]

    if len(positive_returns) == 0:
        avg_win = 0.001
    else:
        avg_win = np.mean(positive_returns)

    if len(negative_returns) == 0:
        avg_loss = max(0.001, np.std(daily_returns) * 0.5)
    else:
        avg_loss = np.mean(np.abs(negative_returns))

    win_rate = len(positive_returns) / len(daily_returns) if len(daily_returns) > 0 else 0.5
    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0

    annual_vol = np.std(daily_returns) * np.sqrt(244)

    cummax = np.maximum.accumulate(navs)
    drawdowns = (cummax - navs) / cummax
    max_drawdown = np.max(drawdowns)

    annual_return = (navs[-1] / navs[0]) ** (244 / len(navs)) - 1
    sharpe = (annual_return - 0.02) / annual_vol if annual_vol > 0 else 0

    recent_vol = (
        np.std(daily_returns[-20:]) * np.sqrt(244)
        if len(daily_returns) >= 20 else annual_vol
    )

    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    return {
        "win_rate": win_rate,
        "payoff_ratio": payoff_ratio,
        "annual_vol": annual_vol,
        "recent_vol": recent_vol,
        "max_drawdown": max_drawdown,
        "annual_return": annual_return,
        "sharpe": sharpe,
        "calmar": calmar,
        "daily_returns": daily_returns,
    }


def _half_kelly(win_rate, payoff_ratio):
    """Half-Kelly: f* = (bp - q) / b / 2"""
    b = payoff_ratio
    p = win_rate
    q = 1 - p
    kelly = max(0, (b * p - q) / b)
    return min(kelly * 0.5, MAX_SINGLE_POSITION)


def _volatility_adjust(base_pct, annual_vol, target_vol):
    """Inverse volatility weighting: lower vol → larger position."""
    if annual_vol <= 0:
        return base_pct
    vol_scale = target_vol / annual_vol
    vol_scale = np.clip(vol_scale, 0.3, 2.0)
    return base_pct * vol_scale


def _drawdown_scale(current_drawdown):
    """Linear drawdown scale: deeper DD → smaller position."""
    if current_drawdown <= 0:
        return 1.0
    return max(0.0, 1.0 - current_drawdown / 0.10)


def _calc_sell_pct(sig, stats, target_vol):
    """Calculate sell percentage using theoretically-grounded methods.

    Scenarios (take the maximum):
      A) Stop-loss:        above_ma200 == False → liquidate 100%
      B) Risk reduction:   max(vol target, DD constraint)
      C) Take-profit:      vol target on recent (20d) volatility
    """
    reasons = []

    # A: Stop-loss — trend break invalidates buy thesis
    if not sig["above_ma200"]:
        return 1.0, "Trend broken, full liquidation"

    # B: Risk reduction
    annual_vol = stats["annual_vol"]
    sell_vol = max(0.0, 1.0 - target_vol / annual_vol) if annual_vol > 0 else 0.0

    pullback = sig["pullback_from_peak"]
    dd_trigger = 0.05
    max_dd = 0.10
    if pullback > dd_trigger:
        sell_dd = min(1.0, (pullback - dd_trigger) / (max_dd - dd_trigger))
    else:
        sell_dd = 0.0

    risk_sell = max(sell_vol, sell_dd)

    # C: Take-profit — recent volatility spike
    recent_vol = stats["recent_vol"]
    sell_profit = max(0.0, 1.0 - target_vol / recent_vol) if recent_vol > 0 else 0.0

    # --- build reason ---
    if sell_vol > 0:
        reasons.append(
            f"Vol target: {annual_vol:.1%} > {target_vol:.0%} "
            f"(cut {sell_vol:.0%})"
        )
    if sell_dd > 0:
        reasons.append(
            f"DD constraint: {pullback:.1%} drawdown "
            f"(cut {sell_dd:.0%})"
        )
    if sell_profit > 0 and sell_profit > risk_sell:
        reasons.append(
            f"Take-profit: recent vol {recent_vol:.1%} "
            f"(cut {sell_profit:.0%})"
        )

    sell_pct = max(risk_sell, sell_profit)
    return round(min(sell_pct, 1.0), 4), " | ".join(reasons) if reasons else "No sell signal"


def _decide_holding_action(action, sig, stats, target_vol):
    """Decide specific operation and proportion for a holding.

    Args:
        action:   from holdings.py/classify_holding (加仓/减仓/清仓/持有)
        sig:      indicator dict for this fund
        stats:    historical statistics
        target_vol: target annualized volatility

    Returns: (action, pct, reason_detail)
    """
    if action == "清仓":
        return "清仓", 1.0, "Trend broken, stop-loss liquidate"

    if action == "减仓":
        sell_pct, reason = _calc_sell_pct(sig, stats, target_vol)
        return "减仓", sell_pct, reason

    if action == "加仓":
        kelly_pct = _half_kelly(stats["win_rate"], stats["payoff_ratio"])
        vol_adjusted = _volatility_adjust(kelly_pct, stats["annual_vol"], target_vol)
        dd_scale = _drawdown_scale(stats["max_drawdown"])
        pct = vol_adjusted * dd_scale
        pct = min(pct, MAX_SINGLE_POSITION * 0.5)
        pct = max(pct, 0.005)
        reason = (
            f"Kelly {kelly_pct:.1%} | "
            f"vol-adj {vol_adjusted:.1%} | "
            f"DD-scale {dd_scale:.2f}"
        )
        return "加仓", pct, reason

    # 持有
    reason = (
        f"Sharpe {stats['sharpe']:.2f} | "
        f"vol {stats['annual_vol']:.1%} | "
        f"maxDD {stats['max_drawdown']:.1%}"
    )
    return "持有", 0, reason