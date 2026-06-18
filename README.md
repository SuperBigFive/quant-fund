# fund-metrics

基于 akshare 数据的 A 股公募基金量化分析系统。每日运行流水线——筛选基金池、获取净值 + 盘中估值、计算技术指标、评分排名、仓位分析、微信推送。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 PushPlus token（用于微信推送）
cp .env.example .env
# 编辑 .env，填入你的 PushPlus token

# 3. 编辑持仓列表
# 修改 holdings.txt，每行一个 6 位基金代码

# 4. 运行
python main.py                  # 完整流程（含盘中估值） + 推送
python main.py --no-estimate    # 不使用盘中估值（盘后净值已发布时）
python main.py --no-push        # 仅指标，不推送
python main.py --fund 000001    # 单基金诊断
```

## 流水线

| 步骤 | 模块 | 功能 |
|------|------|------|
| 1. 筛选基金池 | `universe.py` | 类型/关键词过滤 → 排名评分 → A/C 份额去重 |
| 2. 获取净值 | `data_fetcher.py` | akshare API + 本地 JSON 缓存 |
| 2.5 盘中估值 | `data_fetcher.py` | 天天基金实时估值接口，追加"今天"估算数据点 |
| 3. 计算指标 | `indicators.py` | MA200 趋势、波动率比、连涨连跌、异常检测 |
| 4. 评分排名 | `scorer.py` | 买入/卖出双评分体系，TOP 10 |
| 5. 仓位分析 | `holdings.py` + `position_advisor.py` | 半凯利公式 + 波动率倒数 + 回撤缩放 |
| 6. 报告推送 | `reporter.py` + `notifier.py` | PushPlus → 微信 |

## 盘中估值

默认启用。通过天天基金接口 (`fundgz.1234567.com.cn`) 并行获取所有基金的盘中估算净值，将其作为"今天"的数据点追加到历史净值序列末尾。后续的 MA200 趋势、连涨连跌、买卖评分等指标计算自然就会把当天的实时涨跌考虑进去。

- **适用场景**：盘中运行（如 14:45），在收盘前拿到当日走势
- **跳过估值**：`--no-estimate`（盘后净值已发布，无需估算）
- **注意**：债券型基金和 QDII 通常无盘中估值

## 策略概要

系统使用**规则匹配**（非打分求和）做买卖决策，卖出比例基于学术文献。

### 买入规则（命中任一入围）

| 规则 | 条件 |
|------|------|
| P1 黄金回调 | MA200 上方 ∧ 连跌 3-5 天 ∧ RSI < 40 |
| P2 超跌反弹 | MA200 上方 ∧ RSI < 30 ∧ 60 日回撤 > 8% |
| P3 趋势回调 | MA200 上方 ∧ 连跌 3-5 天 ∧ 夏普 > 0.5 |
| P4 低波抄底 | MA200 上方 ∧ RSI < 35 ∧ 波动率 < 历史均值 |
| P5 深度价值 | MA200 上方 ∧ 60 日回撤 > 12% ∧ RSI < 35 |
| P6 优质回调 | MA200 上方 ∧ 夏普 > 1.0 ∧ 连跌 2-5 天 |
| P7 波动收缩 | MA200 上方 ∧ 波动率 < 0.6x ∧ 高点回撤 > 3% |
| P8 强趋势回调 | MA200 上方 ∧ 趋势强度 > 5% ∧ 连跌 ≥ 2 天 |

### 卖出规则（命中任一触发）

| 规则 | 条件 |
|------|------|
| S1 趋势反转 | 跌破 MA200 |
| S2 加速恶化 | 60 日回撤 > 10% ∧ 波动率 > 1.5x 历史 |
| S3 过热止盈 | 连涨 ≥ 5 天 ∧ RSI > 70 |
| S4 风险恶化 | 夏普 < 0 ∧ 月度亏损 < -5% |
| S5 高位回撤 | MA200 上方 ∧ 高点回撤 > 5% ∧ 连跌 ≥ 4 天 |
| S6 质量崩塌 | 夏普 < -1.0 |
| S7 波动爆炸 | 波动率 > 2.0x 历史 |
| S8 持续阴跌 | MA200 上方 ∧ 连跌 ≥ 6 天 |

### 卖出比例（有理论支撑）

| 场景 | 方法 | 文献 |
|------|------|------|
| 止损 | 清仓（买点逻辑失效） | — |
| 风险减仓 | max(Vol Targeting, DD Constraint) | Moreira & Muir (2017) + Grossman & Zhou (1993) |
| 止盈减仓 | Vol Targeting on recent vol | Moreira & Muir (2017) |

### 仓位

- **买入**：半凯利公式 + 波动率调整 + 回撤缩放（无人工评分乘数）
- 单只 ≤ 5%，补仓总额 ≤ 20%，总仓位 ≤ 80%

## 技术指标

每只基金输出 14 个字段：

| 类别 | 指标 | 说明 |
|------|------|------|
| 趋势 | MA200 | 200 日均线 |
| | Trend Strength | 偏离 MA200 的百分比 |
| | Above MA200 | 是否在 MA200 上方 |
| 回调 | Consecutive Declines | 连续下跌天数 |
| | Consecutive Rises | 连续上涨天数 |
| | Recent Decline | 本轮连跌累计跌幅 |
| | Pullback from Peak | 距 20 日高点回撤 |
| 收益 | Monthly Return | 近月收益 |
| 风险 | Volatility Ratio | 近期/历史波动比 |
| | Rolling Max Drawdown | 60 日最大回撤 |
| 经典 | RSI(14) | 相对强弱 (Wilder, 1978) |
| | Rolling Sharpe | 60 日滚动夏普 (Sharpe, 1966) |
| 元数据 | Current NAV | 最新净值 |
| | Last Date | 最新净值日期 |

## 定时运行

建议配置 cron，每个交易日 14:45 自动运行：

```bash
# 编辑 crontab
crontab -e

# 添加（仅交易日，WSL 中）
45 14 * * 1-5 cd /path/to/fund-metrics && /path/to/venv/bin/python3 main.py
```

## 辅助工具

```bash
python estimate.py              # 查看所有持仓盘中估值
python estimate.py 000001       # 单只基金估值
```

## 文件说明

```
fund-metrics/
├── main.py               # 主入口（含 CLI）
├── config.yaml           # 策略参数配置
├── .env                  # 密钥（PUSHPLUS_TOKEN），不入版本控制
├── holdings.txt          # 持仓基金代码列表
├── data_fetcher.py       # 数据获取（akshare + 缓存 + 估值）
├── universe.py           # 基金池筛选
├── indicators.py         # 技术指标计算（MA200、波动率等）
├── scorer.py             # 买卖评分
├── holdings.py           # 持仓分析
├── position_advisor.py   # 仓位计算（凯利公式）
├── reporter.py           # 报告生成
├── notifier.py           # PushPlus 微信推送
├── estimate.py           # 盘中估值工具（独立使用）
├── cache/                # 净值缓存（JSON）
└── requirements.txt      # Python 依赖
```

## 缓存策略

- 基金列表：1 天刷新（变化极少，几乎不触发）
- 基金排名：无缓存，每次实时拉取（~3 秒）
- 净值数据：每天首次运行时拉取最新，当天后续运行直接复用缓存

## 注意事项

- 首次运行需拉取全量基金数据，耗时 5-10 分钟
- 债券型基金和 QDII 无盘中估值
- PushPlus token 需从 [pushplus.plus](http://www.pushplus.plus) 获取