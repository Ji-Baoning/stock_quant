# 项目辅助脚本清单

根 `RUNBOOK.md` 与 `python -m stock_quant ...` 是正式操作入口。下表仅说明
`project/` 顶层辅助脚本的用途；它们不是绕过数据发布、验收或研究门禁的入口。

| 路径 | 状态 | 唯一用途 | 正式 CLI 替代项 |
| --- | --- | --- | --- |
| `project/audit_raw_provenance.py` | diagnostic | 审计既有原始快照的来源线索 | 无替代项 |
| `project/bootstrap_seed.py` | migration | 从项目配置构造一次性基线数据集 | `python -m stock_quant data bootstrap --root project` |
| `project/build_csi300_universe.py` | migration | 从已封存指数快照生成成员事实 | `python -m stock_quant data index-membership prepare --root project` |
| `project/check_data_sources.py` | diagnostic | 探测已启用供应商的连接契约 | 无替代项 |
| `project/clean_test_data.py` | active | 预览或清理历史派生数据与实验工件 | 无替代项 |
| `project/collect_csi300_official.py` | migration | 采集并封存 CSI 官方历史公告 | 无替代项 |
| `project/collect_index_constitution.py` | migration | 导出第三方指数成分包的不可变快照 | 无替代项 |
| `project/collect_index_weight_membership.py` | migration | 拉取并转换 index_weight 成员证据 | 无替代项 |
| `project/collect_sina_membership.py` | migration | 采集 Sina 历史成分作交叉证据 | 无替代项 |
| `project/crosscheck_calendar_relay.py` | diagnostic | 以 relay 交叉核对已发布交易日历 | 无替代项 |
| `project/drift_audit.py` | diagnostic | 季度全窗口漂移审计已发布原始快照 | 无替代项 |
| `project/execution_diagnostics.py` | diagnostic | 从实验工件生成执行偏差诊断 | 无替代项 |
| `project/expand_universe_to_membership.py` | migration | 将工程主数据扩展到冻结成员集合 | 无替代项 |
| `project/explanatory_diagnostics.py` | diagnostic | 从单个研究运行生成解释性诊断 | 无替代项 |
| `project/extend_history_offline.py` | migration | 用既有证据进行离线历史回补 | 无替代项 |
| `project/extend_master_to_membership.py` | migration | 扩展证券主数据以覆盖冻结成员 | 无替代项 |
| `project/probe_dataset_gates.py` | diagnostic | 预览真实更新必须通过的硬门禁 | 无替代项 |
| `project/probe_expansion_gap_risk.py` | diagnostic | 抽样衡量扩容前的缺口风险 | 无替代项 |
| `project/probe_relay_substitution.py` | diagnostic | 检测 relay 的静默数据替代 | 无替代项 |
| `project/probe_tushare_proxy.py` | diagnostic | 只读探测 Tushare 兼容代理能力 | 无替代项 |
| `project/rebuild_offline_real_dataset.py` | migration | 从已存真实表重建规范数据集 | 无替代项 |
| `project/rebuild_trading_calendar.py` | retired | 保留历史重建实现，入口拒绝执行 | `python -m stock_quant data update --root project` |
| `project/refresh_corporate_action_coverage.py` | migration | 刷新公司行为证据的历史辅助工具 | `python -m stock_quant data update --root project` |
| `project/refresh_index_membership.py` | migration | 将已下载成分快照转为成员事实 | `python -m stock_quant data index-membership prepare --root project` |
| `project/replay_price_arbitration.py` | diagnostic | 按观察价规则重放已发布冲突的结算结果 | 无替代项 |
| `project/test.py` | retired | 历史 SDK 试验片段，不得作为操作入口 | 无替代项 |
| `project/trim_universe_membership.py` | migration | 从冻结成员派生可交易子集 | 无替代项 |
| `project/verify_update_readiness.py` | diagnostic | 在网络更新前进行只读预检 | 无替代项 |

状态含义：`diagnostic` 只读或从既有工件派生诊断；`migration` 是受控、一次性
或历史迁移辅助；`retired` 保留以追溯历史，禁止作为操作入口。新增顶层脚本必须先
加入此表并标明状态。
