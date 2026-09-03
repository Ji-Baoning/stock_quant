"""Typed, secret-free project configuration loading."""

from datetime import date
from pathlib import Path

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


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    initial_cash: float = Field(gt=0)
    benchmark_symbols: list[str]
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    costs: CostConfig = Field(default_factory=CostConfig)

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
    return ProjectConfig.model_validate(project)
