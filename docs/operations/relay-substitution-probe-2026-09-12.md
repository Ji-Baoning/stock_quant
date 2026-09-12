# 静默换源探针报告（2026-09-12）

来源：`project/probe_relay_substitution.py`（设计规格 §3 ③ 最小版本，落地阶段 0）。

**结论：全部用例给出证据，阶段 1 闸门放行**

即：本次运行两行判定全部 `AGREE_EMPTY`，无阻断差异、无 `INCONCLUSIVE`，退出码 `0`。
这是按 owner 裁决把 `unknown_api_name` 用例移出 `CASES`、改派阶段 3 后重跑的稳定结果（前因见 `## 处置历史`）。

判据：官方合法返回空 / 合法报错的请求，relay 必须给出**同为空的载荷**或**逐字相同的错误串**。

- `SUBSTITUTION` = relay 在官方无数据处返回了非空；
- `ERROR_DIFFERS` = 官方给出了它**本该给出**的参照错误，而 relay 的错误串与它不一致。

两者都属于 spec §3 ④ 的阻断条件。`INCONCLUSIVE` 表示官方侧本身没给出可用答案（限流、网络失败，或该问法官方本来就有数据），该用例**不构成证据** —— 既不算通过，也不算失败。**闸门只在全部用例都给出证据时才放行**，所以 `INCONCLUSIVE` 同样让阶段 1 保持关闭。

错误串判定的规则（`classify_error_probe` 的契约，现无用例行使）：只有官方答出参照错误串（`请指定正确的接口名`）时，relay 的错误串才成为证据。官方若因自身原因报错 —— 凭据被拒、限流、网络故障 —— 它答的是另一个问题，该用例一律记 `INCONCLUSIVE`（退出码 2），**不得**记 `ERROR_DIFFERS`。否则一份过期的 `.env` 就足以把可用的 relay 判成阻断条件。行使该规则的 `unknown_api_name` 用例已由 owner 于 2026-09-12 裁决移出 `CASES`，改由阶段 3 的传输保真脚本在可控额度窗口下行使（闸门因此可稳定重跑到退出码 0）；移出**不撤回**当时记录的实测事实 —— jiaoch 网关对不认识的 `api_name` 回显 relay key 并用自身鉴权文案作答，与官方的参照错误串不一致。

| 用例 | endpoint | 官方作答 | relay 作答 | 判定 |
| --- | --- | --- | --- | --- |
| `nonexistent_symbol` | `daily` | ok: 0 rows | ok: 0 rows | **AGREE_EMPTY** |
| `window_before_listing` | `daily` | ok: 0 rows | ok: 0 rows | **AGREE_EMPTY** |

## 处置历史：未知接口名差异（2026-09-12）

**这不是本探针发现的换源行为，而是一处已知、稳定、与数据无关的网关差异。** 下面
记录它的观测与两次处置；把它从用例表移走**不撤回**这些事实。

**观测**：jiaoch 的网关对任何它不认识的 `api_name` 直接回
`token不对，您传过来的是<KEY>请确认` 并回显我们提交的 relay key，**不转发上游**；
官方同名请求回 `请指定正确的接口名`。三个不同的假接口名
（`not_a_real_tushare_endpoint` / `foo_bar_baz` / `daily_`）全部复现同一应答，因此这是
**稳定行为**而非偶发。判定为网关的输入校验路径，不是换源作答：官方合法返回空的用例
全部 `AGREE_EMPTY`（本次重跑为 `nonexistent_symbol` / `window_before_listing` 两例），
填充数据此前有 4/4 逐位一致的记录。

**第一次处置（2026-09-12，已被推翻）**：owner 按 spec §3 ④「由 owner 显式人工豁免」
把该 `ERROR_DIFFERS` 记为已豁免差异，阶段 1 在豁免范围内可以开始。

**第二次处置（2026-09-12，现行）**：owner 于同日晚些时候改口，把 `unknown_api_name`
用例**从阶段 0 的 `CASES` 移出、改派到阶段 3 的轴 1 传输保真脚本**
（`verify_transport_fidelity.py`），使阶段 0 闸门可稳定重跑到退出码 `0`。**移出不是
放弃观察**：该错误串比对在阶段 3 的**可控官方额度窗口**下继续进行，比对官方的参照
错误串与 relay 的作答 —— 与 `index_daily` 保真检查同一条理由。留住的是**机制**：
`classify_error_probe`、`DIFFERS`/`ERROR_DIFFERS`、其在 `BLOCKING` 中的成员资格，以及
`_verdict` 的 `"error"` 分支，全部保留在探针里（它是 spec §3 ④ 的契约）、有单测，
只是不再被阶段 0 的 `CASES` 触达。

**移出的作用域**：只让这一个**已知、非换源**的差异不再把阶段 0 闸门一直卡住；它不把
退出码 `1` 一般化为「可以继续」，也不改变下面「关于 `index_daily`」与「证据上界」的
结论。`SUBSTITUTION`、以及任何**新的或不同的** `ERROR_DIFFERS`，仍然是硬阻断。

## 关于 `index_daily`

`index_daily` 的空结果 / 换源行为**不在本探针的用例表内**，也不应加入：免费 token 的
额度是 1 次/小时，会让阶段 0 这个硬闸门变成随机阻塞而不是有效证据（`trade_cal` 的
1 次/小时按同一原则排除）。该项移至阶段 3 的传输保真脚本，在可控的官方额度窗口下检查。

## 证据上界

本探针**只能证伪、不能证实**：它排除了「对空结果换源作答」这一条通道，
**不证明** relay 的上游就是 tushare 官方。逐位一致与逐字相同的错误串
同样只是「输出与官方不可区分」级证据，不是「上游即官方」的证据。

## 复现

```bash
python project/probe_relay_substitution.py  # 2026-09-12
```
