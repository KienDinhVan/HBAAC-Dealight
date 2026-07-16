import pandas as pd

from scripts import run_dataset_pipeline
from scripts.run_dataset_pipeline import run_stage
from hbacc_prj.dataset_config import DatasetConfig, MappingConfig, SourceConfig


def test_ingest_stage_writes_parquet(tmp_path, monkeypatch):
    src = tmp_path / "s.csv"
    pd.DataFrame({"item": ["A"], "day": ["2026-01-01"], "qty": [1]}).to_csv(src, index=False)
    cfg = DatasetConfig(
        name="sample_ds",
        source=SourceConfig(type="file", location=str(src)),
        mapping=MappingConfig(entity_id="item", ds="day", quantity="qty"),
    )
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))  # write under tmp instead of gs://
    run_stage(cfg, "ingest", batch_id="b1")
    out = pd.read_parquet(tmp_path / "raw" / "sample_ds" / "b1" / "canonical.parquet")
    assert out["entity_id"].tolist() == ["A"]


def test_training_success_sends_discord_summary(monkeypatch):
    sent = []
    report = {
        "metrics": {"lightgbm": {"wape": 0.31}},
        "best_baseline_wape": 0.42,
        "passed_registration_rule": True,
        "registered_model_name": "sku-demand-lightgbm",
        "registered_model_version": "5",
        "mlflow_run_id": "run-123",
    }
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setattr(
        run_dataset_pipeline,
        "_post_discord",
        lambda url, content: sent.append((url, content)),
    )

    run_dataset_pipeline._notify_training_success("hbaac_sku", "manual__1", report)

    assert sent[0][0] == "https://discord.test/hook"
    assert "[Model retrain] SUCCESS" in sent[0][1]
    assert "Quality gate: **PASSED**" in sent[0][1]
    assert "sku-demand-lightgbm` version `5" in sent[0][1]
    assert "run-123" in sent[0][1]


def test_training_success_without_webhook_is_skipped(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(
        run_dataset_pipeline,
        "_post_discord",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    run_dataset_pipeline._notify_training_success("hbaac_sku", "manual__1", {})


def test_training_success_notification_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setattr(
        run_dataset_pipeline,
        "_post_discord",
        lambda *_: (_ for _ in ()).throw(OSError("network unavailable")),
    )

    run_dataset_pipeline._notify_training_success("hbaac_sku", "manual__1", {})
