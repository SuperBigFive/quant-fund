"""基金实时估值工具 — 查看持仓基金的盘中估算净值。

用法:
    python estimate.py              # 查看所有持仓估值
    python estimate.py 000001       # 查看单只基金估值
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests


def load_holdings():
    """与 main.py 共享的持仓加载逻辑。"""
    holdings_path = os.path.join(os.path.dirname(__file__), "holdings.txt")
    codes = []
    with open(holdings_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                codes.append(line)
    return codes


def get_realtime_estimate(code):
    """通过天天基金实时估值接口获取盘中估算数据。"""
    url = f"http://fundgz.1234567.com.cn/js/{code}.js"
    try:
        resp = requests.get(url, timeout=5)
        resp.encoding = "utf-8"
        text = resp.text
        json_str = text[text.index("{") : text.rindex("}") + 1]
        data = json.loads(json_str)
        return {
            "code": data.get("fundcode", code),
            "name": data.get("name", code),
            "nav_yesterday": float(data.get("dwjz", 0)),
            "estimate_nav": float(data.get("gsz", 0)),
            "estimate_change": float(data.get("gszzl", 0)),
            "estimate_time": data.get("gztime", ""),
            "nav_date": data.get("jzrq", ""),
        }
    except Exception:
        return None


def fetch_all_parallel(codes, max_workers=4):
    """并行获取所有基金的实时估值。"""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(get_realtime_estimate, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception:
                results[code] = None
    # 按原始顺序排列
    return [results.get(code) for code in codes]


def main():
    if len(sys.argv) > 1:
        codes = [sys.argv[1]]
    else:
        codes = load_holdings()

    if not codes:
        print("没有找到持仓基金代码，请检查 holdings.txt")
        return

    print("=== 基金实时估值 ===")
    print(f"查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"查询基金: {len(codes)} 只")
    print()
    print(f"{'代码':<8} {'名称':<28} {'昨日净值':<10} {'实时估值':<10} {'估算涨跌':<10} {'估值时间'}")
    print("-" * 90)

    if len(codes) == 1:
        info = get_realtime_estimate(codes[0])
        results = [info]
    else:
        results = fetch_all_parallel(codes)

    for info in results:
        if info:
            chg = info["estimate_change"]
            chg_str = f"{chg:+.2f}%"
            print(
                f"{info['code']:<8} {info['name']:<28} "
                f"{info['nav_yesterday']:<10.4f} "
                f"{info['estimate_nav']:<10.4f} "
                f"{chg_str:<10} "
                f"{info['estimate_time']}"
            )
        else:
            print(f"{info['code'] if info else '?' :<8} {'(无估值数据，可能为债基/QDII)':<28}")

    print()
    print("提示: 实时估值基于基金持仓股票行情估算，仅供参考")
    print("      债券型基金和QDII基金通常无盘中估值")


if __name__ == "__main__":
    main()