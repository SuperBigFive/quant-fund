# fund-metrics

A股公募基金量化分析系统。每日自动筛选基金池、获取净值与盘中估值、计算技术指标、规则匹配买卖信号、生成持仓建议、推送微信报告。内置回测引擎，支持策略参数化与历史验证。

## 特性

- **全市场覆盖**：从 27,000+ 只公募基金中筛选，支持股票型、混合型、债券型、QDII、ETF 等
- **规则驱动 + 参数可配**：8 条买入规则 + 8 条卖出规则，所有阈值在 `config.yaml` 中配置，无需改代码即可调参
- **学术理论支撑**：仓位计算基于 Half-Kelly、Vol Targeting（Moreira & Muir 2017）、Drawdown Constraint（Grossman & Zhou 1993）
- **盘中估值**：集成天天基金实时估值，盘前即可预判当日走势
- **微信推送**：通过 PushPlus 每日自动推送完整报告
- **基金池缓存**：首次筛选后缓存，后续秒级加载
- **策略回测**：逐日历史回测，无未来信息泄露，输出收益指标 + 规则有效性 + 交易记录 CSV

## 快速开始

### 环境要求

- Python 3.8+
- Windows / Linux / macOS
- 网络可访问东方财富 API 和天天基金

### 安装

```bash
git clone https://github.com/SuperBigFive/quant-fund.git
cd quant-fund
pip install -r requirements.txt
```

### 配置

```bash
# 1. 创建环境变量文件
echo "PUSHPLUS_TOKEN=你的token" > .env

# 2. 编辑持仓列表（每行一个 6 位基金代码）
# 编辑 holdings.txt
```

PushPlus token 在 [pushplus.plus](http://www.pushplus.plus) 免费获取。

### 运行

```bash
# 日常运行
python main.py                     # 完整流程（估值 + 推送）
python main.py --no-estimate       # 跳过盘中估值（盘后使用）
python main.py --no-push           # 不推送微信
python main.py --refresh-universe  # 强制重建基金池
python main.py --fund 000001       # 单只基金诊断

# 策略回测
python main.py --backtest                          # 全量回测（自动区间）
python main.py --backtest --bt-start 2024-06-01    # 指定起始日期
python main.py --backtest --bt-end 2026-06-18      # 指定结束日期
python main.py --backtest --bt-capital 500000      # 自定义初始资金
```

## 流水线架构

```
                 ┌─────────────────┐
                 │  1. 基金池筛选   │  universe.py
                 │  类型/关键词过滤  │  规模≥5亿 成立≥1年
                 │  同类去重        │
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │  2. 净值获取     │  data_fetcher.py
                 │  东方财富 API    │  串行拉取 + 日缓存
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │  2.5 盘中估值    │  天天基金 fundgz
                 │  追加今日数据点   │  并行获取 + 重试
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │  3. 技术指标     │  indicators.py
                 │  MA200/RSI/夏普  │  异常基金自动排除
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │  4. 规则匹配     │  scorer.py
                 │  8买 + 8卖规则   │  买入 TOP 10
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │  5. 仓位计算     │  position_advisor.py
                 │  Kelly + Vol + DD│  单只≤5% 总仓≤80%
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │  6. 报告推送     │  reporter.py
                 │  PushPlus → 微信 │  持仓建议 + 买入推荐
                 └─────────────────┘
```

## 策略回测

独立的回测引擎（`backtest/`），用于策略开发与参数优化，不参与日常运行。

### 架构

```
python main.py --backtest
       │
       ▼
┌──────────────────────┐
│  BacktestEngine      │  逐日推进
│  ├─ 数据截断（无未来）│  每天只用当日及之前的数据
│  ├─ 指标计算         │  复用 indicators.py
│  ├─ 信号生成         │  复用 scorer.py
│  ├─ 交易执行         │  Portfolio 模拟账户
│  └─ 结果汇总         │  收益 + 规则有效性 + CSV
└──────────────────────┘
```

### 输出

- **控制台**：收益指标（年化/夏普/最大回撤）、规则胜率、最佳/最差基金
- **CSV 文件**：`cache/backtest_daily.csv`（日净值）、`cache/backtest_trades.csv`（逐笔交易）

## 买卖规则

### 买入（命中任一即入围，按反弹潜力排序取 TOP 10）

所有阈值可在 `config.yaml` 的 `strategy.rules.buy` 段调整。

| 规则 | 条件 | 可配参数 |
|------|------|----------|
| P1 黄金回调 | MA200 上方 ∧ 连跌 N 天 ∧ RSI < M | `decline_min/max`, `rsi_max` |
| P2 超跌反弹 | MA200 上方 ∧ RSI < M ∧ 60 日回撤 > N% | `rsi_max`, `max_drawdown_min` |
| P3 趋势回调 | MA200 上方 ∧ 连跌 N 天 ∧ 夏普 > M | `decline_min/max`, `sharpe_min` |
| P4 低波抄底 | MA200 上方 ∧ RSI < M ∧ 波动率 < 历史 | `rsi_max`, `vol_ratio_max` |
| P5 深度价值 | MA200 上方 ∧ 60 日回撤 > N% ∧ RSI < M | `max_drawdown_min`, `rsi_max` |
| P6 优质回调 | MA200 上方 ∧ 夏普 > M ∧ 连跌 N 天 | `sharpe_min`, `decline_min/max` |
| P7 波动收缩 | MA200 上方 ∧ 波动率 < M × ∧ 高点回撤 > N% | `vol_ratio_max`, `pullback_min` |
| P8 强趋势回调 | MA200 上方 ∧ 趋势强度 > N% ∧ 连跌 ≥ M 天 | `trend_strength_min`, `decline_min` |

### 卖出（命中任一即触发）

所有阈值可在 `config.yaml` 的 `strategy.rules.sell` 段调整。

| 规则 | 条件 | 操作 | 可配参数 |
|------|------|------|----------|
| S1 趋势反转 | 跌破 MA200 | 清仓 | `below_ma_clear` |
| S2 加速恶化 | 60 日回撤 > N% ∧ 波动率 > M× | 减仓 | `max_drawdown_min`, `vol_ratio_min` |
| S3 过热止盈 | 连涨 ≥ N 天 ∧ RSI > M | 减仓 | `rise_min`, `rsi_min` |
| S4 风险恶化 | 夏普 < M ∧ 月度亏损 < N% | 减仓 | `sharpe_max`, `monthly_return_max` |
| S5 高位回撤 | MA200 上方 ∧ 高点回撤 > N% ∧ 连跌 ≥ M 天 | 减仓 | `pullback_min`, `decline_min` |
| S6 质量崩塌 | 夏普 < M | 清仓 | `sharpe_max` |
| S7 波动爆炸 | 波动率 > M× 历史 | 减仓 | `vol_ratio_min` |
| S8 持续阴跌 | MA200 上方 ∧ 连跌 ≥ N 天 | 减仓 | `decline_min` |

### 仓位计算

| 场景 | 方法 | 理论来源 |
|------|------|----------|
| 买入 | Half-Kelly × 波动率调整 × 回撤缩放 | Kelly (1956), Moreira & Muir (2017) |
| 卖出 | max(Vol Targeting, DD Constraint) | Moreira & Muir (2017), Grossman & Zhou (1993) |
| 约束 | 单只 ≤ 5%，总仓 ≤ 80% | — |

## 技术指标

每只基金计算 14 个指标：

| 类别 | 指标 | 说明 |
|------|------|------|
| 趋势 | MA200、趋势强度、是否站上 MA200 | 200 日均线系统 |
| 动量 | RSI(14)、单日涨跌、近月收益 | 相对强弱与短期收益 |
| 回调 | 连跌/连涨天数、累计跌幅、高点回撤 | 近期调整幅度 |
| 风险 | 波动率比、60 日最大回撤、滚动夏普 | 风险度量 |
| 估值 | 盘中实时估值（天天基金） | 当日预判 |

## 配置说明

编辑 `config.yaml`：

```yaml
strategy:
  # 基础参数
  ma_period: 200
  target_annual_vol: 0.10

  # 规则参数（可独立调整每条规则的阈值）
  rules:
    buy:
      P1_golden_pullback:
        rsi_max: 40
        decline_min: 3
        decline_max: 5
      # ... 更多规则
    sell:
      S4_risk_decay:
        sharpe_max: 0.0
        monthly_return_max: -0.05
      # ... 更多规则

universe:
  min_aum_yi: 5             # 排除规模 < 5 亿的小基金
  min_fund_age_days: 365    # 排除成立 < 1 年的新基金

backtest:
  initial_capital: 100000   # 回测初始资金
```

## 文件结构

```
quant-fund/
├── main.py               # 主入口 + CLI（运行/回测/诊断）
├── config.yaml           # 策略参数 + 基金池配置 + 规则阈值
├── requirements.txt      # Python 依赖
├── universe.py           # 基金池筛选（类型/规模/年龄/去重）
├── data_fetcher.py       # 数据获取（净值 + 估值 + 基本信息）
├── indicators.py         # 技术指标计算
├── scorer.py             # 买卖规则匹配（参数化）
├── holdings.py           # 持仓分析
├── position_advisor.py   # 仓位计算（Kelly + Vol + DD）
├── reporter.py           # 报告生成
├── notifier.py           # PushPlus 微信推送
├── estimate.py           # 独立估值工具
├── backtest/             # 回测引擎
│   ├── __init__.py
│   ├── engine.py         # 逐日回测引擎（无未来信息）
│   ├── portfolio.py      # 模拟账户 + 费率模型
│   └── report.py         # 回测报告生成 + CSV 导出
├── .env                  # 密钥（不入版本控制）
├── holdings.txt          # 持仓代码（不入版本控制）
└── cache/                # 净值缓存 + 基金池缓存 + 回测结果（不入版本控制）
```

## 注意事项

- 首次运行需拉取全量基金数据 + 基本信息，耗时约 5-8 分钟。后续通过缓存秒级启动
- 债券型和部分 QDII 无盘中估值，报告自动回退到前日净值
- 东方财富 API 对并发敏感，净值拉取为串行模式（约 0.3s/只），全量约 15-20 分钟
- 基金池缓存不自动过期，需 `--refresh-universe` 手动触发重建
- 回测引擎为离线开发工具，不参与每日运行管道

## License

MIT