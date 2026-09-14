# 项目根运行时边界设计

## 目标

将量化项目的配置、数据和 raw store 明确绑定到执行时指定的**项目根**。默认
项目根是当前工作目录；代码仓库根只保存源代码、测试和非运行时模板，绝不能被
误当作项目配置或数据目录。

本设计消除仓库根与 `project/` 中两套 `configs/` 同时存在时的歧义。尤其是：
在工作项目把 `baostock.enabled` 设为 `false` 后，任何正常更新或诊断命令都不
得因为执行目录不同而再次请求 BaoStock。

## 非目标

- 不将默认项目根自动猜测为代码仓库下的 `project/` 目录。
- 不合并、同步或回退到两套运行时配置。
- 不改变数据源角色、transport 政策或数据发布门禁。
- 不改变仅审计代码仓库的工具；它们不读取项目配置或项目数据。

## 术语与不变量

- **项目根**：命令的 `--root` 值；未传时为当前工作目录 `.` 的绝对解析路径。
- **代码仓库根**：包含 Python 源码、测试、文档和模板的 Git 工作树；它不是
  运行时项目根。
- **运行时项目文件**：仅位于 `<project-root>/configs/` 与
  `<project-root>/data/` 下的配置和数据。
- 所有读取、写入和供应商调用必须从同一个经验证项目根派生；不得由脚本文件
  位置、代码仓库位置或向上搜索另行选择根目录。

## 项目根验证

新增唯一的项目根解析/验证入口，例如
`resolve_project_root(root: Path | str) -> Path`。它在一个函数中按固定次序完成
解析与验证：

1. 保留调用方传入的原始路径文本，并以 `Path.resolve()` 取得规范化绝对路径；
   `resolve()` **跟随符号链接**。
2. 检查解析路径存在且为目录；失败时抛出 `ProjectRootPathError`。
3. 检查该目录中至少存在：

- `configs/project.yml`
- `configs/sources.yml`
- `configs/costs.yml`

缺少任一文件时抛出 `ProjectRootConfigError`。两类错误均须包含原始路径、解析后
绝对路径；配置错误还须包含所有缺失文件的相对路径。这样诊断命令能明确区分
“路径不存在/不是目录”与“目录存在但不是完整项目”。验证发生在加载配置、创建
`DataPipeline`、打开数据集、创建 data source 或触发网络请求之前。因此在代码
仓库根误执行时，命令以非零退出，不会读取模板，更不会发起供应商请求。

`load_project_config()` 只接受这个入口验证过的项目根；所有调用者都经由它，而
非直接拼接任意 `<path>/configs`。一旦根被确定，`project.yml`、`sources.yml` 和
`costs.yml` 都只能从该根的 `configs/` 下读取；禁止相对路径、仓库根或模板目录
回退。测试夹具也必须创建并显式传入独立项目根。

## CLI 与脚本语义

所有面向项目的 CLI 命令保持 `--root`，默认值仍为 `.`：

```bash
# 在工作项目目录中：配置、数据和 raw store 都是 ./ 下的文件
cd /path/to/my-project
python -m stock_quant data update --start 2024-01-01 --end 2024-03-31

# 从其他位置操作同一个工作项目：路径仍是唯一真相
python -m stock_quant data update --root /path/to/my-project \
  --start 2024-01-01 --end 2024-03-31
```

不得把 CLI 默认根改为仓库内 `project/`，也不得在 `--root .` 失败后静默尝试它。

所有读取项目配置或项目数据的 `project/*.py` 维护脚本必须提供同样的 `--root`
参数，默认 `.`。配置、数据集、raw store 与输出路径均由解析后的项目根生成；
不得继续用 `Path(__file__).parent` 充当运行时根。只操作 Git 文档或代码树、不
读取项目配置/数据的工具不使用该入口。当前 `project/*.py` 的盘点结果是：**没
有**这类纯代码仓库审计工具；例如 `audit_raw_provenance.py` 虽不请求供应商，仍
读取 `<project-root>/data/raw`，因此必须使用项目根验证入口。未来若新增纯代码
仓库工具，必须在其模块文档中声明“不读取 `<project-root>/configs`、不读取
`<project-root>/data`、不调用供应商”，才可豁免。

`project/check_data_sources.py` 是项目诊断命令：它首先验证/加载项目根配置，
然后只对 `sources.yml` 中 `enabled: true` 的来源执行探针。禁用来源输出：

```text
baostock: SKIP disabled by config
```

它不得导入、构造、登录或请求该来源。该规则同样适用于日常 `data update`：
`baostock: false` 时它不在 enabled 集合中，即使 `--sources baostock` 也不得
绕过配置。所有可能触发供应商调用的命令（包括更新、bootstrap 的可选官方输入
校验、source 连通性诊断和独立收集脚本）都必须在首次构造 source 前经由同一
项目根验证入口并从该根加载来源开关。

## 模板迁移

代码仓库根的 `configs/` 迁移为 `templates/project-config/`。模板可被文档和新
项目初始化流程引用，但运行时项目根验证只查找 `configs/`，不会查找模板。
仓库根从此没有可被 CLI 当作项目配置的 `configs/` 目录。

README 说明：创建工作项目时复制该模板到目标工作目录的 `configs/`；随后从目
标目录运行命令，或始终传显式 `--root`。仓库根运行项目命令会失败，这是一项
安全性质而非兼容性回退点。

## 错误与数据安全

项目根验证失败不创建 data source、不读取或创建 raw store、不写 staging，且
不改变 `CURRENT`。这确保配置路径错误不会消耗 API 配额或污染其他项目的数据。

显式 `--root` 指向一个独立、完整工作项目时仍完全支持；不同项目的
`configs/`、`data/` 和 `CURRENT` 永远互不共享。

## 测试要求

- 单元测试覆盖项目根解析：完整工作项目成功；符号链接解析为目标目录；路径
  不存在/不是目录时给出 `ProjectRootPathError`，缺失每个必需配置文件时给出
  `ProjectRootConfigError`；两类信息都同时包含原始与解析后的绝对路径。
- CLI/管线测试覆盖：工作项目中的 `baostock: false` 不构造也不调用 BaoStock；
  `--sources baostock` 不会覆盖禁用状态。
- CLI 测试覆盖：从代码仓库根、且根目录不含运行时 `configs/` 时，项目命令在
  发起任何 source fetch 前以非零退出。
- `check_data_sources` 测试覆盖禁用来源的 `SKIP` 输出，并断言其构造器和
  `fetch` 均未执行。
- 至少一个临时外部项目根的端到端 CLI 用例证明显式 `--root` 仍读写该项目而非
  代码仓库。
- 现有测试夹具改为显式创建项目根；没有测试依赖代码仓库根的运行时 `configs/`。
