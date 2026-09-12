# 静默换源探针报告（2026-09-12）

来源：`project/probe_relay_substitution.py`（设计规格 §3 ③ 最小版本，落地阶段 0）。

**结论：命中一处阻断差异，已由 owner 显式豁免（见 `## owner 豁免`）。阶段 1 在豁免范围内可以开始**

即：三行判定里两行 `AGREE_EMPTY`，一行 `ERROR_DIFFERS`。该行不是本次运行新发现的
问题，而是已复核、已定性的稳定行为，owner 已于 2026-09-12 按 spec §3 ④ 显式豁免。
**豁免只针对这一例的这条差异** —— `SUBSTITUTION`、以及任何新的或不同的
`ERROR_DIFFERS`，仍然是硬阻断。

判据：官方合法返回空 / 合法报错的请求，relay 必须给出**同为空的载荷**或**逐字相同的错误串**。

- `SUBSTITUTION` = relay 在官方无数据处返回了非空；
- `ERROR_DIFFERS` = 官方给出了它**本该给出**的参照错误，而 relay 的错误串与它不一致。

两者都属于 spec §3 ④ 的阻断条件。`INCONCLUSIVE` 表示官方侧本身没给出可用答案（限流、网络失败，或该问法官方本来就有数据），该用例**不构成证据** —— 既不算通过，也不算失败。**闸门只在全部用例都给出证据时才放行**，所以 `INCONCLUSIVE` 同样让阶段 1 保持关闭。

注意 `unknown_api_name` 的判定：只有官方答出参照错误串（`请指定正确的接口名`）时，relay 的错误串才成为证据。官方若因自身原因报错 —— 凭据被拒、限流、网络故障 —— 它答的是另一个问题，该用例一律记 `INCONCLUSIVE`（退出码 2），**不得**记 `ERROR_DIFFERS`。否则一份过期的 `.env` 就足以把可用的 relay 判成阻断条件。

| 用例 | endpoint | 官方作答 | relay 作答 | 判定 |
| --- | --- | --- | --- | --- |
| `nonexistent_symbol` | `daily` | ok: 0 rows | ok: 0 rows | **AGREE_EMPTY** |
| `window_before_listing` | `daily` | ok: 0 rows | ok: 0 rows | **AGREE_EMPTY** |
| `unknown_api_name` | `not_a_real_tushare_endpoint` | error: 请指定正确的接口名 | error: token不对，您传过来的是<redacted>请确认 | **ERROR_DIFFERS** |

## owner 豁免（2026-09-12）

`unknown_api_name` 一例的 `ERROR_DIFFERS` 已按 spec §3 ④「由 owner 显式人工豁免」处置。

**实测依据**：jiaoch 的网关对任何它不认识的 `api_name` 直接回
`token不对，您传过来的是<KEY>请确认` 并回显我们提交的 relay key，**不转发上游**；
官方同名请求回 `请指定正确的接口名`。三个不同的假接口名
（`not_a_real_tushare_endpoint` / `foo_bar_baz` / `daily_`）全部复现，因此这是**稳定
行为**而非偶发。判定为网关的输入校验路径，不是换源作答：三个「官方合法返回空结果」
的用例全部 `AGREE_EMPTY`，填充数据此前有 4/4 逐位一致的记录。

**豁免的作用域只有这一例的这条差异**。它不把退出码 `1` 变成「可以继续」：
`SUBSTITUTION`、以及任何**新的或不同的** `ERROR_DIFFERS`，仍然是硬阻断。

**这一豁免同时撤回了什么**：spec 里两处「未知接口名错误串与官方逐字相同」的断言已被
本探针推翻，同轮修正（见 spec `### 修正：未知接口名错误串`）。「输出与官方不可区分」
这条结论的支撑因此只剩逐位比对、日历核对与权限层级三项，错误串不再是其中一项。

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
