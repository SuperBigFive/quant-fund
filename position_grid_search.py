#!/usr/bin/env python3
"""仓位比例化策略参数网格搜索。"""
import json
import numpy as np
from collections import defaultdict
from position_backtest import backtest_position, compute_target_position, run_batch_position
from swing_backtest import (
    precompute, load_funds, check_buy, check_sell,
    BENCHMARK, WINDOWS, FEE_BUY, FEE_SELL, INITIAL_CAPITAL,
    DEFAULT_BUY_RULES, DEFAULT_SELL_RULES,
)


def evaluate(funds, precomputed, buy_rules, sell_rules, params):
    """评估一组参数。"""
    wnames = [w[0] for w in WINDOWS]
    results = {w[0]: {} for w in WINDOWS}

    for code, ind in precomputed.items():
        for wname, sdate, edate in WINDOWS:
            r = backtest_position(ind, sdate, edate, buy_rules, sell_rules, params)
            if r:
                results[wname][code] = r

    fund_wins = defaultdict(list)
    for wn in wnames:
        for code, r in results[wn].items():
            fund_wins[code].append(r["excess"] > 0)
    full = {c: w for c, w in fund_wins.items() if len(w) == len(wnames)}
    if not full:
        return None

    win2 = sum(1 for w in full.values() if sum(w) >= 2)
    non_bull_win = sum(1 for w in full.values() if w[2] and w[3])
    bull_exc = np.mean([r["excess"] for wn in [wnames[0], wnames[1]] for r in results[wn].values()])
    non_bull_exc = np.mean([r["excess"] for wn in [wnames[2], wnames[3]] for r in results[wn].values()])
    bm_exc = [results[wn].get(BENCHMARK, {}).get("excess", 0) for wn in wnames]
    bm_non_bull = bm_exc[2] + bm_exc[3]
    avg_trades = np.mean([r["trades"] for wn in wnames for r in results[wn].values()])

    return {
        "win2": win2,
        "total": len(full),
        "win2_pct": win2 / len(full),
        "non_bull_win": non_bull_win,
        "non_bull_pct": non_bull_win / len(full),
        "bull_exc": bull_exc,
        "non_bull_exc": non_bull_exc,
        "bm_exc": bm_exc,
        "bm_non_bull": bm_non_bull,
        "avg_trades": avg_trades,
    }


def main():
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(f"加载 {n} 只基金...", flush=True)
    funds = load_funds(n=n, include_benchmark=True)

    precomputed = {}
    for fund in funds:
        ind = precompute(fund["records"])
        if ind is not None:
            precomputed[fund["code"]] = ind
    print(f"预计算 {len(precomputed)} 只基金", flush=True)

    buy_rules = DEFAULT_BUY_RULES
    sell_rules = DEFAULT_SELL_RULES

    # ── 第1轮：基准仓位 + 趋势偏移 + 最小交易差额 ──
    print("\n=== 第1轮：基准仓位/趋势偏移/最小交易差额 ===", flush=True)
    best1 = None
    best_score1 = -999

    for base_pos in [0.6, 0.7, 0.8, 0.9]:
        for trend_bias in [0.05, 0.10, 0.15, 0.20]:
            for min_trade in [0.05, 0.10, 0.15, 0.20]:
                params = {
                    "base_pos": base_pos,
                    "trend_bias": trend_bias,
                    "rsi_oversold": 35,
                    "rsi_overbought": 65,
                    "rsi_bonus": 0.10,
                    "buy_weight": 0.08,
                    "sell_weight": 0.10,
                    "dd_penalty": 0.10,
                    "dd_threshold": 0.08,
                    "min_pos": 0.0,
                    "max_pos": 1.0,
                    "min_trade_pct": min_trade,
                    "buy_cooldown": 3,
                }
                ev = evaluate(funds, precomputed, buy_rules, sell_rules, params)
                if ev is None:
                    continue
                score = ev["non_bull_exc"] * 2 + ev["bull_exc"] + ev["bm_non_bull"] * 0.5
                if score > best_score1:
                    best_score1 = score
                    best1 = (base_pos, trend_bias, min_trade)
                    print(f"  base={base_pos:.0%} tb={trend_bias:.0%} mt={min_trade:.0%}: "
                          f"bull={ev['bull_exc']:+.1%} nonbull={ev['non_bull_exc']:+.1%} "
                          f"CSI300_nb={ev['bm_non_bull']:+.1%} win2={ev['win2_pct']:.0%} "
                          f"trades={ev['avg_trades']:.0f} score={score:+.2f} ← BEST", flush=True)

    print(f"\n第1轮最优: base={best1[0]:.0%} tb={best1[1]:.0%} mt={best1[2]:.0%}", flush=True)

    # ── 第2轮：RSI + 买卖权重 ──
    print("\n=== 第2轮：RSI阈值/买卖权重 ===", flush=True)
    base_pos, trend_bias, min_trade = best1
    best2 = None
    best_score2 = -999

    for rsi_os in [30, 35, 40]:
        for rsi_ob in [60, 65, 70]:
            for buy_w in [0.05, 0.08, 0.12]:
                for sell_w in [0.08, 0.10, 0.15]:
                    params = {
                        "base_pos": base_pos,
                        "trend_bias": trend_bias,
                        "rsi_oversold": rsi_os,
                        "rsi_overbought": rsi_ob,
                        "rsi_bonus": 0.10,
                        "buy_weight": buy_w,
                        "sell_weight": sell_w,
                        "dd_penalty": 0.10,
                        "dd_threshold": 0.08,
                        "min_pos": 0.0,
                        "max_pos": 1.0,
                        "min_trade_pct": min_trade,
                        "buy_cooldown": 3,
                    }
                    ev = evaluate(funds, precomputed, buy_rules, sell_rules, params)
                    if ev is None:
                        continue
                    score = ev["non_bull_exc"] * 2 + ev["bull_exc"] + ev["bm_non_bull"] * 0.5
                    if score > best_score2:
                        best_score2 = score
                        best2 = (rsi_os, rsi_ob, buy_w, sell_w)
                        print(f"  rsi_os={rsi_os} ob={rsi_ob} bw={buy_w} sw={sell_w}: "
                              f"bull={ev['bull_exc']:+.1%} nonbull={ev['non_bull_exc']:+.1%} "
                              f"CSI300_nb={ev['bm_non_bull']:+.1%} win2={ev['win2_pct']:.0%} "
                              f"trades={ev['avg_trades']:.0f} score={score:+.2f} ← BEST", flush=True)

    print(f"\n第2轮最优: rsi_os={best2[0]} ob={best2[1]} bw={best2[2]} sw={best2[3]}", flush=True)

    # ── 第3轮：回撤惩罚 + 冷却期 + min_pos ──
    print("\n=== 第3轮：回撤惩罚/冷却期/最小仓位 ===", flush=True)
    rsi_os, rsi_ob, buy_w, sell_w = best2
    best3 = None
    best_score3 = -999

    for dd_pen in [0.05, 0.10, 0.15, 0.20]:
        for dd_thr in [0.05, 0.08, 0.12]:
            for cooldown in [2, 3, 5]:
                for min_pos in [0.0, 0.2, 0.3]:
                    params = {
                        "base_pos": base_pos,
                        "trend_bias": trend_bias,
                        "rsi_oversold": rsi_os,
                        "rsi_overbought": rsi_ob,
                        "rsi_bonus": 0.10,
                        "buy_weight": buy_w,
                        "sell_weight": sell_w,
                        "dd_penalty": dd_pen,
                        "dd_threshold": dd_thr,
                        "min_pos": min_pos,
                        "max_pos": 1.0,
                        "min_trade_pct": min_trade,
                        "buy_cooldown": cooldown,
                    }
                    ev = evaluate(funds, precomputed, buy_rules, sell_rules, params)
                    if ev is None:
                        continue
                    score = ev["non_bull_exc"] * 2 + ev["bull_exc"] + ev["bm_non_bull"] * 0.5
                    if score > best_score3:
                        best_score3 = score
                        best3 = (dd_pen, dd_thr, cooldown, min_pos)
                        print(f"  dd_pen={dd_pen} dd_thr={dd_thr} cd={cooldown} min_pos={min_pos:.0%}: "
                              f"bull={ev['bull_exc']:+.1%} nonbull={ev['non_bull_exc']:+.1%} "
                              f"CSI300_nb={ev['bm_non_bull']:+.1%} win2={ev['win2_pct']:.0%} "
                              f"trades={ev['avg_trades']:.0f} score={score:+.2f} ← BEST", flush=True)

    print(f"\n第3轮最优: dd_pen={best3[0]} dd_thr={best3[1]} cd={best3[2]} min_pos={best3[3]:.0%}", flush=True)

    # ── 最终结果 ──
    dd_pen, dd_thr, cooldown, min_pos = best3
    final_params = {
        "base_pos": base_pos,
        "trend_bias": trend_bias,
        "rsi_oversold": rsi_os,
        "rsi_overbought": rsi_ob,
        "rsi_bonus": 0.10,
        "buy_weight": buy_w,
        "sell_weight": sell_w,
        "dd_penalty": dd_pen,
        "dd_threshold": dd_thr,
        "min_pos": min_pos,
        "max_pos": 1.0,
        "min_trade_pct": min_trade,
        "buy_cooldown": cooldown,
    }

    ev = evaluate(funds, precomputed, buy_rules, sell_rules, final_params)
    print(f"\n{'='*60}")
    print(f"最终最优参数组合")
    print(f"{'='*60}")
    for k, v in final_params.items():
        print(f"  {k}: {v}")
    print(f"\n结果:")
    print(f"  ≥2窗口赢: {ev['win2']}/{ev['total']} = {ev['win2_pct']:.1%}")
    print(f"  非牛市双赢: {ev['non_bull_win']}/{ev['total']} = {ev['non_bull_pct']:.1%}")
    print(f"  牛市平均超额: {ev['bull_exc']:+.2%}")
    print(f"  非牛市平均超额: {ev['non_bull_exc']:+.2%}")
    print(f"  沪深300各窗口: {[f'{e:+.1%}' for e in ev['bm_exc']]}")
    print(f"  平均交易笔数: {ev['avg_trades']:.0f}")

    out = {
        "params": final_params,
        "results": {
            "win2_pct": ev["win2_pct"],
            "non_bull_pct": ev["non_bull_pct"],
            "bull_exc": ev["bull_exc"],
            "non_bull_exc": ev["non_bull_exc"],
            "bm_exc": ev["bm_exc"],
            "avg_trades": ev["avg_trades"],
        },
    }
    with open("/mnt/d/software/USTC-Drive/科大云盘/charming-cloud/project/quant-fund/position_grid_result.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: position_grid_result.json")


if __name__ == "__main__":
    main()
