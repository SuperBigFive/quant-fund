"""生成 fund-metrics 每日报告。"""

from datetime import datetime


def generate_report(buy_positions, sell_top10, hold_positions,
                    indicators=None, estimates=None):
    """生成文本报告。

    Args:
        buy_positions: 买入候选列表
        sell_top10: 卖出预警列表
        hold_positions: 持仓操作建议
        indicators: 指标字典 {code: {...}}
        estimates: 盘中估值字典 {code: {...}}
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("📊 fund-metrics 每日报告")
    lines.append(f"生成时间: {now}")
    lines.append("")

    # 买入推荐
    lines.append(f"━━━ 买入推荐 TOP {len(buy_positions)} ━━━")
    lines.append("")
    for i, item in enumerate(buy_positions, 1):
        name = item.get("name", item["code"])
        lines.append(f"  {i}. {name}({item['code']})")
        lines.append(_fund_return_line(item, indicators, estimates))
        nav_str = f"{item['nav']:.4f}" if item.get("nav") else "N/A"
        pct_str = f"{item['pct']:.1%}" if item.get("pct") else "N/A"
        lines.append(f"     净值: {nav_str}  建议仓位: {pct_str}")
        if item.get("reason"):
            lines.append(f"     触发: {item['reason']}")
        if item.get("reason_detail"):
            lines.append(f"     计算: {item['reason_detail']}")
        if item.get("stats"):
            s = item["stats"]
            lines.append(
                f"     指标: 夏普{s['sharpe']:.2f} | "
                f"年化波动{s['annual_vol']:.1%} | "
                f"最大回撤{s['max_drawdown']:.1%}"
            )
        lines.append("")

    # 卖出预警
    lines.append("━━━ 卖出预警 ━━━")
    lines.append("")
    if sell_top10:
        for i, item in enumerate(sell_top10, 1):
            name = item.get("name", item["code"])
            lines.append(f"  {i}. {name}({item['code']})")
            lines.append(_fund_return_line(item, indicators, estimates))
            sv = item.get("severity", 0)
            lines.append(f"     严重度: {sv:.2f}  净值: {item['nav']:.4f}")
            lines.append(f"     触发: {item['reason']}")
            lines.append("")
    else:
        lines.append("  无卖出预警")
        lines.append("")

    # 持仓操作建议
    lines.append("━━━ 持仓操作建议 ━━━")
    lines.append("")
    for item in hold_positions:
        name = item.get("name", item["code"])
        nav_str = f"{item.get('nav', 0):.4f}" if item.get("nav") else "N/A"
        action = item.get("action", "持有")
        pct = item.get("pct", 0)

        action_icon = {
            "加仓": "🟢",
            "减仓": "🔴",
            "清仓": "🔴",
            "持有": "🔵",
            "观望": "🟡",
            "数据不足": "⚪",
        }.get(action, "⚪")

        lines.append(f"  {action_icon} {name}({item['code']})")
        lines.append(_fund_return_line(item, indicators, estimates))

        if action == "加仓" and pct > 0:
            lines.append(
                f"     操作: {action}  比例: 总资金的{pct:.1%}  净值: {nav_str}"
            )
        elif action in ("减仓", "清仓") and pct > 0:
            lines.append(
                f"     操作: {action}  比例: 卖出持仓的{pct:.0%}  净值: {nav_str}"
            )
        else:
            lines.append(f"     操作: {action}  净值: {nav_str}")

        if item.get("reason_detail"):
            lines.append(f"     依据: {item['reason_detail']}")
        elif item.get("reason"):
            lines.append(f"     理由: {item['reason']}")

        if item.get("stats"):
            s = item["stats"]
            lines.append(
                f"     指标: 夏普{s['sharpe']:.2f} | "
                f"年化波动{s['annual_vol']:.1%} | "
                f"最大回撤{s['max_drawdown']:.1%}"
            )
        lines.append("")

    return "\n".join(lines)


def _fund_return_line(item, indicators, estimates=None):
    """生成基金的日期、涨跌和估值信息行。

    格式: "     📅 06-17 | 📈 估值 +0.35% | 当日 +0.87% | 近一月 +2.35% | 3连跌"
    """
    code = item["code"]
    if not indicators or code not in indicators:
        return ""

    sig = indicators[code]
    parts = []

    # 净值日期
    last_date = sig.get("last_date", "")
    if last_date:
        try:
            dt = datetime.strptime(last_date[:10], "%Y-%m-%d")
            parts.append(f"📅 {dt.strftime('%m-%d')}")
        except ValueError:
            parts.append(f"📅 {last_date}")

    # 盘中实时估值
    has_estimate = False
    if estimates and code in estimates:
        est = estimates[code]
        est_change = est.get("estimate_change", 0)
        est_time = est.get("estimate_time", "")
        # 只要有估值数据就显示（涨跌为0也显示0.00%）
        sign = "+" if est_change >= 0 else ""
        parts.append(f"📈 估值 {sign}{est_change:.2f}%")
        has_estimate = True
        if est_time:
            # 只显示时间部分（如 "14:59"）
            try:
                t = datetime.strptime(est_time.strip(), "%Y-%m-%d %H:%M")
                parts.append(f"⏰ {t.strftime('%H:%M')}")
            except ValueError:
                parts.append(f"⏰ {est_time}")

    # 当日涨跌——仅在没有估值时显示（有估值时它与估值重复）
    if not has_estimate:
        day_change = sig.get("single_day_change")
        if day_change is not None and abs(day_change) > 0.0001:
            sign = "+" if day_change >= 0 else ""
            parts.append(f"当日 {sign}{day_change:.2%}")

    # 近一月涨跌
    monthly = sig.get("monthly_return")
    if monthly is not None:
        sign = "+" if monthly >= 0 else ""
        parts.append(f"近一月 {sign}{monthly:.2%}")

    # 近期回调或连涨
    recent = sig.get("recent_decline", 0)
    if sig.get("consecutive_declines", 0) >= 2 and abs(recent) > 0.001:
        parts.append(f"回调 {recent:.2%}")
    elif sig.get("consecutive_rises", 0) >= 2 and abs(recent) > 0.001:
        parts.append(f"连涨 {abs(recent):.2%}")

    # 连跌/连涨天数
    declines = sig.get("consecutive_declines", 0)
    rises = sig.get("consecutive_rises", 0)
    if declines >= 1:
        parts.append(f"{declines}连跌")
    elif rises >= 1:
        parts.append(f"{rises}连涨")

    return "     " + " | ".join(parts) if parts else ""