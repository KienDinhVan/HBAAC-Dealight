from pathlib import Path

import pandas as pd


TRAIN_PATH = Path("data/raw/train.csv")
SAMPLE_SUBMISSION_PATH = Path("data/raw/sample_submission.csv")
TRAIN_COLUMNS = [
    "Date",
    "Stt",
    "ItemCode",
    "Quantity",
    "UnitPrice",
    "SalesAmount",
    "Unit Cost",
    "Cost Amount",
]
SUBMISSION_COLUMNS = ["id", *[f"F{i}" for i in range(1, 29)]]


def test_raw_transaction_data_matches_sprint_00_contract() -> None:
    assert TRAIN_PATH.exists()

    transactions = pd.read_csv(TRAIN_PATH, low_memory=False)

    assert transactions.columns.tolist() == TRAIN_COLUMNS
    assert transactions["ItemCode"].notna().all()
    assert pd.to_datetime(transactions["Date"], errors="coerce").notna().all()
    assert pd.to_numeric(transactions["Quantity"], errors="coerce").notna().all()
    # Returns are an expected raw input condition, not invalid demand records.
    assert transactions["Quantity"].lt(0).any()


def test_sample_submission_defines_two_28_day_non_negative_phases() -> None:
    assert SAMPLE_SUBMISSION_PATH.exists()

    submission = pd.read_csv(SAMPLE_SUBMISSION_PATH)
    ids = submission["id"].str.extract(
        r"^(?P<sku>.+)_(?P<phase>validation|evaluation)$"
    )
    predictions = submission.drop(columns=["id"])

    assert submission.columns.tolist() == SUBMISSION_COLUMNS
    assert submission["id"].notna().all() and not submission["id"].duplicated().any()
    assert ids.notna().all().all()
    assert ids.groupby("sku")["phase"].nunique().eq(2).all()
    assert predictions.notna().all().all()
    assert predictions.ge(0).all().all()
