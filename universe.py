"""基金池筛选：类型过滤 → A/C去重 → 规模/成立时间过滤。

不再评分和排序——所有通过过滤的基金全部进入后续流程。
"""

import re
from datetime import datetime, timedelta


def filter_universe(fetcher, config):
    universe_config = config["universe"]
    include_types = universe_config["include_types"]
    exclude_keywords = universe_config["exclude_keywords"]
    exclude_patterns = universe_config["exclude_name_patterns"]
    min_aum_yi = universe_config.get("min_aum_yi", 5)
    min_fund_age_days = universe_config.get("min_fund_age_days", 365)

    # Stage 1: 类型/关键词过滤
    print("  加载全量基金列表...")
    fund_list = fetcher.fetch_fund_list()
    print(f"  全量基金: {len(fund_list)} 只")

    type_filtered = set()
    name_map = {}
    type_map = {}
    for fund in fund_list:
        fund_type = str(fund.get("基金类型", ""))
        fund_name = str(fund.get("基金简称", ""))
        fund_code = str(fund.get("基金代码", "")).strip()

        if not any(t in fund_type for t in include_types):
            continue
        if any(kw in fund_name for kw in exclude_keywords):
            continue
        if any(p in fund_name for p in exclude_patterns):
            continue
        if "货币" in fund_type:
            continue

        type_filtered.add(fund_code)
        name_map[fund_code] = fund_name
        type_map[fund_code] = fund_type

    print(f"  类型过滤后: {len(type_filtered)} 只")

    # Stage 2: A/C 份额去重（基于基金列表名称）
    # 构建 {code: {code, name, type}} 然后去重
    entries = [
        {"code": c, "name": name_map[c], "type": type_map.get(c, "")}
        for c in type_filtered
    ]
    entries = _dedup_share_classes(entries)
    print(f"  A/C去重后: {len(entries)} 只")

    # Stage 2.5: 同公司同类策略去重（如汇安泓阳 vs 汇安润阳）
    entries = _dedup_family_clones(entries)
    print(f"  同类去重后: {len(entries)} 只")

    # Stage 3: 获取基金基本信息（规模 + 成立时间）
    all_codes = [e["code"] for e in entries]
    info_data = fetcher.fetch_fund_info_batch(all_codes)

    # 过滤
    age_cutoff = datetime.now() - timedelta(days=min_fund_age_days)
    age_excluded = 0
    aum_excluded = 0
    result = []

    for item in entries:
        code = item["code"]
        info = info_data.get(code)

        # 解析规模
        aum_yi = None
        if info and info.get("_scale_yi") is not None:
            try:
                aum_yi = float(info["_scale_yi"])
            except (ValueError, TypeError):
                pass

        # 规模过滤
        if aum_yi is not None and aum_yi < min_aum_yi:
            aum_excluded += 1
            continue

        # 成立时间过滤
        inception_str = ""
        if info:
            inception_str = info.get("成立时间", "")

        if inception_str:
            try:
                inception = datetime.strptime(inception_str[:10], "%Y-%m-%d")
                if inception > age_cutoff:
                    age_excluded += 1
                    continue
            except ValueError:
                pass

        item["aum_yi"] = aum_yi
        result.append(item)

    if age_excluded:
        print(f"  成立时间不足过滤: {age_excluded} 只")
    if aum_excluded:
        print(f"  规模不足过滤 (<{min_aum_yi}亿): {aum_excluded} 只")

    print(f"  最终基金池: {len(result)} 只")
    return result


def _dedup_share_classes(funds):
    """同一基金的 A/C 份额只保留 C 类"""

    def _base_name(name):
        return re.sub(r"[A-Z]$", "", name.strip())

    base_map = {}
    for item in funds:
        name = item["name"]
        base = _base_name(name)
        if base not in base_map:
            base_map[base] = []
        base_map[base].append(item)

    result = []
    for base, group in base_map.items():
        if len(group) == 1:
            result.append(group[0])
        else:
            c_class = [f for f in group if f["name"].rstrip().endswith("C")]
            if c_class:
                result.append(c_class[0])
            else:
                result.append(group[0])
    return result


def _dedup_family_clones(funds):
    """同公司同类策略去重：同一公司内，名称高度相似（>85%）的合并。

    例如「汇安泓阳三年持有期混合」和「汇安润阳三年持有期混合C」
    去 A/C 后缀后仅「泓阳」「润阳」两字不同，应合并。

    用 SequenceMatcher 比较去掉公司和份额后缀后的名称，
    避免误杀差异较大的不同策略基金。
    """
    import re
    from difflib import SequenceMatcher

    _COMPANIES = [
        "摩根士丹利", "国海富兰克林", "华泰柏瑞", "民生加银",
        "汇添富", "景顺长城",
        "易方达", "华夏", "广发", "天弘", "博时", "汇安", "博道",
        "富国", "中银", "安信", "泰康", "诺安", "招商", "南方",
        "嘉实", "工银", "鹏华", "华安", "银华", "兴全", "万家",
        "国泰", "华宝", "前海", "建信", "中欧", "交银", "东方",
        "长城", "金鹰", "融通", "国联", "中海", "申万", "中信",
        "浦银", "平安", "摩根", "西部", "鑫元", "兴业", "大成",
        "长信", "永赢", "上银", "浙商", "财通", "淳厚", "创金",
        "汇丰", "光大", "海富通", "银河", "德邦", "红土", "恒越",
    ]
    _COMPANIES.sort(key=len, reverse=True)

    def _extract_company(name):
        for c in _COMPANIES:
            if name.startswith(c):
                return c
        return name[:2]

    def _strategy_tail(name):
        """去掉公司前缀和份额后缀后的策略描述。"""
        name = re.sub(r"[A-E]$", "", name.strip())
        company = _extract_company(name)
        return name[len(company):]

    # 按公司分组
    by_company = {}
    for item in funds:
        company = _extract_company(item["name"])
        if company not in by_company:
            by_company[company] = []
        by_company[company].append(item)

    result = []
    dup_count = 0
    for company, group in by_company.items():
        if len(group) == 1:
            result.extend(group)
            continue

        # 用贪心聚类：相似的合并到一组，保留每组的第一个
        kept = []
        merged = set()
        tails = [_strategy_tail(item["name"]) for item in group]

        for i in range(len(group)):
            if i in merged:
                continue
            kept.append(group[i])
            for j in range(i + 1, len(group)):
                if j in merged:
                    continue
                sim = SequenceMatcher(None, tails[i], tails[j]).ratio()
                if sim > 0.85:
                    merged.add(j)
                    dup_count += 1

        result.extend(kept)

    if dup_count:
        print(f"    同类策略合并: {dup_count} 只")
    return result