"""Dataset registry: parse and validate datasets/<name>.yaml files."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
SOURCE_TYPES = {"file", "database", "api"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    type: str
    location: str = ""          # file path/GCS URI, or API endpoint
    format: str = "csv"         # file only: csv | parquet
    secret_ref: str = ""        # database/api: env var name holding DSN/API key
    query: str = ""             # database only
    params: dict = field(default_factory=dict)  # api only: extra query params


@dataclass(frozen=True)
class MappingConfig:
    entity_id: str
    ds: str
    quantity: str
    attrs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrainingConfig:
    schedule: str = "0 4 * * 0"
    validation_days: int = 28
    min_wape_improvement: float = 0.0


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    source: SourceConfig
    mapping: MappingConfig
    schedule: str = "0 2 * * *"
    training: TrainingConfig = field(default_factory=TrainingConfig)
    preprocess: str | None = None   # named hook run on raw df before normalize
    postprocess: str | None = None  # named hook run on forecast output

    @property
    def table_name(self) -> str:
        # Backward compat: hbaac_sku keeps writing to the pre-existing table.
        return "sales_daily" if self.name == "hbaac_sku" else f"{self.name}_daily"


def _require(d: dict, key: str, ctx: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{ctx}: missing required field '{key}'")
    return d[key]


def load_dataset_config(path: str | Path) -> DatasetConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name}: invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")

    name = _require(raw, "name", path.name)
    if not NAME_RE.match(str(name)):
        raise ConfigError(f"{path.name}: name '{name}' must match {NAME_RE.pattern}")

    src = _require(raw, "source", path.name)
    if not isinstance(src, dict):
        raise ConfigError(f"{path.name}: 'source' must be a mapping")
    stype = _require(src, "type", f"{path.name} source")
    if stype not in SOURCE_TYPES:
        raise ConfigError(f"{path.name}: source.type '{stype}' not in {sorted(SOURCE_TYPES)}")

    m = _require(raw, "mapping", path.name)
    if not isinstance(m, dict):
        raise ConfigError(f"{path.name}: 'mapping' must be a mapping")
    mapping = MappingConfig(
        entity_id=_require(m, "entity_id", f"{path.name} mapping"),
        ds=_require(m, "ds", f"{path.name} mapping"),
        quantity=_require(m, "quantity", f"{path.name} mapping"),
        attrs=list(m.get("attrs") or []),
    )

    training_raw = raw.get("training")
    if training_raw is not None and not isinstance(training_raw, dict):
        raise ConfigError(f"{path.name}: 'training' must be a mapping")
    t = training_raw or {}
    training = TrainingConfig(
        schedule=t.get("schedule", TrainingConfig.schedule),
        validation_days=int(t.get("validation_days", TrainingConfig.validation_days)),
        min_wape_improvement=float(t.get("min_wape_improvement", 0.0)),
    )

    return DatasetConfig(
        name=name,
        source=SourceConfig(
            type=stype,
            location=src.get("location", ""),
            format=src.get("format", "csv"),
            secret_ref=src.get("secret_ref", ""),
            query=src.get("query", ""),
            params=src.get("params") or {},
        ),
        mapping=mapping,
        schedule=raw.get("schedule", "0 2 * * *"),
        training=training,
        preprocess=raw.get("preprocess"),
        postprocess=raw.get("postprocess"),
    )


def load_all_dataset_configs(dir_path: str | Path) -> list[DatasetConfig]:
    configs = []
    for p in sorted(Path(dir_path).glob("*.yaml")):
        try:
            configs.append(load_dataset_config(p))
        except ConfigError as e:
            log.error("Skipping dataset config %s: %s", p.name, e)
    return configs
