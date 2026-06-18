import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import akshare as ak
import requests
from tqdm import tqdm

logger = logging.getLogger(__name__)


class DataFetcher:
    def __init__(self, config):
        self.config = config
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.request_interval = 0.3

    def _cache_path(self, filename):
        return os.path.join(self.cache_dir, filename)

    def _is_cache_valid(self, filepath, max_age_days):
        if not os.path.exists(filepath):
            return False
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        return (datetime.now() - mtime).days < max_age_days

    def fetch_fund_list(self):
        cache_file = self._cache_path("fund_list.json")
        refresh_days = self.config["cache"]["universe_refresh_days"]

        if self._is_cache_valid(cache_file, refresh_days):
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

        print("  正在获取全量基金列表（首次较慢）...")
        df = ak.fund_name_em()
        records = df.to_dict("records")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
        return records

    def fetch_fund_nav(self, code, days=300):
        cache_file = self._cache_path(f"nav_{code}.json")
        today_str = datetime.now().strftime("%Y-%m-%d")

        # 今天已经拉取过 → 直接用缓存（不论数据最新到哪天）
        if os.path.exists(cache_file):
            mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
            if mtime.strftime("%Y-%m-%d") == today_str:
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

        # 今天还没拉取过 → 调用 API
        data = self._try_fetch_nav(code, days)
        if data is not None:
            return data

        # API 失败时降级返回缓存（即使旧）
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        return None

    def _try_fetch_nav(self, code, days):
        try:
            time.sleep(self.request_interval)
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is None or df.empty:
                return None

            records = []
            for _, row in df.iterrows():
                date_str = row.iloc[0]
                if isinstance(date_str, datetime):
                    date_str = date_str.strftime("%Y-%m-%d")
                else:
                    date_str = str(date_str)[:10]
                records.append({
                    "date": date_str,
                    "nav": float(row.iloc[1])
                })

            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            records = [r for r in records if r["date"] >= cutoff]
            records.sort(key=lambda x: x["date"])

            cache_file = self._cache_path(f"nav_{code}.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False)
            return records

        except Exception:
            return None

    def fetch_nav_batch(self, codes, days=300):
        """串行获取多只基金净值（东方财富 API 不支持并发）。"""
        results = {}
        for code in tqdm(codes, desc="  获取净值", unit="只"):
            data = self.fetch_fund_nav(code, days)
            if data and len(data) >= 30:
                results[code] = data
            time.sleep(0.05)
        return results

    @staticmethod
    def _fetch_single_estimate(code, max_retries=2):
        """获取单只基金的盘中实时估值（天天基金接口）。

        带重试机制，应对 fundgz 瞬时不可用。
        返回值:
            dict  — 成功获取估值数据
            None  — fundgz 明确无数据（如部分 QDII），无需重试
        """
        url = f"http://fundgz.1234567.com.cn/js/{code}.js"
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(url, timeout=5)
                resp.encoding = "utf-8"
                text = resp.text.strip()

                # fundgz 对不支持估值的基金返回空回调 jsonpgz();
                if text == "jsonpgz();" or len(text) <= 12:
                    return None  # 明确无数据，不重试

                json_str = text[text.index("{") : text.rindex("}") + 1]
                data = json.loads(json_str)
                return {
                    "code": data.get("fundcode", code),
                    "name": data.get("name", code),
                    "nav_yesterday": float(data.get("dwjz", 0)),
                    "estimate_nav": float(data.get("gsz", 0)),
                    "estimate_change": float(data.get("gszzl", 0)),
                    "estimate_time": data.get("gztime", ""),
                }
            except (ValueError, KeyError):
                # 解析失败 → 不重试
                logger.debug("fundgz 解析失败 %s: %.80s", code, text)
                return None
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue

        logger.warning("fundgz 获取失败 %s (重试%d次): %s", code, max_retries, last_error)
        return None

    def fetch_estimates(self, codes, max_workers=4):
        """并行获取多只基金的盘中实时估值。

        Returns:
            dict: {code: estimate_dict or None}, 只包含成功获取的条目
        """
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._fetch_single_estimate, code): code for code in codes}
            for future in tqdm(
                as_completed(futures), total=len(futures), desc="  获取估值", unit="只"
            ):
                code = futures[future]
                try:
                    est = future.result()
                    if est and est.get("estimate_nav", 0) > 0:
                        results[code] = est
                except Exception:
                    pass
        return results

    def fetch_fund_info_basic(self, code):
        """获取单只基金基本信息（成立时间 + 最新规模），永久缓存。

        使用雪球接口 ak.fund_individual_basic_info_xq()，
        一次调用返回 14 个字段含 成立时间、最新规模。

        Returns:
            dict or None: {"成立时间": "2018-04-24", "最新规模": 60.09, "基金代码": "005918", ...}
        """
        cache_file = self._cache_path(f"info_{code}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        time.sleep(self.request_interval)
        try:
            df = ak.fund_individual_basic_info_xq(symbol=code)
            if df is None or df.empty:
                return None

            # 雪球接口返回两列: item (字段名), value (字段值)
            record = {}
            for _, row in df.iterrows():
                key = str(row.iloc[0]).strip()
                val = str(row.iloc[1]).strip() if len(row) > 1 else ""
                record[key] = val

            # 解析规模: "60.09亿" → 60.09, 或 "5000万" → 0.5
            scale_str = record.get("最新规模", "")
            if scale_str:
                try:
                    if "亿" in scale_str:
                        record["_scale_yi"] = float(scale_str.replace("亿", ""))
                    elif "万" in scale_str:
                        record["_scale_yi"] = float(scale_str.replace("万", "")) / 10000
                except ValueError:
                    pass
            else:
                record["_scale_yi"] = None

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False)
            return record
        except Exception:
            return None

    def fetch_fund_info_batch(self, codes, max_workers=8):
        """批量获取多只基金基本信息（并行，永久缓存）。

        用于首次填充基金池的规模和成立时间数据。
        缓存命中直接返回，不发起网络请求。

        Returns:
            dict: {code: info_dict or None}
        """
        results = {}
        uncached = []

        # 先查缓存
        for code in codes:
            cache_file = self._cache_path(f"info_{code}.json")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        results[code] = json.load(f)
                except (json.JSONDecodeError, OSError):
                    uncached.append(code)
            else:
                uncached.append(code)

        if not uncached:
            return results

        print(f"  获取基金基本信息: {len(uncached)} 只待拉取...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.fetch_fund_info_basic, code): code
                       for code in uncached}
            for future in tqdm(
                as_completed(futures), total=len(futures),
                desc="  基本信息", unit="只"
            ):
                code = futures[future]
                try:
                    info = future.result()
                    results[code] = info
                except Exception:
                    results[code] = None
        return results

    def get_fund_name(self, code):
        fund_list = self.fetch_fund_list()
        for fund in fund_list:
            fund_code = str(fund.get("基金代码", "")).strip()
            if fund_code == code:
                return fund.get("基金简称", code)
        return code

    def fetch_fund_rank(self):
        print("  正在获取基金排名数据...")
        all_records = []
        for fund_type in ["股票型", "混合型", "债券型", "指数型", "QDII"]:
            try:
                time.sleep(0.5)
                df = ak.fund_open_fund_rank_em(symbol=fund_type)
                if df is not None and not df.empty:
                    records = df.to_dict("records")
                    for r in records:
                        r["_query_type"] = fund_type
                    all_records.extend(records)
                    print(f"    {fund_type}: {len(records)} 只")
            except Exception as e:
                print(f"    {fund_type}: 获取失败 ({e})")
        return all_records