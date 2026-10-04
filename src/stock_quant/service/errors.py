"""The service's stable error vocabulary and the shared error envelope.

Independent module so routers and the app factory can both import it
without a cycle. Every failure fails closed through :class:`ErrorResponse`
with a stable ``code`` (spec §8.3).
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    dataset_version: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ServiceError(Exception):
    """Base of the stable failure vocabulary; carries its HTTP status."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DatasetUnresolvable(ServiceError):
    status_code = 404
    code = "dataset_not_found"


class NoCurrentDataset(ServiceError):
    status_code = 404
    code = "no_current_dataset"


class UnknownTable(ServiceError):
    status_code = 404
    code = "unknown_table"


class UnknownExperiment(ServiceError):
    status_code = 404
    code = "experiment_not_found"


class ResultsNotFound(ServiceError):
    status_code = 404
    code = "results_not_found"


class FoldNotFound(ServiceError):
    status_code = 404
    code = "fold_not_found"


class ArtifactNotFound(ServiceError):
    status_code = 404
    code = "artifact_not_found"


class ReportNotFound(ServiceError):
    status_code = 404
    code = "report_not_found"


class UnknownColumn(ServiceError):
    status_code = 422
    code = "unknown_column"


class UnsupportedFilter(ServiceError):
    status_code = 422
    code = "unsupported_filter"


class QueryTimeBudgetExceeded(ServiceError):
    status_code = 422
    code = "query_time_budget_exceeded"


class ManifestUnreadable(ServiceError):
    status_code = 500
    code = "dataset_manifest_unreadable"


class QualityReportUnreadable(ServiceError):
    status_code = 500
    code = "quality_report_unreadable"


class AcceptanceRecordUnreadable(ServiceError):
    status_code = 500
    code = "acceptance_record_unreadable"


class ExperimentManifestUnreadable(ServiceError):
    status_code = 500
    code = "experiment_manifest_unreadable"


class ChallengeUnreadable(ServiceError):
    """A published ``strategy_comparison.json`` exists but cannot be projected.

    Fail closed (spec §3.2): silently skipping it would hide a challenge that
    already consumed the holdout -- the one thing this surface must never do.
    """

    status_code = 500
    code = "challenge_comparison_unreadable"


class ServiceConflict(ServiceError):
    """Reserved: P4's update-jobs endpoint raises this with 409 (spec §9.3).

    The 409 envelope shape is contracted here so the read-only surface and
    the later operations API share one error vocabulary from day one.
    """

    status_code = 409
    code = "update_already_running"
