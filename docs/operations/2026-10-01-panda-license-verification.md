# Panda 侧许可证核验(Panda 数据闭环嫁接 · Phase 0)

- 日期:2026-10-01
- 核验人:operator + 实施agent
- 核验对象与结果(当日实查,含 HEAD hash):
  - pandaAI 根仓:`a783e69732da1f9ffc93844dc522375a1f67c507`,根目录含 `LICENSE`,读取首行确认为 **AGPL-3.0**(GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007;34,523 字节)。`setup.py` 无 license 字段(旁证缺席,以 LICENSE 文件为准)。
  - panda_quantflow:`688b90e74a738b84567efe622a2d9c1e5ce10e00`(独立 git 仓),根目录含 `LICENSE`,读取首行确认为 **AGPL-3.0**(同版本文本)。`pyproject.toml` 无 license 字段(旁证缺席,以 LICENSE 文件为准)。
  - panda-data:`f592d618b7b87f1002438fcbc17ac02d167ce971`(独立 git 仓),**无 LICENSE 文件**(全仓 find 无命中);`pyproject.toml` 与 `PKG-INFO` 元数据(Metadata-Version 2.4, `panda-data-tools` 2.0.0)均无 License/License-Expression 字段。按默认版权保留处理,即"无许可证"。
  - panda-data-skill:顶层 `/home/ji/work/program/pandaAI/panda-data-skill` 为一个 git 仓但**尚无任何提交**(main 分支初始状态,`git rev-parse HEAD` 无效,工作区仅含 `.git`),无 LICENSE;实际 skill 内容(SKILL.md、api_reference.md 等)位于 panda-data 仓内嵌目录 `panda-data/panda-data-skill/`,非独立 git 仓、无自身 LICENSE,随 panda-data 仓(`f592d618b7b87f1002438fcbc17ac02d167ce971`)一并按"无许可证"处理。

与既有"AGPL-3.0 / 无许可证"结论的比对:根仓与 panda_quantflow 为 AGPL-3.0,panda-data 与 panda-data-skill 无许可证,当日实查与该结论一致,未发现偏差。

## 裁定

- 选择 **clean-room 重写**(spec §5.1)。
- 允许记录:端点名、字段名、单位、输入输出样例、观察到的行为。
- 禁止复制:函数体、注释、异常文案、前端 bundle、模板、测试 fixture。
- 行为与字段语义优先引用供应商公开文档;Panda 代码只用于验证已观察到的兼容行为。
- 新实现的评审必须能仅凭规格、供应商公开文档和 Stock 测试解释其来源。
