#!/usr/bin/env python3
"""fund-metrics — 基金指标系统：每日运行，生成买卖建议并推送微信。

用法:
    python main.py                     # 完整流程（含盘中估值，使用基金池缓存）
    python main.py --no-push           # 不推送微信
    python main.py --no-estimate       # 不使用盘中估值（盘后用）
    python main.py --fund 000001       # 单只基金诊断
    python main.py --refresh-universe  # 强制重建基金池
"""

import argparse
import json
import logging
import os
import sys
import unicodedata
from datetime import datetime

import yaml
from dotenv import load_dotenv

from data_fetcher import DataFetcher
from universe import filter_universe
from indicators import compute_indicators
from scorer import select_buy_candidates, select_sell_candidates, classify_holding
from holdings import analyze_holdings
from position_advisor import compute_position_advice
from reporter import generate_report
from notifier import send_wechat

# ── 日志 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "fund-metrics.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_holdings():
    holdings_path = os.path.join(os.path.dirname(__file__), "holdings.txt")
    codes = []
    with open(holdings_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                codes.append(line)
    return codes


def display_width(s):
    width = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def pad_to_width(s, target_width):
    current = display_width(s)
    return s + " " * max(0, target_width - current)


def get_pushplus_token(config):
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        token = config.get("pushplus", {}).get("token", "")
    if not token or token == "YOUR_TOKEN_HERE":
        return None
    return token


def run_pipeline(config, holding_codes, use_estimates=True, refresh_universe=False):
    fetcher = DataFetcher(config)
    strategy = config["strategy"]
    target_vol = strategy.get("target_annual_vol", 0.10)

    # 基金池缓存
    cache_path = os.path.join(os.path.dirname(__file__), "cache", "filtered_universe.json")
    universe = None
    cache_valid = False

    if os.path.exists(cache_path):
        try:
            if not refresh_universe:
                with open(cache_path, "r", encoding="utf-8") as f:
                    universe = json.load(f)
                mtime = datetime.fromtimestamp(os.path.getmtime(cache_path))
                age_days = (datetime.now() - mtime).days
                logger.info("[1/6] 从缓存加载基金池: %d 只 (缓存 %d 天)", len(universe), age_days)
                cache_valid = True
            else:
                logger.info("[1/6] --refresh-universe，强制重新筛选")
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("  缓存损坏，将重新筛选")

    if not cache_valid:
        logger.info("[1/6] 筛选基金池...")
        universe = filter_universe(fetcher, config)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(universe, f, ensure_ascii=False)

    logger.info("[2/6] 获取净值数据...")
    universe_codes = [f["code"] for f in universe]
    all_codes = holding_codes + [c for c in universe_codes if c not in holding_codes]
    nav_data = fetcher.fetch_nav_batch(all_codes, config["cache"]["nav_history_days"])

    if use_estimates:
        today_str = datetime.now().strftime("%Y-%m-%d")
        logger.info("[2.5/6] 获取盘中实时估值...")
        estimates = fetcher.fetch_estimates(all_codes)
        est_count = 0
        for code, est in estimates.items():
            if code not in nav_data:
                continue
            last = nav_data[code][-1]
            if last["date"] == today_str and not last.get("_estimated"):
                continue
            nav_data[code].append({
                "date": today_str,
                "nav": est["estimate_nav"],
                "_estimated": True,
            })
            est_count += 1
        if est_count:
            logger.info("  已为 %d 只基金追加盘中估值数据点", est_count)

    logger.info("[3/6] 计算技术指标...")
    equity_codes = {f["code"] for f in universe
                    if f.get("type") in ("股票型", "混合型", "指数型", "QDII")}
    indicators = compute_indicators(
        nav_data, strategy, equity_codes, set(holding_codes)
    )

    logger.info("[4/6] 规则匹配...")
    rules_cfg = config["strategy"].get("rules")
    buy_top10 = select_buy_candidates(indicators, rules=rules_cfg)
    sell_top10 = select_sell_candidates(indicators, holding_codes, rules=rules_cfg)
    name_map = {f["code"]: f["name"] for f in universe}
    for item in buy_top10 + sell_top10:
        if item["code"] not in name_map:
            name_map[item["code"]] = fetcher.get_fund_name(item["code"])
        item["name"] = name_map.get(item["code"], item["code"])

    logger.info("[5/6] 分析持仓...")
    holdings_advice = analyze_holdings(holding_codes, indicators, strategy, rules=rules_cfg)
    for item in holdings_advice:
        if item["name"] == item["code"]:
            item["name"] = name_map.get(
                item["code"], fetcher.get_fund_name(item["code"])
            )
    buy_positions, hold_positions = compute_position_advice(
        indicators, nav_data, buy_top10, sell_top10,
        holdings_advice, target_vol=target_vol,
    )

    return buy_positions, sell_top10, hold_positions, indicators, estimates


def print_holdings_table(hold_positions):
    max_width = (
        max(display_width(p["name"]) for p in hold_positions)
        if hold_positions else 10
    )
    for p in hold_positions:
        name_padded = pad_to_width(p["name"], max_width)
        if p["pct"] > 0:
            pct_str = f"{p['pct']:.1%}"
            logger.info("  %s %s  %s %s", p["code"], name_padded, p["action"], pct_str)
        else:
            logger.info("  %s %s  %s", p["code"], name_padded, p["action"])


def diagnose_fund(config, fund_code):
    fetcher = DataFetcher(config)
    strategy = config["strategy"]
    nav_data = fetcher.fetch_nav_batch(
        [fund_code], config["cache"]["nav_history_days"]
    )

    if fund_code not in nav_data:
        logger.error("基金 %s 无数据", fund_code)
        return

    name = fetcher.get_fund_name(fund_code)
    logger.info("=== %s (%s) 诊断 ===", name, fund_code)

    indicators = compute_indicators(nav_data, strategy, {fund_code}, set())
    if fund_code not in indicators:
        logger.warning("无法计算指标（数据不足或异常）")
        return

    sig = indicators[fund_code]
    logger.info("净值: %.4f  |  MA200: %.4f  |  上方: %s",
                sig["current_nav"], sig["ma200"], sig["above_ma200"])
    logger.info("趋势强度: %.2f%%  |  波动率比: %.2f",
                sig["trend_strength"] * 100, sig["volatility_ratio"])
    logger.info("连跌: %d 天  |  连涨: %d 天  |  RSI: %.0f",
                sig["consecutive_declines"], sig["consecutive_rises"],
                sig.get("rsi_14", 50))
    logger.info("近月收益: %.2f%%  |  高点回撤: %.2f%%",
                sig["monthly_return"] * 100, sig["pullback_from_peak"] * 100)
    logger.info("滚动夏普: %.2f  |  最大回撤: %.2f%%",
                sig.get("rolling_sharpe", 0),
                sig.get("rolling_max_drawdown", 0) * 100)
    logger.info("最新净值日期: %s", sig["last_date"])

    action, reason = classify_holding(sig)
    logger.info("规则判定: %s — %s", action, reason)


def parse_args():
    parser = argparse.ArgumentParser(description="fund-metrics — 基金指标系统")
    parser.add_argument(
        "--mode", choices=["full", "indicator", "push"], default="full",
        help="运行模式: full=完整流程, indicator=仅指标, push=仅推送",
    )
    parser.add_argument("--fund", type=str, default=None, help="单只基金诊断")
    parser.add_argument("--no-push", action="store_true", help="不推送微信")
    parser.add_argument(
        "--no-estimate", action="store_true",
        help="不使用盘中实时估值",
    )
    parser.add_argument(
        "--refresh-universe", action="store_true",
        help="强制重新拉取全量基金数据并重建基金池（默认使用缓存）",
    )
    parser.add_argument(
        "--backtest", action="store_true",
        help="运行策略回测（基于历史净值数据）",
    )
    parser.add_argument(
        "--bt-start", type=str, default=None,
        help="回测起始日期 YYYY-MM-DD（默认：自动）",
    )
    parser.add_argument(
        "--bt-end", type=str, default=None,
        help="回测结束日期 YYYY-MM-DD（默认：昨天）",
    )
    parser.add_argument(
        "--bt-capital", type=float, default=None,
        help="回测初始资金（默认：100000）",
    )
    parser.add_argument(
        "--bt-report", action="store_true",
        help="回测时输出 CSV 报告文件",
    )
    return parser.parse_args()


def run_backtest(config, holding_codes, args):
    """执行策略回测。"""
    from backtest.engine import BacktestEngine
    from backtest.report import generate_report as bt_generate_report

    bt_config = config.get("backtest", {})
    initial_capital = bt_config.get("initial_capital", 100000)

    # 加载基金池缓存
    cache_path = os.path.join(os.path.dirname(__file__), "cache", "filtered_universe.json")
    if not os.path.exists(cache_path):
        logger.error("基金池缓存不存在，请先运行一次主流程生成缓存")
        sys.exit(1)

    with open(cache_path, "r", encoding="utf-8") as f:
        universe = json.load(f)
    logger.info("加载基金池: %d 只", len(universe))

    # 拉取净值数据
    fetcher = DataFetcher(config)
    universe_codes = [f["code"] for f in universe]
    all_codes = holding_codes + [c for c in universe_codes if c not in holding_codes]
    nav_data = fetcher.fetch_nav_batch(all_codes, config["cache"]["nav_history_days"])
    logger.info("净值数据: %d 只基金", len(nav_data))

    # 解析日期
    start_date = None
    end_date = None
    if args.bt_start:
        start_date = datetime.strptime(args.bt_start, "%Y-%m-%d")
    if args.bt_end:
        end_date = datetime.strptime(args.bt_end, "%Y-%m-%d")
    if args.bt_capital:
        initial_capital = args.bt_capital

    # 执行回测
    engine = BacktestEngine(
        config=config,
        holding_codes=holding_codes,
        nav_data=nav_data,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
    )
    result = engine.run()

    # 输出报告
    report = bt_generate_report(result)
    print(report)
    logger.info("CSV 报告已保存到 cache/backtest_daily.csv 和 cache/backtest_trades.csv")


def main():
    load_env()
    args = parse_args()

    if args.fund:
        config = load_config()
        diagnose_fund(config, args.fund)
        return

    config = load_config()
    holding_codes = load_holdings()

    if args.backtest:
        run_backtest(config, holding_codes, args)
        return

    logger.info("=== fund-metrics ===")
    logger.info("运行时间: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("持仓基金: %d 只", len(holding_codes))

    use_estimates = not args.no_estimate

    if args.mode in ("full", "indicator"):
        try:
            buy_positions, sell_top10, hold_positions, indicators, estimates = run_pipeline(
                config, holding_codes, use_estimates=use_estimates,
                refresh_universe=args.refresh_universe,
            )
            print_holdings_table(hold_positions)
        except Exception:
            logger.exception("流水线执行失败")
            if args.mode == "indicator":
                sys.exit(1)
            logger.error("流水线中断，终止运行")
            sys.exit(1)

    if args.mode in ("full", "push"):
        report = generate_report(buy_positions, sell_top10, hold_positions,
                                indicators, estimates)
        print(report)

        if args.no_push:
            logger.info("--no-push，跳过微信推送")
            return

        token = get_pushplus_token(config)
        if not token:
            logger.warning(
                "PushPlus token 未配置，跳过推送。"
                "请在 .env 中设置 PUSHPLUS_TOKEN"
            )
            return

        try:
            topic = config.get("pushplus", {}).get("topic", "")
            success = send_wechat("每日基金指标", report, token, topic)
            if success:
                logger.info("已推送到微信")
            else:
                logger.error("微信推送失败")
        except Exception:
            logger.exception("推送异常")

    logger.info("=== 完成 ===")


if __name__ == "__main__":
    main()