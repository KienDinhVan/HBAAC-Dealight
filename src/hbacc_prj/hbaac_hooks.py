"""HBAAC-specific hooks: raw sales cleaning reused from data.py semantics."""
from __future__ import annotations

import pandas as pd

from hbacc_prj.data import parse_vn_decimal
from hbacc_prj.hooks import register


@register("hbaac_sales")
def hbaac_sales(df: pd.DataFrame) -> pd.DataFrame:
    """Clean raw HBAAC rows and aggregate to one row per ItemCode/Date."""
    df = df.copy()
    df["ItemCode"] = df["ItemCode"].astype("string")
    df["Date"] = pd.to_datetime(df["Date"])
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    df["SalesAmount"] = parse_vn_decimal(df["SalesAmount"].astype("string")).fillna(0)
    agg = (
        df.groupby(["ItemCode", "Date"], observed=True)
        .agg(net_qty=("Quantity", "sum"), SalesAmount=("SalesAmount", "sum"))
        .reset_index()
    )
    return agg


@register("hbaac_key_skus")
def hbaac_key_skus(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """Postprocess forecasts for two key SKUs by blending the model forecast
    with a weekday-seasonal reference computed from historical daily demand.

    Delegates to the real module-level function in
    ``hbacc_prj.postprocess_key_skus``: ``adjust_submission``. There is no
    ``apply_key_sku_rules`` function in that module -- that name in the
    original brief was a placeholder guess.

    ``adjust_submission`` is a *file-to-file* transform, not a pure
    DataFrame->DataFrame function:

        adjust_submission(input_path, output_path, train_end,
                           sku2_alpha, sku3_alpha) -> None

    It reads a Kaggle-style submission CSV (columns: ``id`` formatted as
    ``"<SKU>_validation"`` / ``"<SKU>_evaluation"``, plus 28 value columns
    ``F1..F28`` from ``hbacc_prj.model_twostage.VALUE_COLUMNS``), reads a
    pickled historical daily-demand matrix from a hard-coded path
    (``data/processed/daily_demand_matrix.pkl``), blends in a
    weekday/month-seasonal reference for SKU-00002 and SKU-00003, and writes
    the adjusted submission back out to ``output_path``.

    To adapt it to this hook's DataFrame->DataFrame interface, we round-trip
    through temp files: write ``forecast_df`` (expected to already be in the
    submission format described above) to a temp CSV, call
    ``adjust_submission`` with its CLI defaults, and read the adjusted
    result back in.

    The import of ``postprocess_key_skus`` is deferred to inside this
    function body (not at module scope) because it transitively imports
    ``hbacc_prj.model_twostage``, which pulls in ``lightgbm`` -- a heavy
    runtime dependency we don't want to force onto every process that
    merely imports this hooks module (e.g. at hook-registration time via
    ``hbacc_prj/__init__.py``). Calling this hook also requires the
    pickled demand matrix to exist on disk, which is not available in unit
    tests -- so this hook is registered but not exercised by
    ``tests/test_hbaac_dataset.py`` (only ``hbaac_sales`` is).
    """
    import tempfile
    from pathlib import Path

    from hbacc_prj.postprocess_key_skus import adjust_submission

    with tempfile.TemporaryDirectory() as tmp_dir:
        in_path = Path(tmp_dir) / "submission_in.csv"
        out_path = Path(tmp_dir) / "submission_out.csv"
        forecast_df.to_csv(in_path, index=False)
        adjust_submission(
            input_path=in_path,
            output_path=out_path,
            train_end="2025-09-05",
            sku2_alpha=0.0,
            sku3_alpha=0.15,
        )
        return pd.read_csv(out_path)
