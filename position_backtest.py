#!/usr/bin/env python3
"""仓位比例化回测引擎。

核心改变：从"全仓/清仓"二元操作改为"每日计算目标仓位比例→调整差额"。

目标仓位计算：
  base = 0.5（中性基准）
  + 每个买入信号触发 → 加 weight
  - 每个卖出信号触发 → 减 weight
  + MA200上方 → 加 trend_bias（牛市倾向）
  + RSI超卖 → 加 rsi_bonus
  + RSI超买 → 减 rsi_penalty
  钳位到 [min_pos, max_pos]

交易执行：
  目标仓位 vs 当前仓位 差额 > min_trade_pct → 调整
  买入差额 = (target - current) * 总资产
  卖出差额 = (current - target) * 总资产
"""
import json
import numpy as np
from collections import defaultdict
from swing_backtest import (
    precompute, load_funds, check_buy, check_sell,
    BENCHMARK, WINDOWS, FEE_BUY, FEE_SELL, INITIAL_CAPITAL,
    DEFAULT_BUY_RULES, DEFAULT_SELL_RULES,
)


def compute_target_position(i, ind, buy_rules, sell_rules, params):
    """计算第 i 天的目标仓位比例（0.0 ~ 1.0）。

    多个信号加权叠加，看涨加仓、看跌减仓。
    """
    nav = ind["navs"][i]
    ma200 = ind["ma200"][i]
    if np.isnan(ma200) or nav <= 0:
        return 0.0

    base = params.get("base_pos", 0.5)
    target = base

    above_ma = nav >= ma200
    rsi = ind["rsi"][i]
    trend = ind["trend"][i]
    max_dd = ind["max_dd"][i]
    pullback = ind["pullback"][i]
    consec_dec = ind["consec_dec"][i]
    monthly = ind["monthly"][i]
    vol_ratio = ind["vol_ratio"][i]

    # ── 趋势信号：MA200 方向 ──
    trend_bias = params.get("trend_bias", 0.15)
    if above_ma:
        target += trend_bias
    else:
        target -= trend_bias

    # ── RSI 信号：超卖加仓，超买减仓 ──
    rsi_oversold = params.get("rsi_oversold", 35)
    rsi_overbought = params.get("rsi_overbought", 65)
    rsi_bonus = params.get("rsi_bonus", 0.10)
    if rsi < rsi_oversold:
        # RSI 越低，加仓越多（线性）
        ratio = (rsi_oversold - rsi) / rsi_oversold
        target += rsi_bonus * ratio
    elif rsi > rsi_overbought:
        ratio = (rsi - rsi_overbought) / (100 - rsi_overbought) if rsi_overbought < 100 else 0
        target -= rsi_bonus * ratio

    # ── P1-P8 买入规则：每个触发加仓 ──
    buy_weight = params.get("buy_weight", 0.08)  # 每个买入规则触发的加仓量
    # 逐个检查 P1-P8 是否触发
    triggered_buys = 0
    if buy_rules.get("P1", {}).get("enabled", True):
        r = buy_rules["P1"]
        if above_ma and r["decline_min"] <= consec_dec <= r["decline_max"] and rsi < r["rsi_max"]:
            triggered_buys += 1
    if buy_rules.get("P2", {}).get("enabled", True):
        r = buy_rules["P2"]
        if above_ma and rsi < r["rsi_max"] and max_dd > r["max_dd_min"]:
            triggered_buys += 1
    if buy_rules.get("P3", {}).get("enabled", True):
        r = buy_rules["P3"]
        if above_ma and r["decline_min"] <= consec_dec <= r["decline_max"] and ind["sharpe"][i] > r["sharpe_min"]:
            triggered_buys += 1
    if buy_rules.get("P4", {}).get("enabled", True):
        r = buy_rules["P4"]
        if above_ma and rsi < r["rsi_max"] and vol_ratio < r["vol_ratio_max"]:
            triggered_buys += 1
    if buy_rules.get("P5", {}).get("enabled", True):
        r = buy_rules["P5"]
        if above_ma and max_dd > r["max_dd_min"] and rsi < r["rsi_max"]:
            triggered_buys += 1
    if buy_rules.get("P6", {}).get("enabled", True):
        r = buy_rules["P6"]
        if above_ma and ind["sharpe"][i] > r["sharpe_min"] and r["decline_min"] <= consec_dec <= r["decline_max"]:
            triggered_buys += 1
    if buy_rules.get("P7", {}).get("enabled", True):
        r = buy_rules["P7"]
        if above_ma and vol_ratio < r["vol_ratio_max"] and pullback > r["pullback_min"]:
            triggered_buys += 1
    if buy_rules.get("P8", {}).get("enabled", True):
        r = buy_rules["P8"]
        if above_ma and trend > r["trend_min"] and consec_dec >= r["decline_min"]:
            triggered_buys += 1

    target += triggered_buys * buy_weight

    # ── S1-S8 卖出规则：每个触发减仓 ──
    sell_weight = params.get("sell_weight", 0.10)
    triggered_sells = 0

    # S1: 60日回撤过大
    if sell_rules.get("S1", {}).get("enabled", True):
        r = sell_rules["S1"]
        if max_dd > r["max_dd_min"]:
            triggered_sells += 1
    # S2: 连涨+超买
    if sell_rules.get("S2", {}).get("enabled", False):
        r = sell_rules["S2"]
        if ind["consec_rise"][i] >= r["rise_min"] and rsi > r["rsi_min"]:
            triggered_sells += 1
    # S3: 月度涨幅过大+超买
    if sell_rules.get("S3", {}).get("enabled", False):
        r = sell_rules["S3"]
        if monthly > r["monthly_min"] and rsi > r["rsi_min"]:
            triggered_sells += 1
    # S4: 夏普差+月度下跌
    if sell_rules.get("S4", {}).get("enabled", False):
        r = sell_rules["S4"]
        if ind["sharpe"][i] < r["sharpe_max"] and monthly < r["monthly_max"]:
            triggered_sells += 1
    # S5: 高点回撤+连跌
    if sell_rules.get("S5", {}).get("enabled", False):
        r = sell_rules["S5"]
        if above_ma and pullback > r["pullback_min"] and consec_dec >= r["decline_min"]:
            triggered_sells += 1
    # S6: 趋势偏离过大+超买
    if sell_rules.get("S6", {}).get("enabled", False):
        r = sell_rules["S6"]
        if trend > r["trend_min"] and rsi > r["rsi_min"]:
            triggered_sells += 1
    # S7: 波动率放大
    if sell_rules.get("S7", {}).get("enabled", False):
        r = sell_rules["S7"]
        if vol_ratio > r["vol_ratio_min"]:
            triggered_sells += 1
    # S8: 月度大跌
    if sell_rules.get("S8", {}).get("enabled", False):
        r = sell_rules["S8"]
        if not above_ma and monthly < r["monthly_max"]:
            triggered_sells += 1

    target -= triggered_sells * sell_weight

    # ── 回撤信号：当前回撤越大，仓位越低 ──
    dd_penalty = params.get("dd_penalty", 0.10)
    dd_threshold = params.get("dd_threshold", 0.08)
    if max_dd > dd_threshold:
        # 回撤超过阈值后，线性降低仓位
        ratio = min(1.0, (max_dd - dd_threshold) / 0.15)
        target -= dd_penalty * ratio

    # 钳位
    min_pos = params.get("min_pos", 0.0)
    max_pos = params.get("max_pos", 1.0)
    target = max(min_pos, min(max_pos, target))

    return target


def backtest_position(ind, start_date, end_date, buy_rules, sell_rules, params):
    """仓位比例化回测引擎。

    每日计算目标仓位比例，调整差额部分。
    买入：差额 > 0 → 买入 (target - current) * 总资产
    卖出：差额 < 0 → 卖出 (current - target) * 总资产
    """
    if ind is None:
        return None

    dates = ind["dates"]
    navs = ind["navs"]
    n = len(navs)

    min_trade_pct = params.get("min_trade_pct", 0.05)  # 最小交易比例（差额<5%不交易）
    cooldown = params.get("buy_cooldown", 1)            # 交易冷却天数

    # 定位回测区间
    start_idx = None
    for i, d in enumerate(dates):
        if d >= start_date:
            start_idx = i
            break
    if start_idx is None or start_idx < 200:
        return None
    end_idx = n - 1
    for i in range(start_idx, n):
        if dates[i] > end_date:
            end_idx = i - 1
            break
    if end_idx <= start_idx:
        return None

    nav0 = navs[start_idx]
    nav_end = navs[end_idx]
    if nav0 <= 0:
        return None
    bh_return = (1 - FEE_BUY) * nav_end / nav0 - 1

    cash = INITIAL_CAPITAL
    units = 0.0
    trades = 0
    last_trade_day = -999
    trade_log = []

    # 起始：计算第一天目标仓位并建仓
    if nav0 > 0:
        target = compute_target_position(start_idx, ind, buy_rules, sell_rules, params)
        buy_amt = cash * target * 0.999
        if buy_amt > 100:
            fee = buy_amt * FEE_BUY
            units = (buy_amt - fee) / nav0
            cash -= buy_amt
            trades += 1
            last_trade_day = start_idx
            cur_pos = target
            trade_log.append((dates[start_idx], "BUY", nav0, f"建仓{target:.0%}"))
        else:
            cur_pos = 0.0
    else:
        cur_pos = 0.0

    for i in range(start_idx + 1, end_idx + 1):
        nav = navs[i]
        if nav <= 0 or np.isnan(ind["ma200"][i]):
            continue

        # 计算当前总资产和仓位
        total_value = cash + units * nav
        if total_value <= 0:
            continue
        current_pos = (units * nav) / total_value

        # 计算目标仓位
        target_pos = compute_target_position(i, ind, buy_rules, sell_rules, params)

        # 差额
        diff = target_pos - current_pos

        # 冷却期检查
        if i - last_trade_day < cooldown:
            continue

        # 差额太小不交易
        if abs(diff) < min_trade_pct:
            continue

        if diff > 0:
            # 买入差额部分
            buy_amt = total_value * diff * 0.999
            if buy_amt > 100 and cash > 100:
                fee = buy_amt * FEE_BUY
                units += (buy_amt - fee) / nav
                cash -= buy_amt
                trades += 1
                last_trade_day = i
                cur_pos = target_pos
                trade_log.append((dates[i], "BUY", nav, f"+{diff:.0%}→{target_pos:.0%}"))
        else:
            # 卖出差额部分
            sell_units = units * (-diff)
            if sell_units > 0.01:
                # 判断是否短期赎回
                # 简化：统一用0.5%赎回费（因为不是全仓进出，持有时间难追踪）
                gross = sell_units * nav
                fee = gross * FEE_SELL
                cash += gross - fee
                units -= sell_units
                trades += 1
                last_trade_day = i
                cur_pos = target_pos
                trade_log.append((dates[i], "SELL", nav, f"{diff:.0%}→{target_pos:.0%}"))

    final_value = cash + units * navs[end_idx]
    strat_return = final_value / INITIAL_CAPITAL - 1

    return {
        "strat_return": strat_return,
        "bh_return": bh_return,
        "excess": strat_return - bh_return,
        "trades": trades,
        "trade_log": trade_log,
    }


def run_batch_position(funds, buy_rules, sell_rules, params, windows=None, verbose=True):
    """批量仓位比例化回测。"""
    if windows is None:
        windows = WINDOWS
    results = {w[0]: {} for w in windows}

    precomputed = {}
    for fund in funds:
        ind = precompute(fund["records"])
        if ind is not None:
            precomputed[fund["code"]] = ind

    for fi, (code, ind) in enumerate(precomputed.items()):
        for wname, sdate, edate in windows:
            r = backtest_position(ind, sdate, edate, buy_rules, sell_rules, params)
            if r:
                results[wname][code] = r
        if verbose and (fi + 1) % 20 == 0:
            print(f"  进度: {fi+1}/{len(precomputed)}", flush=True)

    return results, precomputed


def summarize(results, funds_meta=None):
    """汇总结果。"""
    meta = {f["code"]: f for f in (funds_meta or [])}
    print("\n" + "=" * 80)
    print("仓位比例化策略回测结果汇总")
    print("=" * 80)

    for wname, code_results in results.items():
        if not code_results:
            continue
        n = len(code_results)
        wins = sum(1 for r in code_results.values() if r["excess"] > 0)
        avg_strat = np.mean([r["strat_return"] for r in code_results.values()])
        avg_bh = np.mean([r["bh_return"] for r in code_results.values()])
        avg_excess = np.mean([r["excess"] for r in code_results.values()])
        med_excess = np.median([r["excess"] for r in code_results.values()])
        avg_trades = np.mean([r["trades"] for r in code_results.values()])

        bm = code_results.get(BENCHMARK)

        print(f"\n--- {wname} ({n} 只基金) ---")
        print(f"  胜率:        {wins}/{n} = {wins/n:.1%}")
        print(f"  平均策略收益: {avg_strat:+.2%}")
        print(f"  平均持有收益: {avg_bh:+.2%}")
        print(f"  平均超额:    {avg_excess:+.2%}  (中位 {med_excess:+.2%})")
        print(f"  平均交易:    {avg_trades:.1f} 笔")
        if bm:
            print(f"  沪深300:     策略 {bm['strat_return']:+.2%} vs 持有 {bm['bh_return']:+.2%}  超额 {bm['excess']:+.2%}  ({bm['trades']}笔)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="仓位比例化回测")
    parser.add_argument("--n", type=int, default=None, help="基金数量")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    print("加载基金数据...", flush=True)
    funds = load_funds(n=args.n, include_benchmark=True)
    print(f"共 {len(funds)} 只基金（含沪深300基准）", flush=True)

    # 网格搜索优化参数（v3.1）
    params = {
        "base_pos": 0.7,          # 中性基准仓位（牛市高仓位捕获涨幅）
        "trend_bias": 0.2,        # MA200趋势偏移（站上方+20%，下方-20%）
        "rsi_oversold": 30,       # RSI超卖阈值
        "rsi_overbought": 70,     # RSI超买阈值
        "rsi_bonus": 0.10,        # RSI信号权重
        "buy_weight": 0.05,       # 每个买入规则触发的加仓量
        "sell_weight": 0.15,      # 每个卖出规则触发的减仓量
        "dd_penalty": 0.10,       # 回撤惩罚
        "dd_threshold": 0.08,     # 回撤惩罚阈值
        "min_pos": 0.3,           # 最小仓位（不完全清仓）
        "max_pos": 1.0,           # 最大仓位
        "min_trade_pct": 0.20,    # 最小交易差额（20%才调整，减少频繁交易）
        "buy_cooldown": 3,        # 交易冷却天数
    }

    print(f"\n策略: 仓位比例化（基准{params['base_pos']:.0%} + P1-P8加仓 + S1-S8减仓）")
    print(f"运行回测（{len(WINDOWS)} 窗口）...", flush=True)
    results, _ = run_batch_position(funds, DEFAULT_BUY_RULES, DEFAULT_SELL_RULES, params, verbose=not args.quiet)
    summarize(results, funds)

    # 多窗口一致性
    wnames = [w[0] for w in WINDOWS]
    fund_wins = defaultdict(list)
    for wn in wnames:
        for code, r in results[wn].items():
            fund_wins[code].append(r["excess"] > 0)
    full = {c: w for c, w in fund_wins.items() if len(w) == len(wnames)}
    if full:
        win2 = sum(1 for w in full.values() if sum(w) >= 2)
        non_bull_win = sum(1 for w in full.values() if w[2] and w[3])
        bull_exc = np.mean([r["excess"] for wn in [wnames[0], wnames[1]] for r in results[wn].values()])
        non_bull_exc = np.mean([r["excess"] for wn in [wnames[2], wnames[3]] for r in results[wn].values()])
        bm_exc = [results[wn].get(BENCHMARK, {}).get("excess", 0) for wn in wnames]
        print(f"\n--- 多窗口一致性 ---")
        print(f"  ≥2窗口赢: {win2}/{len(full)} = {win2/len(full):.1%}")
        print(f"  非牛市双赢: {non_bull_win}/{len(full)} = {non_bull_win/len(full):.1%}")
        print(f"  牛市平均超额: {bull_exc:+.2%}")
        print(f"  非牛市平均超额: {non_bull_exc:+.2%}")
        print(f"  沪深300各窗口: {[f'{e:+.1%}' for e in bm_exc]}")

    # 沪深300交易明细
    for wn in wnames:
        r = results[wn].get(BENCHMARK)
        if r:
            print(f"\n  {wn}: 沪深300 {r['trades']}笔")
            for t in r["trade_log"][:8]:
                print(f"    {t[0]} {t[1]} @{t[2]:.4f} {t[3]}")

    # 保存
    out = "/mnt/d/software/USTC-Drive/科大云盘/charming-cloud/project/quant-fund/position_results.json"
    serializable = {}
    for wn, cr in results.items():
        serializable[wn] = {
            code: {k: v for k, v in r.items() if k != "trade_log"}
            for code, r in cr.items()
        }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\n明细已保存: {out}")


if __name__ == "__main__":
    main()
