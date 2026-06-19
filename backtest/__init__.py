"""backtest — 基金策略回测系统。

用法:
    from backtest import BacktestEngine
    engine = BacktestEngine(config, holding_codes, nav_data, universe)
    report = engine.run()
"""

from .portfolio import Portfolio, Trade, DailySnapshot
from .engine import BacktestEngine
from .report import generate_report