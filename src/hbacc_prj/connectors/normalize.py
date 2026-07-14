"""Map any raw DataFrame to the canonical schema and validate it."""
from __future__ import annotations

import pandas as pd

from hbacc_prj.dataset_config import MappingConfig

CANONICAL_COLUMNS = ["entity_id", "ds", "quantity", "attrs"]


class NormalizeError(ValueError):
    def __init__(self, report: list[str]):
        super().__init__("; ".join(report))
        self.report = report


def normalize(df: pd.DataFrame, mapping: MappingConfig) -> pd.DataFrame:
    report: list[str] = []
    needed = {"entity_id": mapping.entity_id, "ds": mapping.ds, "quantity": mapping.quantity}
    for canon, src in needed.items():
        if src not in df.columns:
            report.append(f"missing source column '{src}' (mapped to {canon})")
    for a in mapping.attrs:
        if a not in df.columns:
            report.append(f"missing attrs column '{a}'")
    if report:
        raise NormalizeError(report)

    out = pd.DataFrame({
        "entity_id": df[mapping.entity_id].astype("string"),
        "ds": pd.to_datetime(df[mapping.ds], errors="coerce"),
        "quantity": pd.to_numeric(df[mapping.quantity], errors="coerce"),
    })
    bad_ds = int(out["ds"].isna().sum())
    if bad_ds:
        report.append(f"ds: {bad_ds} unparseable date value(s)")
    bad_qty = int(out["quantity"].isna().sum())
    if bad_qty:
        report.append(f"quantity: {bad_qty} non-numeric value(s)")
    dups = int(out.duplicated(subset=["entity_id", "ds"]).sum())
    if dups:
        report.append(f"duplicate entity_id/ds pairs: {dups}")
    if report:
        raise NormalizeError(report)

    out["attrs"] = df[mapping.attrs].to_dict("records") if mapping.attrs else [{}] * len(df)
    return out[CANONICAL_COLUMNS]
