"""回测报告生成：格式化输出到控制台和 CSV 文件。"""

import os
from datetime import datetime


def generate_report(result: dict) -> str:
    """生成回测报告文本。

    Args:
        result: BacktestEngine.run() 返回的字典
    Returns:
        格式化的报告字符串
    """
    stats = result["stats"]
    rules = result.get("rule_effectiveness", {})
    top = result.get("top_performers", [])
    worst = result.get("worst_performers", [])

    lines = []
    lines.append("═" * 56)
    lines.append("  基金策略回测报告")
    lines.append("═" * 56)
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # ── 收益指标 ──
    lines.append("─── 收益指标 ───")
    total_ret = stats.get("total_return", 0)
    tag = "✓" if total_ret > 0 else "✗"
    lines.append(f"  总收益率:     {total_ret:+.2%}  {tag}")

    annual = stats.get("annual_return", 0)
    tag2 = "✓" if annual > 0.03 else ("─" if annual > 0 else "✗")
    lines.append(f"  年化收益:     {annual:+.2%}  {tag2}")

    vol = stats.get("annual_volatility", 0)
    lines.append(f"  年化波动:     {vol:.1%}")

    sharpe = stats.get("sharpe", 0)
    tag3 = "✓" if sharpe > 0.5 else ("─" if sharpe > 0 else "✗")
    lines.append(f"  夏普比率:     {sharpe:.2f}  {tag3}")

    max_dd = stats.get("max_drawdown", 0)
    dd_s = stats.get("dd_start")
    dd_e = stats.get("dd_end")
    dd_str = ""
    if dd_s and dd_e:
        dd_s_str = dd_s.strftime("%Y-%m-%d") if hasattr(dd_s, "strftime") else str(dd_s)[:10]
        dd_e_str = dd_e.strftime("%Y-%m-%d") if hasattr(dd_e, "strftime") else str(dd_e)[:10]
        dd_str = f"  ({dd_s_str} ~ {dd_e_str})"
    lines.append(f"  最大回撤:    {max_dd:+.1%}{dd_str}")

    calmar = annual / max_dd if max_dd > 0 else 0
    lines.append(f"  卡尔玛比率:   {calmar:.2f}")
    lines.append("")

    # ── 交易统计 ──
    lines.append("─── 交易统计 ───")
    lines.append(f"  总交易次数:   {stats.get('total_trades', 0)}")
    lines.append(f"  买入:         {stats.get('buy_count', 0)}  笔")
    lines.append(f"  卖出:         {stats.get('sell_count', 0)}  笔")
    lines.append(f"  盈利卖出:     {stats.get('profitable_sells', 0)}  笔")
    lines.append(f"  胜率:         {stats.get('win_rate', 0):.1%}")
    lines.append(f"  手续费合计:   ¥{stats.get('total_fees', 0):,.2f}")
    lines.append("")

    # ── 规则有效性 ──
    if rules:
        lines.append("─── 规则有效性 ───")
        lines.append(f"  {'规则':<8s} {'触发':>6s} {'胜率':>8s}  {'评估'}")
        lines.append(f"  {'─'*8} {'─'*6} {'─'*8}  {'─'*8}")

        # 按触发次数排序
        sorted_rules = sorted(
            rules.items(),
            key=lambda x: x[1]["triggers"],
            reverse=True,
        )
        for rule_id, data in sorted_rules:
            total_t = data["triggers"]
            wr = data["win_rate"]
            if total_t >= 5:
                if wr > 0.6:
                    eval_txt = "✓ 有效"
                elif wr > 0.4:
                    eval_txt = "─ 一般"
                else:
                    eval_txt = "✗ 无效"
            else:
                eval_txt = "? 样本少"

            lines.append(
                f"  {rule_id:<8s} {total_t:>6d} {wr:>7.0%}  {eval_txt}"
            )
        lines.append("")

    # ── 最佳/最差基金 ──
    if top:
        lines.append("─── 区间收益 Top 5 ───")
        for i, f in enumerate(top, 1):
            lines.append(
                f"  {i}. {f['code']} {f['name']:<20s} {f['return']:+.1%}"
            )
        lines.append("")

    if worst:
        lines.append("─── 区间收益 Bottom 5 ───")
        for i, f in enumerate(worst, 1):
            lines.append(
                f"  {i}. {f['code']} {f['name']:<20s} {f['return']:+.1%}"
            )
        lines.append("")

    # ── 初始/最终资产 ──
    initial = result.get("initial_capital", 100000)
    final = initial * (1 + total_ret)
    lines.append("─── 资产变化 ───")
    lines.append(f"  初始资金:     ¥{initial:,.0f}")
    lines.append(f"  最终资产:     ¥{final:,.0f}")
    lines.append(f"  净收益:       ¥{final - initial:+,.0f}")
    lines.append("")
    lines.append("═" * 56)

    report = "\n".join(lines)

    # 同时写入 CSV
    _write_csv(result)

    return report


def _write_csv(result: dict):
    """将交易日志和日净值写入 CSV。"""
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cache",
    )
    os.makedirs(output_dir, exist_ok=True)

    # 日净值
    snapshots = result.get("snapshots", [])
    if snapshots:
        csv_path = os.path.join(output_dir, "backtest_daily.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("date,cash,position_value,total_value,daily_return\n")
            for s in snapshots:
                d = s.date.strftime("%Y-%m-%d") if hasattr(s.date, "strftime") else str(s.date)[:10]
                f.write(f"{d},{s.cash:.2f},{s.position_value:.2f},{s.total_value:.2f},{s.daily_return:.6f}\n")

    # 交易日志
    trades = result.get("trade_log", [])
    if trades:
        csv_path = os.path.join(output_dir, "backtest_trades.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("date,code,name,action,nav,units,amount,fee,reason\n")
            for t in trades:
                d = t.date.strftime("%Y-%m-%d") if hasattr(t.date, "strftime") else str(t.date)[:10]
                reason = t.reason.replace(",", " ")
                f.write(f"{d},{t.code},{t.name},{t.action},{t.nav:.4f},{t.units:.2f},{t.amount:.2f},{t.fee:.2f},{reason}\n")