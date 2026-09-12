"""Typed, secret-free project configuration loading."""

from datetime import date
from datetime import time as dt_time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_retries: int = Field(default=3, ge=0, le=3)


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


def load_project_config(root: Path) -> ProjectConfig:
    """Load project, source, and cost configuration without reading secrets."""

    def read(name: str) -> dict[str, object]:
        return yaml.safe_load((root / "configs" / name).read_text()) or {}

    project = read("project.yml")
    project["sources"] = read("sources.yml")
    project["costs"] = read("costs.yml")
    review_path = root / "configs" / "corporate_action_reviews.yml"
    project["corporate_action_reviews"] = (
        yaml.safe_load(review_path.read_text()) or [] if review_path.exists() else []
    )
    return ProjectConfig.model_validate(project)
