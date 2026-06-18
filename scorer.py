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


def select_buy_candidates(indicators):
    """Rule-based buy candidate selection, ranked by expected rebound."""
    candidates = []

    for code, sig in indicators.items():
        reason = None

        # P1: Golden Pullback
        if (sig["above_ma200"]
                and 3 <= sig["consecutive_declines"] <= 5
                and sig["rsi_14"] < 40):
            reason = f"P1 Golden pullback ({sig['consecutive_declines']}d, RSI={sig['rsi_14']:.0f})"

        # P2: Oversold Bounce
        elif (sig["above_ma200"]
                and sig["rsi_14"] < 30
                and sig["rolling_max_drawdown"] > 0.08):
            reason = f"P2 Oversold bounce (RSI={sig['rsi_14']:.0f}, DD={sig['rolling_max_drawdown']:.1%})"

        # P3: Trend Pullback
        elif (sig["above_ma200"]
                and 3 <= sig["consecutive_declines"] <= 5
                and sig["rolling_sharpe"] > 0.5):
            reason = f"P3 Trend pullback ({sig['consecutive_declines']}d, Sharpe={sig['rolling_sharpe']:.1f})"

        # P4: Low-Vol Dip
        elif (sig["above_ma200"]
                and sig["rsi_14"] < 35
                and sig["volatility_ratio"] < 1.0):
            reason = f"P4 Low-vol dip (RSI={sig['rsi_14']:.0f}, vol<hist)"

        # P5: Deep Value
        elif (sig["above_ma200"]
                and sig["rolling_max_drawdown"] > 0.12
                and sig["rsi_14"] < 35):
            reason = f"P5 Deep value (DD={sig['rolling_max_drawdown']:.1%}, RSI={sig['rsi_14']:.0f})"

        # P6: Quality Dip
        elif (sig["above_ma200"]
                and sig["rolling_sharpe"] > 1.0
                and 2 <= sig["consecutive_declines"] <= 5):
            reason = f"P6 Quality dip ({sig['consecutive_declines']}d, Sharpe={sig['rolling_sharpe']:.1f})"

        # P7: Vol Contraction
        elif (sig["above_ma200"]
                and sig["volatility_ratio"] < 0.6
                and sig["pullback_from_peak"] > 0.03):
            reason = f"P7 Vol contraction (vol {sig['volatility_ratio']:.2f}x, pullback {sig['pullback_from_peak']:.1%})"

        # P8: Strong Trend Dip
        elif (sig["above_ma200"]
                and sig["trend_strength"] > 0.05
                and sig["consecutive_declines"] >= 2):
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


def select_sell_candidates(indicators, holding_codes=None):
    """Rule-based sell candidate selection, ranked by severity."""
    candidates = []
    codes = holding_codes if holding_codes else list(indicators.keys())

    for code in codes:
        if code not in indicators:
            continue
        sig = indicators[code]
        triggers = []

        # S1: Trend Break
        if not sig["above_ma200"]:
            triggers.append(f"S1 Trend break (bias {sig['trend_strength']:.1%})")

        # S2: Accelerating Deterioration
        if sig["rolling_max_drawdown"] > 0.10 and sig["volatility_ratio"] > 1.5:
            triggers.append(
                f"S2 Accelerating (DD {sig['rolling_max_drawdown']:.1%}, "
                f"vol {sig['volatility_ratio']:.1f}x)"
            )

        # S3: Overbought
        if sig["consecutive_rises"] >= 5 and sig["rsi_14"] > 70:
            triggers.append(
                f"S3 Overbought ({sig['consecutive_rises']}d, RSI={sig['rsi_14']:.0f})"
            )

        # S4: Risk Deterioration
        if sig["rolling_sharpe"] < 0 and sig["monthly_return"] < -0.05:
            triggers.append(
                f"S4 Risk decay (Sharpe {sig['rolling_sharpe']:.1f}, "
                f"monthly {sig['monthly_return']:.1%})"
            )

        # S5: Peak Retreat
        if (sig["above_ma200"]
                and sig["pullback_from_peak"] > 0.05
                and sig["consecutive_declines"] >= 4):
            triggers.append(
                f"S5 Peak retreat (pullback {sig['pullback_from_peak']:.1%}, "
                f"{sig['consecutive_declines']}d decline)"
            )

        # S6: Quality Collapse
        if sig["rolling_sharpe"] < -1.0:
            triggers.append(
                f"S6 Quality collapse (Sharpe {sig['rolling_sharpe']:.1f})"
            )

        # S7: Vol Explosion
        if sig["volatility_ratio"] > 2.0:
            triggers.append(
                f"S7 Vol explosion ({sig['volatility_ratio']:.1f}x hist)"
            )

        # S8: Slow Bleed
        if sig["above_ma200"] and sig["consecutive_declines"] >= 6:
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
    """Sell severity (0-1): max of four normalized risk dimensions.

    Each dimension maps to [0, 1]:
      - Trend risk:  how far below MA200 (0 at MA, 1 at -5%)
      - Drawdown:   60-day max drawdown (0 at 0%, 1 at 15%)
      - Vol spike:   vol ratio above 1.0 (0 at 1x, 1 at 2x)
      - Overbought:  RSI above 60 (0 at 60, 1 at 100)
    """
    trend = max(0.0, -sig["trend_strength"]) / 0.05 if not sig["above_ma200"] else 0.0
    drawdown = sig["rolling_max_drawdown"] / 0.15
    vol = max(0.0, sig["volatility_ratio"] - 1.0) / 1.0
    rsi = max(0.0, sig["rsi_14"] - 60) / 40

    return round(min(1.0, max(trend, drawdown, vol, rsi)), 4)


def classify_holding(sig):
    """Classify a single holding using rules. Returns (action, reason)."""
    # ── Sell rules ──────────────────────────
    if not sig["above_ma200"]:
        return "清仓", f"S1 Trend break (bias {sig['trend_strength']:.1%})"

    if sig["rolling_max_drawdown"] > 0.10 and sig["volatility_ratio"] > 1.5:
        return "减仓", f"S2 Accelerating (DD {sig['rolling_max_drawdown']:.1%})"

    if sig["consecutive_rises"] >= 5 and sig["rsi_14"] > 70:
        return "减仓", f"S3 Overbought ({sig['consecutive_rises']}d, RSI={sig['rsi_14']:.0f})"

    if sig["rolling_sharpe"] < 0 and sig["monthly_return"] < -0.05:
        return "减仓", f"S4 Risk decay (Sharpe {sig['rolling_sharpe']:.1f})"

    if (sig["above_ma200"]
            and sig["pullback_from_peak"] > 0.05
            and sig["consecutive_declines"] >= 4):
        return "减仓", f"S5 Peak retreat (pullback {sig['pullback_from_peak']:.1%})"

    if sig["rolling_sharpe"] < -1.0:
        return "减仓", f"S6 Quality collapse (Sharpe {sig['rolling_sharpe']:.1f})"

    if sig["volatility_ratio"] > 2.0:
        return "减仓", f"S7 Vol explosion ({sig['volatility_ratio']:.1f}x)"

    if sig["above_ma200"] and sig["consecutive_declines"] >= 6:
        return "减仓", f"S8 Slow bleed ({sig['consecutive_declines']}d decline)"

    # ── Buy rule ───────────────────────────
    if (sig["above_ma200"]
            and 3 <= sig["consecutive_declines"] <= 5
            and sig["rsi_14"] < 40):
        return "加仓", f"P1 Golden pullback ({sig['consecutive_declines']}d, RSI={sig['rsi_14']:.0f})"

    # ── Hold ───────────────────────────────
    if sig["above_ma200"]:
        return "持有", f"Uptrend (+{sig['trend_strength']:.1%} vs MA200)"
    else:
        return "持有", "Weak trend, monitoring"