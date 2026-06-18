from datetime import datetime

import numpy as np

MIN_RECORDS = 100
# 波动率异常标志值（历史波动率为零时使用）
VOLATILITY_RATIO_ABNORMAL = float("inf")


def _is_anomalous(records, is_equity=False):
    """检测异常基金数据：停牌、数据错误、异常波动等"""
    navs = [r["nav"] for r in records]

    if is_equity:
        single_day_limit = 0.05
        extreme_day_limit = 0.04
        window_limit = 0.20
    else:
        single_day_limit = 0.035
        extreme_day_limit = 0.03
        window_limit = 0.15

    tail = navs[-15:]
    if len(set(tail)) == 1:
        return True

    if any(n <= 0 for n in navs[-30:]):
        return True

    recent_navs = navs[-60:] if len(navs) >= 60 else navs

    daily_changes = []
    for i in range(1, len(recent_navs)):
        if recent_navs[i - 1] > 0:
            daily_changes.append(abs(recent_navs[i] - recent_navs[i - 1]) / recent_navs[i - 1])

    if daily_changes:
        if max(daily_changes) > single_day_limit:
            return True
        extreme_days = sum(1 for c in daily_changes if c > extreme_day_limit)
        if extreme_days >= 5:
            return True

    if len(recent_navs) >= 6:
        for i in range(5, len(recent_navs)):
            if recent_navs[i - 5] > 0:
                window_change = abs(recent_navs[i] - recent_navs[i - 5]) / recent_navs[i - 5]
                if window_change > window_limit:
                    return True

    recent = records[-30:]
    if len(recent) >= 30:
        first_date = datetime.strptime(recent[0]["date"], "%Y-%m-%d")
        last_date = datetime.strptime(recent[-1]["date"], "%Y-%m-%d")
        if (last_date - first_date).days > 60:
            return True

    return False


def _rsi(navs, period=14):
    """RSI — 相对强弱指标 (Wilder, 1978)。"""
    if len(navs) < period + 1:
        return 50.0
    deltas = np.diff(navs[-period - 1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _rolling_sharpe(navs, period=60, rf=0.02):
    """滚动年化夏普比率 (Sharpe, 1966)。"""
    if len(navs) < period + 1:
        return 0.0
    returns = np.diff(navs[-period - 1:]) / navs[-period - 1:-1]
    if len(returns) < 2 or np.std(returns) == 0:
        return 0.0
    annual_return = (navs[-1] / navs[-period - 1]) ** (244 / len(returns)) - 1
    annual_vol = np.std(returns, ddof=1) * np.sqrt(244)
    if annual_vol == 0:
        return 0.0
    return round((annual_return - rf) / annual_vol, 2)


def _rolling_max_drawdown(navs, period=60):
    """滚动最大回撤 (近 period 天)。"""
    if len(navs) < period:
        return 0.0
    window = navs[-period:]
    cummax = np.maximum.accumulate(window)
    drawdowns = (cummax - window) / cummax
    return round(np.max(drawdowns), 4)


# ── 主函数 ──────────────────────────────────────────────

def compute_indicators(nav_data, strategy_config, equity_codes=None, holding_codes=None):
    ma_period = strategy_config["ma_period"]
    vol_window = strategy_config["volatility_window"]
    vol_max_ratio = strategy_config["volatility_max_ratio"]

    indicators = {}

    if equity_codes is None:
        equity_codes = set()
    if holding_codes is None:
        holding_codes = set()
    anomaly_count = 0

    for code, records in nav_data.items():
        if len(records) < MIN_RECORDS:
            continue

        if code not in holding_codes:
            if _is_anomalous(records, is_equity=(code in equity_codes)):
                anomaly_count += 1
                continue

        navs = np.array([r["nav"] for r in records])
        dates = [r["date"] for r in records]

        actual_ma_period = min(ma_period, len(navs))
        ma = np.mean(navs[-actual_ma_period:])
        current_nav = navs[-1]

        if len(navs) < vol_window + 60:
            continue

        recent_returns = np.diff(navs[-vol_window - 1:]) / navs[-vol_window - 1:-1]
        hist_returns = np.diff(navs[-61:]) / navs[-61:-1]
        recent_vol = np.std(recent_returns)
        hist_vol = np.std(hist_returns)
        volatility_ratio = recent_vol / hist_vol if hist_vol > 0 else VOLATILITY_RATIO_ABNORMAL

        consecutive_declines = 0
        for i in range(len(navs) - 1, 0, -1):
            if navs[i] < navs[i - 1]:
                consecutive_declines += 1
            else:
                break

        consecutive_rises = 0
        for i in range(len(navs) - 1, 0, -1):
            if navs[i] > navs[i - 1]:
                consecutive_rises += 1
            else:
                break

        recent_decline = 0.0
        if consecutive_declines > 0:
            start_nav = navs[-(consecutive_declines + 1)]
            recent_decline = (current_nav - start_nav) / start_nav

        peak_20d = np.max(navs[-20:]) if len(navs) >= 20 else current_nav
        pullback_from_peak = (peak_20d - current_nav) / peak_20d if peak_20d > 0 else 0.0

        month_ago_nav = navs[-22] if len(navs) >= 22 else navs[0]
        monthly_return = (current_nav - month_ago_nav) / month_ago_nav

        single_day_change = 0.0
        if len(navs) >= 2:
            single_day_change = (navs[-1] - navs[-2]) / navs[-2]

        trend_strength = (current_nav - ma) / ma

        # 三项经典指标
        rsi_14 = _rsi(navs, 14)
        rolling_sharpe = _rolling_sharpe(navs)
        rolling_max_dd = _rolling_max_drawdown(navs)

        indicators[code] = {
            "current_nav": current_nav,
            "last_date": dates[-1],

            # 趋势
            "ma200": ma,
            "trend_strength": trend_strength,
            "above_ma200": current_nav > ma,

            # 回调
            "consecutive_declines": consecutive_declines,
            "consecutive_rises": consecutive_rises,
            "recent_decline": recent_decline,
            "pullback_from_peak": pullback_from_peak,

            # 收益
            "monthly_return": monthly_return,
            "single_day_change": single_day_change,

            # 波动/风险
            "volatility_ratio": volatility_ratio,
            "rolling_max_drawdown": rolling_max_dd,

            # 经典指标
            "rsi_14": rsi_14,
            "rolling_sharpe": rolling_sharpe,
        }

    if anomaly_count > 0:
        print(f"  排除异常基金: {anomaly_count} 只")

    return indicators