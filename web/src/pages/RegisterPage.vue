<script setup lang="ts">
// S3a Task 2 注册台：Card 内表单 → 实时生成 spec-YAML 草稿预览 + 冻结 CLI 命令。
// 诚实边界：零写面、零 API 触点——不 import useApiClient 之外任何数据源，本页连 client 都不注入。
import { computed, reactive } from "vue";
import Card from "../components/Card.vue";
import {
  baselineForm,
  buildCommand,
  buildSpecYaml,
  validateForm,
  type RegisterForm,
} from "../register/specBuilder";

// 表单状态 = 基线草稿（hypothesis 是占位符，被 validateForm 拦截——防呆初始值）。
const form = reactive<RegisterForm>(baselineForm());

// 页面级档位子集收口：cost_scenarios 只提供三种合法档（builder 的 ⊆ 校验保持原样）。
const COST_SCENARIO_OPTIONS = ["zero_cost", "commission_tax", "full_cost"] as const;

const PORTFOLIO_RULES = [
  { value: "top_n_equal_weight", label: "top_n_equal_weight（等权 top_n）" },
  { value: "buffered_risk_weighted", label: "buffered_risk_weighted（缓冲加权）" },
] as const;

const EXECUTION_PIPELINES = [
  { value: "walk_forward_oos_v1", label: "walk_forward_oos_v1（走查 OOS）" },
  { value: "engineering_single_window", label: "engineering_single_window（工程单窗）" },
] as const;

const errors = computed(() => validateForm(form));
const specYaml = computed(() => buildSpecYaml(form));
// root 固定 "."：命令提示在仓库根手动运行（页面永不代跑）。
const command = computed(() => buildCommand(form, "."));

// 复制模式复用顶栏 copy-version：navigator.clipboard 存在才可点，否则禁用。
const canCopy = typeof navigator !== "undefined" && Boolean(navigator.clipboard);

async function copyText(value: string) {
  if (canCopy) {
    await navigator.clipboard.writeText(value);
  }
}
</script>

<template>
  <section>
    <h1>策略注册台</h1>
    <!-- 强制文案整句不换行：Vue 模板空白压缩会把模板内换行保留为 \n，拆行会破坏 text() 断言。 -->
    <blockquote class="honesty" data-testid="honesty-note">
      注册台只生成草稿：把 YAML 保存为 configs/experiments/&lt;文件名&gt;.yml 后在仓库根运行命令。web 不写文件、不启动 run（owner 裁定 2：发布型命令由人负责）。前端校验只是便捷提示，最终以 CLI 的冻结校验为准。信任档与命令入口一致：engineering → backtest --engineering 是工程诊断唯一真实入口；research run 恒以 RESEARCH 档运行（覆盖规格里的 trust_mode 与 data_acceptance_id）。
    </blockquote>

    <Card title="注册一个策略实验" testid="register-card">
      <div class="register-grid">
        <form data-testid="register-form" @submit.prevent>
          <label>
            文件名（不含扩展名）
            <input
              type="text"
              v-model="form.fileName"
              data-testid="field-file-name"
              autocomplete="off"
            />
          </label>
          <label>
            hypothesis（经济逻辑与预期来源）
            <textarea
              v-model="form.hypothesis"
              rows="3"
              data-testid="field-hypothesis"
            ></textarea>
          </label>
          <label>
            execution_pipeline
            <select v-model="form.executionPipeline" data-testid="field-execution-pipeline">
              <option v-for="option in EXECUTION_PIPELINES" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <label>
            trust_mode（信任档）
            <select v-model="form.trustMode" data-testid="field-trust-mode">
              <option value="engineering">engineering（工程诊断）</option>
              <option value="research">research（正式研究）</option>
            </select>
          </label>
          <p class="hint-inline" data-testid="trust-mode-hint">
            engineering → 不绑定验收记录（UNTRUSTED 诊断），命令改走 backtest --engineering（debug 产物，永不发布正式实验）；research → 绑定 CURRENT_ACCEPTED（正式门禁），命令为 research run。YAML 与命令预览随档位同步变化。
          </p>

          <label>
            universe_definition
            <input
              type="text"
              v-model="form.universeDefinition"
              list="universe-definition-options"
              data-testid="field-universe-definition"
              autocomplete="off"
            />
            <datalist id="universe-definition-options">
              <option value="custom_csi300_tw_tradable"></option>
              <option value="custom_csi300_ic_tradable">已退役（选中即跑不通）</option>
            </datalist>
          </label>
          <p class="hint-inline">
            已知选项仅为参考清单（custom_csi300_ic_tradable
            已退役，选中即跑不通）；以 configs/universes/ 实际存在为准。留空则不写入该键（工程路径）。
          </p>

          <label>
            start_date
            <input type="date" v-model="form.dateStart" data-testid="field-date-start" />
          </label>
          <label>
            end_date
            <input type="date" v-model="form.dateEnd" data-testid="field-date-end" />
          </label>

          <label>
            portfolio_rule
            <select v-model="form.portfolioRuleName" data-testid="field-portfolio-rule-name">
              <option v-for="rule in PORTFOLIO_RULES" :key="rule.value" :value="rule.value">
                {{ rule.label }}
              </option>
            </select>
          </label>

          <!-- 组合规则名切换参数域：equal-weight 与 buffered 各占一域，不混填。 -->
          <fieldset v-if="form.portfolioRuleName === 'top_n_equal_weight'">
            <legend>top_n_equal_weight 参数</legend>
            <label>
              top_n
              <input type="number" v-model.number="form.topN" min="1" data-testid="field-top-n" />
            </label>
            <label>
              lot_size
              <input
                type="number"
                v-model.number="form.lotSize"
                min="1"
                data-testid="field-lot-size"
              />
            </label>
          </fieldset>
          <fieldset v-else>
            <legend>buffered_risk_weighted 参数</legend>
            <label>
              target_count
              <input
                type="number"
                v-model.number="form.buffered.targetCount"
                min="1"
                data-testid="field-buffered-target-count"
              />
            </label>
            <label>
              entry_rank
              <input
                type="number"
                v-model.number="form.buffered.entryRank"
                min="1"
                data-testid="field-buffered-entry-rank"
              />
            </label>
            <label>
              hold_rank
              <input
                type="number"
                v-model.number="form.buffered.holdRank"
                min="1"
                data-testid="field-buffered-hold-rank"
              />
            </label>
            <label>
              risk_lookback_days
              <input
                type="number"
                v-model.number="form.buffered.riskLookbackDays"
                min="2"
                data-testid="field-buffered-risk-lookback-days"
              />
            </label>
            <label>
              min_risk_observations
              <input
                type="number"
                v-model.number="form.buffered.minRiskObservations"
                min="2"
                data-testid="field-buffered-min-risk-observations"
              />
            </label>
            <label>
              volatility_floor_annualized
              <input
                type="text"
                v-model="form.buffered.volatilityFloorAnnualized"
                data-testid="field-buffered-volatility-floor-annualized"
              />
            </label>
            <label>
              max_single_weight
              <input
                type="text"
                v-model="form.buffered.maxSingleWeight"
                data-testid="field-buffered-max-single-weight"
              />
            </label>
            <label>
              rebalance_band_absolute
              <input
                type="text"
                v-model="form.buffered.rebalanceBandAbsolute"
                data-testid="field-buffered-rebalance-band-absolute"
              />
            </label>
            <label>
              gross_exposure
              <input
                type="text"
                v-model="form.buffered.grossExposure"
                data-testid="field-buffered-gross-exposure"
              />
            </label>
          </fieldset>

          <fieldset data-testid="field-cost-scenarios">
            <legend>cost_scenarios（至少一项）</legend>
            <label v-for="scenario in COST_SCENARIO_OPTIONS" :key="scenario">
              <input type="checkbox" :value="scenario" v-model="form.costScenarios" />
              {{ scenario }}
            </label>
          </fieldset>

          <label>
            random_seed
            <input type="number" v-model.number="form.randomSeed" data-testid="field-random-seed" />
          </label>
        </form>

        <div class="register-output">
          <div
            data-testid="form-errors"
            :class="{ error: errors.length > 0 }"
            role="alert"
          >
            <template v-if="errors.length > 0">
              <strong>校验未通过（便捷提示，最终以 CLI 冻结校验为准）：</strong>
              <ul>
                <li v-for="message in errors" :key="message">{{ message }}</li>
              </ul>
            </template>
            <p v-else>前端校验通过（最终以 CLI 冻结校验为准）。</p>
          </div>

          <h3>spec YAML 草稿</h3>
          <pre data-testid="yaml-preview"><code>{{ specYaml }}</code></pre>
          <button
            type="button"
            data-testid="copy-yaml"
            :disabled="!canCopy"
            @click="copyText(specYaml)"
          >
            复制 YAML
          </button>

          <h3>冻结命令（保存 YAML 后在仓库根手动运行）</h3>
          <pre data-testid="command-preview"><code>{{ command }}</code></pre>
          <button
            type="button"
            data-testid="copy-command"
            :disabled="!canCopy"
            @click="copyText(command)"
          >
            复制命令
          </button>
          <p class="hint-inline">
            页面不提供运行按钮：发布型命令由人负责（见上方诚实边界）。复制后请先核对 YAML
            内容与文件名，再在仓库根执行。
          </p>
        </div>
      </div>
    </Card>
  </section>
</template>
