---
status: accepted
date: 2026-10-02
decision: "§7.0.10 采用 B Iceberg 式单指针，两条路径不共存。schema-v2 固定 membership_hash_scope: universe_id，membership_table_sha256 只哈希目标 slice 的 canonical facts；coverage_segments 与带证据哈希的 gap 进入定义内容，version 为 canonical JSON SHA-256（exclude_none 序列化，v1 字节不变）；v2 下验收与 runner 先选 slice 再检查，空 slice 以 UNIVERSE_SLICE_EMPTY 稳定失败，跨 gap 窗口以 universe_gap_in_window fail closed；data update/data validate 仍对整表跑 schema 与事实校验。"
affects:
  - src/stock_quant/research/universe.py
  - src/stock_quant/data_model/universe_membership.py
  - src/stock_quant/research/acceptance/checks.py
  - src/stock_quant/research/runner.py
  - src/stock_quant/data_model/membership_refresh.py
  - src/stock_quant/data_model/dataset.py
  - src/stock_quant/cli.py
---

# ADR-024: universe membership 切片哈希与 schema-v2 定义

日期:2026-10-02
相关：spec §7.0/§7.1/§2.2/§11；ADR-003；ADR-022（attested-boundary 近似契约）

## 决策
1. schema-v2 固定 `membership_hash_scope: universe_id`：`membership_table_sha256`
   只哈希 `frame[frame.universe_id == definition.universe_id]` 的 canonical
   facts（`membership_slice_hash`）。v1 整表语义原样保留、仅用于旧重放，
   无隐式 fallback：v1 文档携带 v2 键即拒绝加载。
2. `coverage_segments`（各带 evidence_sha256）与 gap 列表（reason 固定
   `membership_observation_gap` + 证据哈希）进入定义内容；`version` = canonical
   JSON SHA-256（exclude_none 序列化，v1 字节不变）。`coverage_start/end` =
   段包络；段重叠、事实落段外、gap 与段不互补 → 拒绝加载/拒绝验收。
3. v2 下验收与 runner 先选 slice 再做 schema/事实/coverage/cardinality/hash
   检查；空 slice 稳定失败（UNIVERSE_SLICE_EMPTY）；runner 传给
   _facts_from_membership_frame/resolve_memberships/UniverseResolver 的必须是
   同一 slice；跨 gap 窗口以 `universe_gap_in_window` fail closed。
   `data update`/`data validate` 仍对整表跑 schema 与事实校验。
4. 注册表 `configs/universes/versions/<definition_version>.yml` 不可变、
   append-only；显式 `universe_version` 从注册表解析并复核内容哈希，只有
   `CURRENT` 规格解析顶层文件；顶层仍是完整定义（非递归扫描、universe_id
   不得重复、指针文件阻断发布）。
5. mixed-lineage 迁移（§7.0.5/6）：首次发布前，启用且在目标数据集有非空
   slice 的顶层定义一次性升 v2 并重算 slice hash（定义 version 必变，已发布
   实验不变，CURRENT 源规格下次冻结新 version，显式旧 version 走注册表）；
   无 slice 的启用定义保持 v1 并移入 archive/，且只许在剩余定义最小
   coverage_start 不变时进行。
6. refresh 崩溃一致性：prepare（新 dataset + 新定义不提升；注册表先追加）→
   commit（见裁定段）→ recovery（`data/.membership_generation.json` 与磁盘
   不一致时不猜、稳定错误码，`data index-membership recover --to
   <generation>` 由 operator 显式选择）。崩溃注入覆盖 prepare 后 / CURRENT
   替换后 / definition 替换后（或单指针的派生缓存落后），各收敛到自洽
   generation；重跑幂等；回滚 = 注册表文件替换。
7. `collected_at` 是 provenance 列：不进 `membership_content_hash` 与定义
   version，首次落盘固定、重放不更新（§2.2）。

## 协议 B（Iceberg 式单指针）
generation 文件是唯一权威指针：读取路径先解析它（单文件 os.replace 原子），
CURRENT 与顶层定义是 refresh 替换 generation 后尽力重写的派生缓存，必须整写
完整定义以满足装载器约束。commit 一步：原子替换 generation 文件。三中断点
退化为"派生缓存落后于 generation"的自愈：校验只判 generation 可解析且其
dataset 目录/注册表条目存在；缓存落后时重写而非报错；读 CURRENT 一律经
`current_dataset_version()` 间接。

## 后果
- 未选协议段与对应代码路径在裁定时删除；`COMMIT_PROTOCOL` 常量固定为被选
  值，两条路径不得共存。
- P4 常驻服务启动时的 generation 校验挂钩由 P4 阶段计划接线（本 ADR 记义务）。
