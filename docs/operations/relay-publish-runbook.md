# 上线当天 relay 发布运行手册

来源：`docs/superpowers/plans/2026-09-12-data-source-role-division.md` Task 6 Step 4–6。

本手册把计划里 Task 6 的三步人工验收搬出计划文档，供上线当天直接执行。
**这三步是阶段 1 的人工验收，不能由测试代替**：Task 6 的集成测试
（`tests/integration/test_raw_provenance_chain.py`）只覆盖证据链的**内部一致性**，
不覆盖真实供应商下的发布，也不覆盖「未指定 transport 必须失败」。运行者按顺
序执行并留痕。

相关证据：换源可替换性见 `docs/operations/relay-substitution-probe-2026-09-12.md`
（含 owner 对该探针唯一阻断差异的显式豁免），存量快照出处见
`docs/operations/raw-provenance-audit-2026-09-12.md`。

## 环境准备

密钥只从本仓库的 `.env`（已 gitignore）读入，**不要把任何真实值写进本文件或
`.env.example`**；下述命令只导出、不回显。

```bash
cd /home/ji/work/program/stock
set -a; . ./.env; set +a
export TUSHARE_TRANSPORT=relay
```

> **`--root project` 是必须的。** `data update` 的 `--root` 默认是 `.`，而真实
> 数据在 `project/data/` 下；不加这个参数会写到仓库根的新目录里，既不是本次发
> 布、也不会被后续任何东西读到。

## Step 4：真实发布（人工，不能由测试代替）

```bash
cd /home/ji/work/program/stock
set -a; . ./.env; set +a
export TUSHARE_TRANSPORT=relay
/home/ji/miniconda3/envs/py310/bin/python -m stock_quant data update --root project
```

**Expected:**
1. 终端出现 `INFO stock_quant.data_sources.tushare_transport: tushare transport: kind=relay host=...`；
2. 命令以 `PASS` 结束并打印新的 `dataset_version`。

**失败时：** 未出现 `kind=relay` 那行 —— 检查 `TUSHARE_TRANSPORT` 是否在 `.env`
之外被显式导出（本步的 `export` 就是为此）；命令未以 `PASS` 结束 —— 读非零退出
码上方的 `FAILED: ...` 行定位原因，**不要**为了让它通过而放宽闸门或改数据。若
relay 本身不可达，见探针报告的结论与豁免范围。

## Step 5：反向验证（未指定 transport 必须失败且不动数据集）

```bash
cd /home/ji/work/program/stock
set -a; . ./.env; set +a
env -u TUSHARE_TRANSPORT /home/ji/miniconda3/envs/py310/bin/python -m stock_quant data update --root project
```

**Expected:** 退出码 1，报错含 `TUSHARE_TRANSPORT must be set explicitly`，数据集版本不变。

**失败时：** 若命令**未**失败（即无 transport 也能发布），说明闸门被绕过 —— 这是
阻断项，停止发布并排查 `resolve_transport` 的发布路径；若失败但触发了网络或改动了
数据集，同样阻断。先在执行前后各记一次 `dataset_version` 以便比对。

## Step 6：从证据侧自证（不能只看终端）

真实布局是 `project/data/standardized/<version>/`，版本号记在
`project/data/standardized/CURRENT` 里；`build_config` 不是独立文件，而是
`dataset_manifest.json` 的一个字段。下面这段按真实布局读这三件事，不需要人工填版本号。

```bash
cd /home/ji/work/program/stock
/home/ji/miniconda3/envs/py310/bin/python - <<'PY'
import json
from pathlib import Path

from stock_quant.data_sources.raw_store import RawSnapshotEvidence, RawStore
from stock_quant.research.acceptance.models import RawSnapshotBinding

root = Path("project")
version = (root / "data" / "standardized" / "CURRENT").read_text().strip()
manifest = json.loads(
    (root / "data" / "standardized" / version / "dataset_manifest.json").read_text()
)
rows = manifest["build_config"]["raw_snapshots"]
store = RawStore(root)
labels = set()
for row in rows:
    if row["source"] != "tushare":
        continue
    binding = RawSnapshotBinding.model_validate(row)
    snapshot = store.verify_evidence(RawSnapshotEvidence(**binding.model_dump()))
    labels.add(snapshot.manifest["supplier_endpoint"])

print(f"version={version} tushare_rows={sum(r['source'] == 'tushare' for r in rows)}")
print(sorted(labels))
assert labels, "no tushare snapshots were bound"
assert all(label.startswith("tushare_relay.") for label in labels), labels
print(f"OK: all {len(labels)} distinct tushare labels resolve to the relay")
PY
```

**Expected:** 打印的标签全部以 `tushare_relay.` 开头，**无一为 `tushare.pro.`**。
同理，`transport_id` 全部是 relay 的作答 host，没有 `api.waditu.com`。

**失败时：** 先 `ls project/data/standardized/` 与
`ls project/data/standardized/<version>/` 核对实际布局，再改脚本 —— **不要改断言**。

> **不要拿现存的那条 acceptance 记录来验 Step 6。** 本检出里已有一条发布过的记录
> （`project/data/acceptances/<CURRENT 版本>/acceptance.json`，93 条证据行），它
> **全部是五字段旧行、不含 `transport_id`** —— 那是改造前发布的，「标签全部以
> `tushare_relay.` 开头」在它身上**必然不成立**。Step 6 只对**改造后新发布的那一版**
> 成立。别为了让旧记录“通过”去改它，也别去重写 `project/data/` 下的任何东西：那条
> 记录的价值恰恰在于它老。存量快照的出处分布见前述审计报告。
