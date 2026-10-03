# 025. Rights issues book at full participation

- Status: accepted
- Date: 2026-10-03
- Spec: docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md（编号
  说明：设计 §7.1 原取 023，登记时 023/024 已被占用，顺延为 025）

## Context

持有期回测对已实现配股无条件中止（准备性检查与入账两处），当前数据集窗口内
20 条配股因此挡死全窗口运行；`adjusted_bar` 的 total-return 递推却已隐含
"全额参与"假设——因子侧与回测侧口径分裂。

## Decision

1. 已实现、有可用配股价（`rights_issue_price > 0`）的配股在除权日按**全额参与**
   口径入账：应配 `round(持仓 × r, HALF_UP)`；认购 `min(应配, floor(当日预算/配股价))`；
   缴款 CENT 量化；同一事件的现金分红先入账、后算预算。
2. 现金不足时按可用现金部分认购，弃配逐事件留痕（账本三原子字段
   `cash_paid` / `rights_entitlement_shares` / `rights_subscribed_shares`，
   弃配为导出量）。
3. `Account.debit_cash` 是全或无原语：非正额 `ValueError`、超额
   `CashShortfallError`，现金非负不变量不被削弱。
4. TERP 权益守恒（与 `adjusted_bar` 递推一致）有单元断言钉住（合成价格用例）。

## Rejected alternatives

- 透支 / 强制卖出 / 现金缓冲预留：引入未定义的资金规则，超出本决策。
- 按零价或估算价入账无法给价事件：无法计算成本的事件仍 fail-closed 中止。
- 收窄 `possible_held_symbols` 窗口级超集：那是既有契约（预跑持仓重放才能收窄）。

## Consequences

其余复杂行为（吸收合并、换股、非 `implemented`、跨源冲突、缺关键日期、无价
配股）继续 fail-closed；重放端 `.get() or 0` 保持旧 run 可读；弃配 binding 频率
在首次全窗口运行后即可测量（若某场景普遍 binding，是资金规则问题，非本口径）。
`adjusted_bar` 递推口径本身的 ADR 登记仍是独立未决事项（见 DECISIONS_INDEX
"Not yet recorded"）。
