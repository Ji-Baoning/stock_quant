"""Contract tests for the challenge adjudication endpoint (spec 2026-10-04).

One published experiment (``service_project``) plus challenge results written
straight into ``data/strategy_challenges/results/`` in the *published*
``strategy_comparison.json`` shape (identity + consumption + result), so the
read-only projection is pinned against the real artifact form.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

EXPERIMENT_ID = "e" * 64
CHALLENGER_ID = "c" * 64
CHALLENGE_ID = "a" * 64
OTHER_CHALLENGE_ID = "b" * 64
BASELINE_HASH = "1" * 64
CHALLENGER_HASH = "2" * 64
UNIVERSE = {
    "universe_id": "custom_csi300_tw_tradable",
    "universe_version": "3" * 64,
    "membership_table_sha256": "4" * 64,
    "evidence_summary_sha256": "5" * 64,
}
POLICY_HASH = "6" * 64
FOLD_SCHEDULE_HASH = "f" * 64


def write_experiment_manifest(
    root: Path, experiment_id: str, strategy_hash: str
) -> None:
    """Overwrite the fixture experiment manifest with the identity field."""
    directory = root / "data" / "experiments" / experiment_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "status": "ACCEPTED",
                "dataset_version": "d" * 64,
                "universe_version": "u" * 64,
                "strategy_snapshot_sha256": strategy_hash,
            }
        ),
        encoding="utf-8",
    )


def consumption_record(challenge_id: str = CHALLENGE_ID) -> dict:
    return {
        "status": "consumed",
        "consumption_key": f"buffered_risk_weighted_momentum:{FOLD_SCHEDULE_HASH}",
        "challenge_id": challenge_id,
        "strategy_family": "buffered_risk_weighted_momentum",
        "fold_schedule_hash": FOLD_SCHEDULE_HASH,
        "declaration_sha256": "7" * 64,
        "comparison_policy_hash": POLICY_HASH,
        "baseline_experiment_id": EXPERIMENT_ID,
        "challenger_strategy_hash": CHALLENGER_HASH,
        "universe_definition": dict(UNIVERSE),
        "consumed_at": "2026-10-01T00:00:01+00:00",
    }


def comparison_payload(
    *,
    challenge_id: str = CHALLENGE_ID,
    baseline_experiment_id: str = EXPERIMENT_ID,
    challenger_strategy_hash: str = CHALLENGER_HASH,
    declared_before_run_at: str = "2026-10-01T00:00:00+00:00",
    status: str = "COMPLETED",
    conclusion: str | None = "PROMOTED",
    consumption: dict | None = None,
    error_code: str | None = None,
    reasons: tuple[str, ...] = (),
) -> dict:
    """The published ``strategy_comparison.json`` shape, verbatim."""
    completed = status == "COMPLETED"
    return {
        "challenge_id": challenge_id,
        "status": status,
        "conclusion": conclusion,
        "declaration": {
            "identity_scheme_version": "strategy-challenge-v1",
            "strategy_family": "buffered_risk_weighted_momentum",
            "baseline_experiment_id": baseline_experiment_id,
            "challenger_strategy_hash": challenger_strategy_hash,
            "fold_schedule_hash": FOLD_SCHEDULE_HASH,
            "universe_definition": dict(UNIVERSE),
            "comparison_policy": {"policy_version": "strategy-comparison-v1"},
            "comparison_policy_hash": POLICY_HASH,
            "declared_before_run_at": declared_before_run_at,
        },
        "comparison_policy_hash": POLICY_HASH,
        "fold_schedule_hash": FOLD_SCHEDULE_HASH,
        "universe_definition": dict(UNIVERSE),
        "holdout_consumption": consumption,
        "baseline_experiment_id": baseline_experiment_id,
        "challenger_experiment_id": CHALLENGER_ID if completed else None,
        "snapshot_hashes": {"baseline": None, "challenger": None},
        "result": {
            "challenge_id": challenge_id,
            "strategy_family": "buffered_risk_weighted_momentum",
            "status": status,
            "conclusion": conclusion,
            "baseline_experiment_id": baseline_experiment_id,
            "challenger_experiment_id": CHALLENGER_ID if completed else None,
            "challenger_stability_conclusion": "STABLE" if completed else None,
            "comparison_policy_hash": POLICY_HASH,
            "executed_fold_count": 6 if completed else None,
            "declared_scenario_count": 3 if completed else None,
            "skipped_fold_ids": [],
            "failed_scenarios": [],
            "scenario_results": (
                [
                    {
                        "scenario": "full_cost",
                        "executed_fold_count": 6,
                        "passed": True,
                        "cells": [
                            {
                                "metric": "aggregate_sharpe_delta",
                                "baseline": 1.0,
                                "challenger": 1.3,
                                "delta": 0.3,
                                "threshold": ">= 0.10",
                                "passed": True,
                            }
                        ],
                    }
                ]
                if completed
                else []
            ),
            "reasons": list(reasons),
            "error_code": error_code,
        },
        "artifacts": {"strategy_comparison.json": "9" * 64},
    }


def write_challenge(
    root: Path, payload: dict, challenge_id: str = CHALLENGE_ID
) -> Path:
    directory = root / "data" / "strategy_challenges" / "results" / challenge_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "strategy_comparison.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return directory


def test_empty_root_returns_200_and_an_empty_list(client: TestClient) -> None:
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 200
    assert response.json() == {"experiment_id": EXPERIMENT_ID, "challenges": []}


def test_empty_results_tree_returns_an_empty_list(
    client: TestClient, service_project: Path
) -> None:
    (service_project / "data" / "strategy_challenges" / "results").mkdir(
        parents=True
    )
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 200
    assert response.json()["challenges"] == []


def test_unknown_hex64_experiment_is_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/experiments/{'9' * 64}/challenges")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "experiment_not_found"


def test_non_hex_experiment_id_is_422_invalid_request(client: TestClient) -> None:
    response = client.get("/api/v1/experiments/not-a-hash/challenges")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_baseline_side_match_is_projected_verbatim(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert len(body["challenges"]) == 1
    view = body["challenges"][0]
    assert view["challenge_id"] == CHALLENGE_ID
    assert view["role"] == "baseline"
    declaration = view["declaration"]
    assert declaration["strategy_family"] == "buffered_risk_weighted_momentum"
    assert declaration["baseline_experiment_id"] == EXPERIMENT_ID
    assert declaration["challenger_strategy_hash"] == CHALLENGER_HASH
    assert declaration["fold_schedule_hash"] == FOLD_SCHEDULE_HASH
    assert declaration["comparison_policy_hash"] == POLICY_HASH
    assert declaration["declared_before_run_at"] == "2026-10-01T00:00:00+00:00"
    assert declaration["universe_definition"] == UNIVERSE
    assert view["consumption"]["consumption_key"] == (
        f"buffered_risk_weighted_momentum:{FOLD_SCHEDULE_HASH}"
    )
    assert view["consumption"]["consumed_at"] == "2026-10-01T00:00:01+00:00"
    assert view["consumption"]["universe_definition"] == UNIVERSE
    result = view["result"]
    assert result["status"] == "COMPLETED"
    assert result["conclusion"] == "PROMOTED"
    assert result["challenger_experiment_id"] == CHALLENGER_ID
    assert result["executed_fold_count"] == 6
    assert result["scenario_results"][0]["cells"][0]["delta"] == 0.3
    assert result["scenario_results"][0]["cells"][0]["threshold"] == ">= 0.10"
    # 投影省略的校验层字段不得出现在响应里（spec §3.1 (b)）。
    assert "snapshot_hashes" not in view
    assert "artifacts" not in view
    assert "declaration_sha256" not in view["consumption"]


def test_challenger_side_match_uses_the_manifest_snapshot_hash(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, CHALLENGER_HASH)
    write_challenge(
        service_project,
        # baseline 侧必须是别的实验：否则 baseline 等式同时成立，按 spec §2.3
        # 判定顺序角色会落在 baseline，测不到 challenger 侧的快照哈希匹配。
        comparison_payload(
            baseline_experiment_id="8" * 64,
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["role"] for view in body["challenges"]] == ["challenger"]


def test_baseline_equality_wins_the_role_when_both_hold(
    client: TestClient, service_project: Path
) -> None:
    # 合法挑战两侧必为不同实验，这里只钉住判定顺序（先 baseline 后 challenger）。
    write_experiment_manifest(service_project, EXPERIMENT_ID, CHALLENGER_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            challenger_strategy_hash=CHALLENGER_HASH,
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["role"] for view in body["challenges"]] == ["baseline"]


def test_unrelated_challenge_is_excluded(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            baseline_experiment_id="8" * 64,
            challenger_strategy_hash="7" * 64,
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert body["challenges"] == []


def test_non_hex_names_and_plain_files_are_skipped(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    results = service_project / "data" / "strategy_challenges" / "results"
    (results / "not-a-challenge").mkdir(parents=True)
    (results / ".staging-deadbeef").mkdir(parents=True)
    (results / ("d" * 64)).write_text("i am a file, not a directory", encoding="utf-8")
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["challenge_id"] for view in body["challenges"]] == [CHALLENGE_ID]


def test_challenges_are_sorted_by_declaration_instant(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            challenge_id=OTHER_CHALLENGE_ID,
            declared_before_run_at="2026-10-02T00:00:00+00:00",
            consumption=consumption_record(OTHER_CHALLENGE_ID),
        ),
        challenge_id=OTHER_CHALLENGE_ID,
    )
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["challenge_id"] for view in body["challenges"]] == [
        CHALLENGE_ID,
        OTHER_CHALLENGE_ID,
    ]


def test_same_instant_falls_back_to_challenge_id_order(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            challenge_id=OTHER_CHALLENGE_ID,
            consumption=consumption_record(OTHER_CHALLENGE_ID),
        ),
        challenge_id=OTHER_CHALLENGE_ID,
    )
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    # 同一 instant 下 "a"*64 < "b"*64。
    assert [view["challenge_id"] for view in body["challenges"]] == [
        CHALLENGE_ID,
        OTHER_CHALLENGE_ID,
    ]


def test_missing_comparison_file_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    # hex64 目录但产物缺失：仍是"本挑战存在且不可读"，不是"本挑战不存在"。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    directory = service_project / "data" / "strategy_challenges" / "results" / CHALLENGE_ID
    directory.mkdir(parents=True)
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_non_json_comparison_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    directory = write_challenge(
        service_project, comparison_payload(consumption=consumption_record())
    )
    (directory / "strategy_comparison.json").write_text("{not json", encoding="utf-8")
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_non_object_comparison_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    directory = write_challenge(
        service_project, comparison_payload(consumption=consumption_record())
    )
    (directory / "strategy_comparison.json").write_text("[1, 2, 3]", encoding="utf-8")
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_missing_declaration_or_result_block_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    payload = comparison_payload(consumption=consumption_record())
    payload.pop("result")
    write_challenge(service_project, payload)
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_payload_violating_the_declared_subset_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    # 决策层字段缺失 => 不是本模型声明的形状：失败闭合，不半截展示。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    payload = comparison_payload(consumption=consumption_record())
    payload["declaration"]["universe_definition"].pop("evidence_summary_sha256")
    write_challenge(service_project, payload)
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_non_iso_declaration_instant_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            declared_before_run_at="yesterday afternoon",
            consumption=consumption_record(),
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_malformed_consumption_scalar_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    # 损坏的 holdout_consumption 不是"未消费"：fail-closed，不得静默投影成 null（owner 裁决 2026-10-04）。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(consumption="corrupted"),
    )
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_refused_consumption_projects_a_null_record(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 形态一：注册表在消费前拒绝。consumption 为 null 且这里确实没消费。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="HOLDOUT_CONSUMPTION_REFUSED",
            reasons=("holdout already consumed",),
            consumption=None,
        ),
    )
    view = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()[
        "challenges"
    ][0]
    assert view["role"] == "baseline"
    assert view["consumption"] is None
    assert view["result"]["status"] == "FAILED"
    assert view["result"]["conclusion"] is None
    assert view["result"]["error_code"] == "HOLDOUT_CONSUMPTION_REFUSED"
    assert view["result"]["reasons"] == ["holdout already consumed"]


def test_experiment_load_error_projects_a_null_record(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 形态二：消费已成功，但 _publish_failure 恒传 consumption=None。
    # 服务层必须原样透传 null —— 消费与否由 error_code 与记录两轴共同判定。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="EXPERIMENT_LOAD_ERROR",
            reasons=("baseline artifacts unreadable",),
            consumption=None,
        ),
    )
    view = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()[
        "challenges"
    ][0]
    assert view["consumption"] is None
    assert view["result"]["error_code"] == "EXPERIMENT_LOAD_ERROR"


def test_evaluation_failure_keeps_the_real_consumption_record(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 形态三：评估期完整性失败，消费记录随结果一并发布。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="IDENTITY_MISMATCH",
            reasons=("manifest identity mismatch",),
            consumption=consumption_record(),
        ),
    )
    view = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()[
        "challenges"
    ][0]
    assert view["consumption"] is not None
    assert view["consumption"]["status"] == "consumed"
    assert view["consumption"]["consumption_key"].endswith(FOLD_SCHEDULE_HASH)
    assert view["result"]["error_code"] == "IDENTITY_MISMATCH"


def test_a_failed_challenge_appears_on_its_baseline_page_only(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 恒无 challenger 实验 id；best-effort 归属只在 baseline 侧成立。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="PAIRING_INCOMPLETE",
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["role"] for view in body["challenges"]] == ["baseline"]
