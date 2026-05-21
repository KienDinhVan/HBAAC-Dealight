from __future__ import annotations

import pandas as pd


def select_skus(
    profile: pd.DataFrame,
    strategy: str = "top_profit",
    top_n: int = 300,
    min_active_days: int = 50,
    max_days_since_last_sale: int = 56,
) -> pd.Index:
    ranked = profile.sort_values("profit_weight", ascending=False)
    if strategy == "top_profit":
        return ranked.head(top_n).index
    if strategy == "active_recent_top_profit":
        filtered = ranked[
            (ranked["active_days"] >= min_active_days)
            & (ranked["days_since_last_sale"] <= max_days_since_last_sale)
        ]
        return filtered.head(top_n).index
    if strategy == "active_top_profit":
        filtered = ranked[ranked["active_days"] >= min_active_days]
        return filtered.head(top_n).index
    if strategy == "recent_top_profit":
        filtered = ranked[ranked["days_since_last_sale"] <= max_days_since_last_sale]
        return filtered.head(top_n).index
    raise ValueError(f"Unknown SKU selection strategy: {strategy}")

