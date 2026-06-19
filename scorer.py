# fund-metrics rule library
#
# Buy rules (match any to enter candidate pool):
#   P1 Golden Pullback:   above MA200, decline 3-5d, RSI < 40
#   P2 Oversold Bounce:   above MA200, RSI < 30, 60d DD > 8%
#   P3 Trend Pullback:    above MA200, decline 3-5d, Sharpe > 0.5
#   P4 Low-Vol Dip:       above MA200, RSI < 35, vol < historical
#   P5 Deep Value:        above MA200, 60d DD > 12%, RSI < 35
#   P6 Quality Dip:       above MA200, Sharpe > 1.0, decline 2-5d
#   P7 Vol Contraction:   above MA200, vol < 0.6x hist, pullback > 3%
#   P8 Strong Trend Dip:  above MA200, trend > 5%, decline >= 2d
#
# Sell rules (match any to trigger):
#   S1 Trend Break:       below MA200
#   S2 Acceleration:      60d DD > 10%, vol > 1.5x hist
#   S3 Overbought:        rise >= 5d, RSI > 70
#   S4 Risk Decay:        Sharpe < 0, monthly < -5%
#   S5 Peak Retreat:      above MA200, pullback > 5%, decline >= 4d
#   S6 Quality Collapse:  Sharpe < -1.0
#   S7 Vol Explosion:     vol > 2.0x hist
#   S8 Slow Bleed:        above MA200, decline >= 6d
#
# 所有规则阈值可从 config.yaml 的 strategy.rules 段覆盖。
# 传入 rules=None 时使用下方的默认值（向后兼容）。


# ── 默认规则参数 ──────────────────────────────────────

_DEFAULT_BUY_RULES = {
    "P1_golden_pullback":    {"rsi_max": 40, "decline_min": 3, "decline_max": 5},
    "P2_oversold_bounce":    {"rsi_max": 30, "max_drawdown_min": 0.08},
    "P3_trend_pullback":     {"decline_min": 3, "decline_max": 5, "sharpe_min": 0.5},
    "P4_low_vol_dip":        {"rsi_max": 35, "vol_ratio_max": 1.0},
    "P5_deep_value":         {"max_drawdown_min": 0.12, "rsi_max": 35},
    "P6_quality_dip":        {"sharpe_min": 1.0, "decline_min": 2, "decline_max": 5},
    "P7_vol_contraction":    {"vol_ratio_max": 0.6, "pullback_min": 0.03},
    "P8_strong_trend_dip":   {"trend_strength_min": 0.05, "decline_min": 2},
}

_DEFAULT_SELL_RULES = {
    "S1_trend_break":        {"below_ma_clear": True},
    "S2_accelerating":       {"max_drawdown_min": 0.10, "vol_ratio_min": 1.5},
    "S3_overbought":         {"rise_min": 5, "rsi_min": 70},
    "S4_risk_decay":         {"sharpe_max": 0.0, "monthly_return_max": -0.05},
    "S5_peak_retreat":       {"pullback_min": 0.05, "decline_min": 4},
    "S6_quality_collapse":   {"sharpe_max": -1.0},
    "S7_vol_explosion":      {"vol_ratio_min": 2.0},
    "S8_slow_bleed":         {"decline_min": 6},
}


def _get_rules(user_rules, section, rule_id):
    """获取某条规则的参数，优先用用户配置，回退到默认值。"""
    if user_rules:
        rule = user_rules.get(section, {}).get(rule_id, {})
        if rule:
            return rule
    defaults = (
        _DEFAULT_BUY_RULES if section == "buy" else _DEFAULT_SELL_RULES
    )
    return defaults.get(rule_id, {})


# ── 买入候选 ─────────────────────────────────────────

def select_buy_candidates(indicators, rules=None):
    """Rule-based buy candidate selection, ranked by expected rebound."""
    candidates = []

    for code, sig in indicators.items():
        reason = None

        # P1: Golden Pullback
        r = _get_rules(rules, "buy", "P1_golden_pullback")
        if (sig["above_ma200"]
                and r["decline_min"] <= sig["consecutive_declines"] <= r["decline_max"]
                and sig["rsi_14"] < r["rsi_max"]):
            reason = f"P1 Golden pullback ({sig['consecutive_declines']}d, RSI={sig['rsi_14']:.0f})"

        # P2: Oversold Bounce
        elif not reason:
            r = _get_rules(rules, "buy", "P2_oversold_bounce")
            if (sig["above_ma200"]
                    and sig["rsi_14"] < r["rsi_max"]
                    and sig["rolling_max_drawdown"] > r["max_drawdown_min"]):
                reason = f"P2 Oversold bounce (RSI={sig['rsi_14']:.0f}, DD={sig['rolling_max_drawdown']:.1%})"

        # P3: Trend Pullback
        elif not reason:
            r = _get_rules(rules, "buy", "P3_trend_pullback")
            if (sig["above_ma200"]
                    and r["decline_min"] <= sig["consecutive_declines"] <= r["decline_max"]
                    and sig["rolling_sharpe"] > r["sharpe_min"]):
                reason = f"P3 Trend pullback ({sig['consecutive_declines']}d, Sharpe={sig['rolling_sharpe']:.1f})"

        # P4: Low-Vol Dip
        elif not reason:
            r = _get_rules(rules, "buy", "P4_low_vol_dip")
            if (sig["above_ma200"]
                    and sig["rsi_14"] < r["rsi_max"]
                    and sig["volatility_ratio"] < r["vol_ratio_max"]):
                reason = f"P4 Low-vol dip (RSI={sig['rsi_14']:.0f}, vol<hist)"

        # P5: Deep Value
        elif not reason:
            r = _get_rules(rules, "buy", "P5_deep_value")
            if (sig["above_ma200"]
                    and sig["rolling_max_drawdown"] > r["max_drawdown_min"]
                    and sig["rsi_14"] < r["rsi_max"]):
                reason = f"P5 Deep value (DD={sig['rolling_max_drawdown']:.1%}, RSI={sig['rsi_14']:.0f})"

        # P6: Quality Dip
        elif not reason:
            r = _get_rules(rules, "buy", "P6_quality_dip")
            if (sig["above_ma200"]
                    and sig["rolling_sharpe"] > r["sharpe_min"]
                    and r["decline_min"] <= sig["consecutive_declines"] <= r["decline_max"]):
                reason = f"P6 Quality dip ({sig['consecutive_declines']}d, Sharpe={sig['rolling_sharpe']:.1f})"

        # P7: Vol Contraction
        elif not reason:
            r = _get_rules(rules, "buy", "P7_vol_contraction")
            if (sig["above_ma200"]
                    and sig["volatility_ratio"] < r["vol_ratio_max"]
                    and sig["pullback_from_peak"] > r["pullback_min"]):
                reason = f"P7 Vol contraction (vol {sig['volatility_ratio']:.2f}x, pullback {sig['pullback_from_peak']:.1%})"

        # P8: Strong Trend Dip
        elif not reason:
            r = _get_rules(rules, "buy", "P8_strong_trend_dip")
            if (sig["above_ma200"]
                    and sig["trend_strength"] > r["trend_strength_min"]
                    and sig["consecutive_declines"] >= r["decline_min"]):
                reason = f"P8 Strong trend dip ({sig['consecutive_declines']}d, trend +{sig['trend_strength']:.1%})"

        if reason:
            vol = max(sig["volatility_ratio"], 0.01)
            rebound = abs(sig["recent_decline"]) * sig["trend_strength"] / vol
            candidates.append({
                "code": code,
                "reason": reason,
                "nav": sig["current_nav"],
                "trend_strength": sig["trend_strength"],
                "rebound": rebound,
            })

    candidates.sort(key=lambda x: x["rebound"], reverse=True)
    return candidates[:10]


# ── 卖出候选 ─────────────────────────────────────────

def select_sell_candidates(indicators, holding_codes=None, rules=None):
    """Rule-based sell candidate selection, ranked by severity."""
    candidates = []
    codes = holding_codes if holding_codes else list(indicators.keys())

    for code in codes:
        if code not in indicators:
            continue
        sig = indicators[code]
        triggers = []

        # S1: Trend Break
        r = _get_rules(rules, "sell", "S1_trend_break")
        if not sig["above_ma200"]:
            triggers.append(f"S1 Trend break (bias {sig['trend_strength']:.1%})")

        # S2: Accelerating Deterioration
        r = _get_rules(rules, "sell", "S2_accelerating")
        if sig["rolling_max_drawdown"] > r["max_drawdown_min"] and sig["volatility_ratio"] > r["vol_ratio_min"]:
            triggers.append(
                f"S2 Accelerating (DD {sig['rolling_max_drawdown']:.1%}, "
                f"vol {sig['volatility_ratio']:.1f}x)"
            )

        # S3: Overbought
        r = _get_rules(rules, "sell", "S3_overbought")
        if sig["consecutive_rises"] >= r["rise_min"] and sig["rsi_14"] > r["rsi_min"]:
            triggers.append(
                f"S3 Overbought ({sig['consecutive_rises']}d, RSI={sig['rsi_14']:.0f})"
            )

        # S4: Risk Deterioration
        r = _get_rules(rules, "sell", "S4_risk_decay")
        if sig["rolling_sharpe"] < r["sharpe_max"] and sig["monthly_return"] < r["monthly_return_max"]:
            triggers.append(
                f"S4 Risk decay (Sharpe {sig['rolling_sharpe']:.1f}, "
                f"monthly {sig['monthly_return']:.1%})"
            )

        # S5: Peak Retreat
        r = _get_rules(rules, "sell", "S5_peak_retreat")
        if (sig["above_ma200"]
                and sig["pullback_from_peak"] > r["pullback_min"]
                and sig["consecutive_declines"] >= r["decline_min"]):
            triggers.append(
                f"S5 Peak retreat (pullback {sig['pullback_from_peak']:.1%}, "
                f"{sig['consecutive_declines']}d decline)"
            )

        # S6: Quality Collapse
        r = _get_rules(rules, "sell", "S6_quality_collapse")
        if sig["rolling_sharpe"] < r["sharpe_max"]:
            triggers.append(
                f"S6 Quality collapse (Sharpe {sig['rolling_sharpe']:.1f})"
            )

        # S7: Vol Explosion
        r = _get_rules(rules, "sell", "S7_vol_explosion")
        if sig["volatility_ratio"] > r["vol_ratio_min"]:
            triggers.append(
                f"S7 Vol explosion ({sig['volatility_ratio']:.1f}x hist)"
            )

        # S8: Slow Bleed
        r = _get_rules(rules, "sell", "S8_slow_bleed")
        if sig["above_ma200"] and sig["consecutive_declines"] >= r["decline_min"]:
            triggers.append(
                f"S8 Slow bleed ({sig['consecutive_declines']}d decline, "
                f"still above MA200)"
            )

        if triggers:
            severity = _sell_severity(sig)
            candidates.append({
                "code": code,
                "reason": " | ".join(triggers),
                "nav": sig["current_nav"],
                "severity": severity,
            })

    candidates.sort(key=lambda x: x["severity"], reverse=True)
    return candidates[:10]


def _sell_severity(sig):
    """Sell severity (0-1): max of four normalized risk dimensions."""
    trend = max(0.0, -sig["trend_strength"]) / 0.05 if not sig["above_ma200"] else 0.0
    drawdown = sig["rolling_max_drawdown"] / 0.15
    vol = max(0.0, sig["volatility_ratio"] - 1.0) / 1.0
    rsi = max(0.0, sig["rsi_14"] - 60) / 40

    return round(min(1.0, max(trend, drawdown, vol, rsi)), 4)


# ── 持仓分类 ─────────────────────────────────────────

def classify_holding(sig, rules=None):
    """Classify a single holding using rules.  Returns (action, reason)."""
    # ── Sell rules ──────────────────────────
    if not sig["above_ma200"]:
        return "清仓", f"S1 Trend break (bias {sig['trend_strength']:.1%})"

    r = _get_rules(rules, "sell", "S2_accelerating")
    if sig["rolling_max_drawdown"] > r["max_drawdown_min"] and sig["volatility_ratio"] > r["vol_ratio_min"]:
        return "减仓", f"S2 Accelerating (DD {sig['rolling_max_drawdown']:.1%})"

    r = _get_rules(rules, "sell", "S3_overbought")
    if sig["consecutive_rises"] >= r["rise_min"] and sig["rsi_14"] > r["rsi_min"]:
        return "减仓", f"S3 Overbought ({sig['consecutive_rises']}d, RSI={sig['rsi_14']:.0f})"

    r = _get_rules(rules, "sell", "S4_risk_decay")
    if sig["rolling_sharpe"] < r["sharpe_max"] and sig["monthly_return"] < r["monthly_return_max"]:
        return "减仓", f"S4 Risk decay (Sharpe {sig['rolling_sharpe']:.1f})"

    r = _get_rules(rules, "sell", "S5_peak_retreat")
    if (sig["above_ma200"]
            and sig["pullback_from_peak"] > r["pullback_min"]
            and sig["consecutive_declines"] >= r["decline_min"]):
        return "减仓", f"S5 Peak retreat (pullback {sig['pullback_from_peak']:.1%})"

    r = _get_rules(rules, "sell", "S6_quality_collapse")
    if sig["rolling_sharpe"] < r["sharpe_max"]:
        return "减仓", f"S6 Quality collapse (Sharpe {sig['rolling_sharpe']:.1f})"

    r = _get_rules(rules, "sell", "S7_vol_explosion")
    if sig["volatility_ratio"] > r["vol_ratio_min"]:
        return "减仓", f"S7 Vol explosion ({sig['volatility_ratio']:.1f}x)"

    r = _get_rules(rules, "sell", "S8_slow_bleed")
    if sig["above_ma200"] and sig["consecutive_declines"] >= r["decline_min"]:
        return "减仓", f"S8 Slow bleed ({sig['consecutive_declines']}d decline)"

    # ── Buy rule ───────────────────────────
    r = _get_rules(rules, "buy", "P1_golden_pullback")
    if (sig["above_ma200"]
            and r["decline_min"] <= sig["consecutive_declines"] <= r["decline_max"]
            and sig["rsi_14"] < r["rsi_max"]):
        return "加仓", f"P1 Golden pullback ({sig['consecutive_declines']}d, RSI={sig['rsi_14']:.0f})"

    # ── Hold ───────────────────────────────
    if sig["above_ma200"]:
        return "持有", f"Uptrend (+{sig['trend_strength']:.1%} vs MA200)"
    else:
        return "持有", "Weak trend, monitoring"