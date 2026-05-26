from datetime import date

import pandas as pd
import pytest

from scripts.load_submission_forecast import submission_to_forecast_frame


def _submission_frame() -> pd.DataFrame:
    values = {
        f"F{i}": [float(i), float(i + 1), float(i + 2), float(i + 3)]
        for i in range(1, 29)
    }
    return pd.DataFrame(
        {
            "id": [
                "SKU-00001_validation",
                "SKU-00002_validation",
                "SKU-00001_evaluation",
                "SKU-00002_evaluation",
            ],
            **values,
        }
    )


def test_submission_to_forecast_frame_builds_56_horizons_per_sku(tmp_path) -> None:
    source = tmp_path / "submission.csv"
    _submission_frame().to_csv(source, index=False)

    result = submission_to_forecast_frame(source, date(2025, 9, 5))

    assert len(result) == 112
    assert result.groupby("item_code")["horizon"].nunique().tolist() == [56, 56]
    first = result.loc[result["item_code"].eq("SKU-00001")].iloc[0]
    last = result.loc[result["item_code"].eq("SKU-00001")].iloc[-1]
    assert first["target_date"] == date(2025, 9, 6)
    assert last["target_date"] == date(2025, 10, 31)


def test_submission_to_forecast_frame_rejects_negative_prediction(tmp_path) -> None:
    source = tmp_path / "submission.csv"
    frame = _submission_frame()
    frame.loc[0, "F1"] = -1.0
    frame.to_csv(source, index=False)

    with pytest.raises(ValueError, match="negative"):
        submission_to_forecast_frame(source, date(2025, 9, 5))
