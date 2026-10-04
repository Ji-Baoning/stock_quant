<!-- web/src/components/ChallengeBlock.vue —— 详情页挑战裁决区块（spec 2026-10-04）。
     只读已发布的 strategy_comparison.json 投影：逐字展示，零派生、零重算。
     消费事实按 error_code × consumption 两轴三分支判定（§4.2-6/7）。 -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type {
  ChallengeScenarioResult,
  ChallengeView,
  ExperimentChallengesResponse,
} from "../api/types";
import Card from "./Card.vue";
import DataTable, { type DataTableColumn } from "./DataTable.vue";
import EmptyState from "./EmptyState.vue";
import Skeleton from "./Skeleton.vue";
import StateBadge from "./StateBadge.vue";

const props = defineProps<{ experimentId: string }>();
const client = useApiClient();

// 复制模式沿用顶栏 copy-version / 注册台 copy-command：navigator.clipboard
// 存在才可点（happy-dom 下是 getter-only，测试用 defineProperty 注入假实现）。
// 复制只是 provenance 便利，不是动作入口（spec §4.3）。
const canCopy = typeof navigator !== "undefined" && Boolean(navigator.clipboard);

async function copyChallengeId(value: string) {
  if (canCopy) {
    await navigator.clipboard.writeText(value);
  }
}

const payload = ref<ExperimentChallengesResponse | null>(null);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

const CELL_COLUMNS: DataTableColumn[] = [
  { key: "metric", label: "指标" },
  { key: "baseline", label: "baseline", align: "right" },
  { key: "challenger", label: "challenger", align: "right" },
  { key: "delta", label: "Δ", align: "right" },
  { key: "threshold", label: "门槛" },
  { key: "passed", label: "判定" },
];

/** 显示层格式化：单值定宽，绝不回写契约、不重算判定。 */
function fmt(value: number | null): string {
  return value === null ? "—" : value.toFixed(4);
}

function cellRows(result: ChallengeScenarioResult): Array<Record<string, unknown>> {
  return result.cells.map((cell) => ({
    metric: cell.metric,
    baseline: fmt(cell.baseline),
    challenger: fmt(cell.challenger),
    delta: fmt(cell.delta),
    threshold: cell.threshold,
    passed: cell.passed ? "通过" : "未通过",
  }));
}

function shortHash(value: string): string {
  return value.slice(0, 8);
}

/** §4.2-6：三分支。只看 consumption == null 会把 REFUSED 与 LOAD_ERROR 说成同一件事。 */
function consumptionNote(view: ChallengeView): string {
  if (view.consumption !== null) return "本次消耗了 holdout。";
  if (view.result.error_code === "HOLDOUT_CONSUMPTION_REFUSED") {
    return "消费被拒：本挑战未取得 holdout 消费。";
  }
  return "holdout 已被本次挑战消耗，但失败结果未附消费记录。";
}

/** §4.2-7：只有真正发生过消费的 FAILED 才加这句；REFUSED 一支不得出现。 */
function showsConsumedWarning(view: ChallengeView): boolean {
  return (
    view.result.status === "FAILED" &&
    (view.consumption !== null ||
      view.result.error_code !== "HOLDOUT_CONSUMPTION_REFUSED")
  );
}

onMounted(async () => {
  try {
    payload.value = await client.listExperimentChallenges(props.experimentId);
  } catch (cause) {
    error.value = toDisplayError(cause);
  } finally {
    loaded.value = true;
  }
});
</script>

<template>
  <Card title="挑战裁决" testid="challenge-block">
    <p v-if="error !== null" class="error" data-testid="challenge-error">
      挑战裁决加载失败 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <Skeleton v-else-if="!loaded" :rows="4" />
    <template v-else>
      <!-- 断言敏感的句子不跨行：Vue 模板空白压缩会把模板内换行保留成 \n，
           拆行会破坏 text() 的 toContain（同 RegisterPage 的诚实边界注释）。 -->
      <p class="challenge-honesty" data-testid="challenge-honesty">
        挑战是一次性持有集（holdout）的预注册配对比较：一笔 holdout 一经消耗即不可恢复，
        改变数据集版本、参数、成本情景或失败结果都不会恢复它。本区块是既有裁决的证据展示——
        web 不发起挑战、不能重跑（对已消费的 holdout 重复声明会被
        <code>HOLDOUT_CONSUMPTION_REFUSED</code> 拒绝）。
      </p>
      <div v-if="(payload?.challenges.length ?? 0) === 0" data-testid="challenge-empty">
        <EmptyState
          title="本实验尚无挑战裁决"
          description="该实验尚未作为 baseline 或 challenger 参与任何一次性挑战。发起挑战：python -m stock_quant research challenge --declaration <declaration.json> --root <root>"
        />
      </div>
      <article
        v-for="view in payload?.challenges ?? []"
        :key="view.challenge_id"
        class="challenge-card"
        data-testid="challenge-card"
      >
        <p class="challenge-role" data-testid="challenge-role">
          {{ view.role === "baseline" ? "本实验为 baseline" : "本实验为 challenger" }}
          · <code :title="view.challenge_id">{{ shortHash(view.challenge_id) }}</code>
          <button
            type="button"
            data-testid="challenge-copy-id"
            :disabled="!canCopy"
            @click="copyChallengeId(view.challenge_id)"
          >
            复制
          </button>
        </p>
        <p class="challenge-verdict" data-testid="challenge-verdict">
          <StateBadge
            kind="challenge"
            :value="view.result.conclusion ?? view.result.status"
          />
          <span class="challenge-time">{{ view.declaration.declared_before_run_at }}</span>
        </p>
        <p class="resolved" data-testid="challenge-identity">
          家族 {{ view.declaration.strategy_family }}
          · 挑战方策略快照
          <code :title="view.declaration.challenger_strategy_hash">{{ shortHash(view.declaration.challenger_strategy_hash) }}</code>
          · 冻结政策
          <code :title="view.declaration.comparison_policy_hash">{{ shortHash(view.declaration.comparison_policy_hash) }}</code>
          · 折调度
          <code :title="view.declaration.fold_schedule_hash">{{ shortHash(view.declaration.fold_schedule_hash) }}</code>
        </p>
        <ul
          v-if="view.result.reasons.length > 0"
          class="challenge-reasons"
          data-testid="challenge-reasons"
        >
          <li v-for="(reason, index) in view.result.reasons" :key="index">{{ reason }}</li>
        </ul>
        <p
          v-if="view.result.error_code !== null"
          class="challenge-error-code"
          data-testid="challenge-error-code"
        >
          失败码 <code>{{ view.result.error_code }}</code>
        </p>
        <p
          v-if="view.result.failed_scenarios.length > 0"
          class="challenge-line"
          data-testid="challenge-failed-scenarios"
        >
          未通过情景：{{ view.result.failed_scenarios.join("、") }}
        </p>
        <p
          v-if="view.result.skipped_fold_ids.length > 0"
          class="challenge-line"
          data-testid="challenge-skipped-folds"
        >
          跳过折：
          <span v-for="fold in view.result.skipped_fold_ids" :key="fold">
            <code :title="fold">{{ shortHash(fold) }}</code>
          </span>
        </p>
        <section
          v-for="scenario in view.result.scenario_results"
          :key="scenario.scenario"
          class="challenge-scenario"
          data-testid="challenge-scenario"
        >
          <p class="challenge-scenario-head">
            情景 <strong>{{ scenario.scenario }}</strong>
            · 执行折 {{ scenario.executed_fold_count }}
            · {{ scenario.passed ? "全部门槛通过" : "存在未通过门槛" }}
          </p>
          <DataTable
            testid="challenge-cells"
            row-testid="challenge-cell"
            :rows="cellRows(scenario)"
            :columns="CELL_COLUMNS"
          />
        </section>
        <div class="challenge-consumption" data-testid="challenge-consumption">
          <p>{{ consumptionNote(view) }}</p>
          <template v-if="view.consumption !== null">
            <p>
              消费键 <code>{{ view.consumption.consumption_key }}</code>
              · 消费于 {{ view.consumption.consumed_at }}
            </p>
            <p class="resolved">
              universe {{ view.consumption.universe_definition.universe_id }}
              · 成员表
              <code :title="view.consumption.universe_definition.membership_table_sha256">{{ shortHash(view.consumption.universe_definition.membership_table_sha256) }}</code>
              · 证据摘要
              <code :title="view.consumption.universe_definition.evidence_summary_sha256">{{ shortHash(view.consumption.universe_definition.evidence_summary_sha256) }}</code>
            </p>
          </template>
          <p
            v-if="showsConsumedWarning(view)"
            class="challenge-consumed-warning"
            data-testid="challenge-consumed-warning"
          >
            该挑战仍已消耗 holdout。
          </p>
        </div>
      </article>
    </template>
  </Card>
</template>
