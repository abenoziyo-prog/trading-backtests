"""Performance statistics for daily return series."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns).cumprod()
    return float((equity / equity.cummax() - 1).min())


def summary(returns: pd.Series, position: pd.Series | None = None, rf: float = 0.0) -> dict:
    """Headline stats. rf is the annual risk-free rate used for Sharpe/Sortino."""
    returns = returns.dropna()
    n = len(returns)
    if n == 0:
        return {}
    years = n / TRADING_DAYS
    total = float((1 + returns).prod() - 1)
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    excess = returns - rf / TRADING_DAYS
    vol = float(returns.std() * np.sqrt(TRADING_DAYS))
    downside = float(np.sqrt((np.minimum(excess, 0) ** 2).mean()) * np.sqrt(TRADING_DAYS))
    mdd = max_drawdown(returns)

    stats = {
        "start": returns.index[0].date(),
        "end": returns.index[-1].date(),
        "total_return": total,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": float(excess.mean() * TRADING_DAYS / vol) if vol > 0 else np.nan,
        "sortino": float(excess.mean() * TRADING_DAYS / downside) if downside > 0 else np.nan,
        "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "best_day": float(returns.max()),
        "worst_day": float(returns.min()),
    }
    if position is not None:
        position = position.reindex(returns.index).fillna(0)
        stats["exposure"] = float((position != 0).mean())
        stats["trades"] = int((position.diff().fillna(position) != 0).sum())
    return stats


def format_table(results: dict[str, dict]) -> str:
    """Side-by-side table of several summary() dicts."""
    pct = {"total_return", "cagr", "volatility", "max_drawdown", "best_day", "worst_day", "exposure"}
    df = pd.DataFrame(results)
    rows = []
    for key, row in df.iterrows():
        cells = []
        for v in row:
            if isinstance(v, float) and key in pct:
                cells.append(f"{v:8.2%}")
            elif isinstance(v, float):
                cells.append(f"{v:8.2f}")
            else:
                cells.append(f"{v!s:>10}")
        rows.append(f"{key:<14}" + "  ".join(cells))
    header = f"{'':<14}" + "  ".join(f"{c:>10}" for c in df.columns)
    return "\n".join([header, *rows])
