"""Typed, secret-free project configuration loading."""

from datetime import date
from datetime import time as dt_time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_quant.data_contracts import DataContract, parse_data_contracts
from stock_quant.project_root import resolve_project_root
from stock_quant.safe_yaml import read_yaml


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_retries: int = Field(default=3, ge=0, le=3)
    #: Batch fields come in pairs and are off unless both are set: a size with
    #: no process bound would let one call hold the run, and a bound with no
    #: size cannot be applied.  Unset means the batch channel is not used at
    #: all (the lane falls back to per-symbol requests); the defaults are
    #: frozen from the batch-channel probes (docs/operations/), never guessed
    #: here.
    batch_size: int | None = Field(default=None, ge=1, le=1000)
    batch_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    factor_batch_size: int | None = Field(default=None, ge=1, le=1000)
    factor_batch_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)

    @model_validator(mode="after")
    def _batch_fields_are_paired(self) -> "SourceConfig":
        for size_field, timeout_field in (
            ("batch_size", "batch_timeout_seconds"),
            ("factor_batch_size", "factor_batch_timeout_seconds"),
        ):
            size = getattr(self, size_field)
            timeout = getattr(self, timeout_field)
            if (size is None) != (timeout is None):
                raise ValueError(
                    f"{size_field} and {timeout_field} must be configured together"
                )
        return self


class CostRate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_from: date
    commission_rate: float = Field(ge=0)
    minimum_commission: float = Field(ge=0)
    stamp_tax_sell_rate: float = Field(ge=0)
    slippage_rate: float = Field(ge=0)


class CostScenario(BaseModel):
    name: str
    rates: list[CostRate]


class CostConfig(BaseModel):
    scenarios: list[CostScenario] = Field(default_factory=list)


class CorporateActionReviewConfig(BaseModel):
    """A reviewed resolution for one otherwise-conflicting action event."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    ex_date: date
    selected_source: Literal["cninfo", "eastmoney"]
    record_date: date
    cash_dividend_per_share: float = Field(ge=0)
    bonus_share_ratio: float = Field(default=0, ge=0)
    capitalization_ratio: float = Field(default=0, ge=0)
    rationale: str = Field(min_length=1)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    initial_cash: float = Field(gt=0)
    benchmark_symbols: list[str]
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    data_contracts: dict[str, DataContract] = Field(default_factory=dict)
    costs: CostConfig = Field(default_factory=CostConfig)
    corporate_action_reviews: list[CorporateActionReviewConfig] = Field(
        default_factory=list
    )
    publication_time: dt_time = Field(
        default=dt_time(15, 0),
        description=(
            "Recorded market publication time. Informational only: update end "
            "dates come from the published trading calendar, never from the clock."
        ),
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectConfig":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


def load_project_config(root: str | Path) -> ProjectConfig:
    """Load project, source, and cost configuration without reading secrets.

    ``root`` is validated through :func:`resolve_project_root` first; every
    YAML file is then opened from that resolved root only — there is no
    fallback to the working directory or the repository root.
    """

    root = resolve_project_root(root)

    def read(name: str) -> dict[str, object]:
        return read_yaml(root / "configs" / name) or {}

    project = read("project.yml")
    sources = read("sources.yml")
    project["sources"] = {
        name: entry for name, entry in sources.items() if name != "data_contracts"
    }
    project["data_contracts"] = parse_data_contracts(sources.get("data_contracts"))
    project["costs"] = read("costs.yml")
    review_path = root / "configs" / "corporate_action_reviews.yml"
    project["corporate_action_reviews"] = (
        read_yaml(review_path) or [] if review_path.exists() else []
    )
    return ProjectConfig.model_validate(project)
