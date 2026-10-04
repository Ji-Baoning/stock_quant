import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { ApiError, apiClientKey, type ApiClient } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import ChallengeBlock from "../src/components/ChallengeBlock.vue";
import { fakeClient, flushPromises } from "./helpers";
import type { ChallengeView, ExperimentChallengesResponse } from "../src/api/types";

const EX = "e".repeat(64);
const CHALLENGE_ID = "a".repeat(64);
const CHALLENGER_HASH = "2".repeat(64);
const UNIVERSE = {
  universe_id: "custom_csi300_tw_tradable",
  universe_version: "3".repeat(64),
  membership_table_sha256: "4".repeat(64),
  evidence_summary_sha256: "5".repeat(64),
};

function challengeView(overrides: Partial<ChallengeView> = {}): ChallengeView {
  const base: ChallengeView = {
    challenge_id: CHALLENGE_ID,
    role: "baseline",
    declaration: {
      strategy_family: "buffered_risk_weighted_momentum",
      baseline_experiment_id: EX,
      challenger_strategy_hash: CHALLENGER_HASH,
      fold_schedule_hash: "f".repeat(64),
      comparison_policy_hash: "6".repeat(64),
      declared_before_run_at: "2026-10-01T00:00:00+00:00",
      universe_definition: { ...UNIVERSE },
    },
    consumption: {
      status: "consumed",
      consumption_key: `buffered_risk_weighted_momentum:${"f".repeat(64)}`,
      consumed_at: "2026-10-01T00:00:01+00:00",
      universe_definition: { ...UNIVERSE },
    },
    result: {
      status: "COMPLETED",
      conclusion: "PROMOTED",
      challenger_stability_conclusion: "STABLE",
      challenger_experiment_id: "c".repeat(64),
      executed_fold_count: 6,
      declared_scenario_count: 3,
      skipped_fold_ids: [],
      failed_scenarios: [],
      reasons: [],
      scenario_results: [
        {
          scenario: "full_cost",
          executed_fold_count: 6,
          passed: true,
          cells: [
            {
              metric: "aggregate_sharpe_delta",
              baseline: 1.0,
              challenger: 1.3,
              delta: 0.3,
              threshold: ">= 0.10",
              passed: true,
            },
          ],
        },
      ],
      error_code: null,
    },
  };
  return { ...base, ...overrides };
}

function payload(challenges: ChallengeView[]): ExperimentChallengesResponse {
  return { experiment_id: EX, challenges };
}

function mountBlock(client: ApiClient, experimentId: string = EX) {
  return mount(ChallengeBlock, {
    props: { experimentId },
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

function clientFor(challenges: ChallengeView[]): ApiClient {
  return fakeClient({
    listExperimentChallenges: async () => payload(challenges),
  });
}

describe("ChallengeBlock（挑战裁决区块）", () => {
  it("空态：标题、原因与 CLI 引导", async () => {
    const wrapper = mountBlock(clientFor([]));
    await flushPromises();
    const empty = wrapper.get('[data-testid="challenge-empty"]');
    expect(empty.text()).toContain("本实验尚无挑战裁决");
    expect(empty.text()).toContain("尚未作为 baseline 或 challenger");
    expect(empty.text()).toContain("research challenge --declaration");
    // 诚实文案常驻（§4.3）。
    expect(wrapper.get('[data-testid="challenge-honesty"]').text()).toContain(
      "一经消耗即不可恢复",
    );
  });

  it("COMPLETED：角色、徽章、身份摘要、阈值表逐字渲染", async () => {
    const wrapper = mountBlock(clientFor([challengeView()]));
    await flushPromises();
    const card = wrapper.get('[data-testid="challenge-card"]');
    expect(card.get('[data-testid="challenge-role"]').text()).toContain(
      "本实验为 baseline",
    );
    expect(card.get('[data-testid="challenge-verdict"]').get('[data-testid="state-badge"]').text())
      .toBe("PROMOTED");
    const identity = card.get('[data-testid="challenge-identity"]').text();
    expect(identity).toContain("buffered_risk_weighted_momentum");
    expect(identity).toContain("2".repeat(8));
    const table = card.get('[data-testid="challenge-cells"]');
    expect(table.text()).toContain("aggregate_sharpe_delta");
    expect(table.text()).toContain(">= 0.10");
    expect(table.text()).toContain("0.3000");
  });

  it("consumption 非 null：展示消费键/时刻/universe 四字段 + 已消耗文案", async () => {
    const wrapper = mountBlock(clientFor([challengeView()]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("本次消耗了 holdout");
    expect(block.text()).toContain("buffered_risk_weighted_momentum:");
    expect(block.text()).toContain("2026-10-01T00:00:01+00:00");
    expect(block.text()).toContain("custom_csi300_tw_tradable");
    expect(block.text()).toContain("4".repeat(8));
    expect(block.text()).toContain("5".repeat(8));
    expect(wrapper.find('[data-testid="challenge-consumed-warning"]').exists()).toBe(false);
  });

  it("FAILED + HOLDOUT_CONSUMPTION_REFUSED：未消费文案，且不得出现‘已消耗’", async () => {
    const view = challengeView({
      consumption: null,
      result: {
        ...challengeView().result,
        status: "FAILED",
        conclusion: null,
        error_code: "HOLDOUT_CONSUMPTION_REFUSED",
        reasons: ["holdout already consumed"],
        scenario_results: [],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("消费被拒：本挑战未取得 holdout 消费");
    expect(block.text()).not.toContain("已消耗");
    expect(wrapper.find('[data-testid="challenge-consumed-warning"]').exists()).toBe(false);
    // FAILED 的 error_code 与 reasons 必须显式渲染（§4.2-7）。
    expect(wrapper.get('[data-testid="challenge-error-code"]').text()).toContain(
      "HOLDOUT_CONSUMPTION_REFUSED",
    );
    expect(wrapper.get('[data-testid="challenge-reasons"]').text()).toContain(
      "holdout already consumed",
    );
    expect(wrapper.get('[data-testid="challenge-verdict"]').get('[data-testid="state-badge"]').text())
      .toBe("FAILED");
  });

  it("FAILED + EXPERIMENT_LOAD_ERROR：null 记录但必须说‘已消耗但未附消费记录’", async () => {
    const view = challengeView({
      consumption: null,
      result: {
        ...challengeView().result,
        status: "FAILED",
        conclusion: null,
        error_code: "EXPERIMENT_LOAD_ERROR",
        reasons: ["baseline artifacts unreadable"],
        scenario_results: [],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("已被本次挑战消耗，但失败结果未附消费记录");
    expect(wrapper.get('[data-testid="challenge-consumed-warning"]').text()).toContain(
      "仍已消耗 holdout",
    );
  });

  it("FAILED + IDENTITY_MISMATCH：展示真实消费记录，并标注仍已消耗", async () => {
    const view = challengeView({
      result: {
        ...challengeView().result,
        status: "FAILED",
        conclusion: null,
        error_code: "IDENTITY_MISMATCH",
        reasons: ["manifest identity mismatch"],
        scenario_results: [],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("本次消耗了 holdout");
    expect(block.text()).toContain("buffered_risk_weighted_momentum:");
    expect(wrapper.get('[data-testid="challenge-consumed-warning"]').text()).toContain(
      "仍已消耗 holdout",
    );
  });

  it("failed_scenarios / skipped_fold_ids 非空时标注", async () => {
    const view = challengeView({
      result: {
        ...challengeView().result,
        failed_scenarios: ["zero_cost"],
        skipped_fold_ids: ["9".repeat(64)],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    expect(wrapper.get('[data-testid="challenge-failed-scenarios"]').text()).toContain(
      "zero_cost",
    );
    expect(wrapper.get('[data-testid="challenge-skipped-folds"]').text()).toContain(
      "9".repeat(8),
    );
  });

  it("challenge_id 的复制按钮写入剪贴板，且区块内没有第二个按钮（§4.2-1 / §4.3）", async () => {
    const wrapper = mountBlock(clientFor([challengeView()]));
    await flushPromises();
    const writeText = vi.fn(async () => undefined);
    // happy-dom 20 的 navigator.clipboard 是 getter-only，用 defineProperty 注入假实现。
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const buttons = wrapper.findAll('[data-testid="challenge-block"] button');
    expect(buttons.map((button) => button.text())).toEqual(["复制"]);
    await wrapper.get('[data-testid="challenge-copy-id"]').trigger("click");
    expect(writeText).toHaveBeenCalledWith(CHALLENGE_ID);
  });

  it("加载失败：渲染稳定 code 与安全摘要，不渲染堆栈", async () => {
    const client = fakeClient({
      listExperimentChallenges: async () => {
        throw new ApiError(500, "challenge_comparison_unreadable", "comparison unreadable");
      },
    });
    const wrapper = mountBlock(client);
    await flushPromises();
    const error = wrapper.get('[data-testid="challenge-error"]');
    expect(error.text()).toContain("challenge_comparison_unreadable");
    expect(error.text()).toContain("comparison unreadable");
    expect(wrapper.text()).not.toContain("stack");
  });

  it("初始渲染骨架，取数完成前不渲染区块内容", async () => {
    let release!: (value: ExperimentChallengesResponse) => void;
    const client = fakeClient({
      listExperimentChallenges: () =>
        new Promise<ExperimentChallengesResponse>((resolve) => {
          release = resolve;
        }),
    });
    const wrapper = mountBlock(client);
    await flushPromises();
    expect(wrapper.find('[data-testid="skeleton"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="challenge-honesty"]').exists()).toBe(false);
    release(payload([]));
    await flushPromises();
    expect(wrapper.find('[data-testid="skeleton"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="challenge-empty"]').exists()).toBe(true);
  });
});
