from scorer import classify_holding


def analyze_holdings(holding_codes, indicators, strategy):
    """Classify each holding using rule-based logic from scorer."""
    results = []
    for code in holding_codes:
        if code not in indicators:
            results.append({
                "code": code, "name": code,
                "action": "数据不足",
                "reason": "无法获取数据",
                "nav": None,
            })
            continue

        sig = indicators[code]
        action, reason = classify_holding(sig)

        results.append({
            "code": code,
            "name": code,
            "action": action,
            "reason": reason,
            "nav": sig["current_nav"],
            "trend_strength": sig["trend_strength"],
            "consecutive_declines": sig["consecutive_declines"],
            "monthly_return": sig["monthly_return"],
        })

    return results