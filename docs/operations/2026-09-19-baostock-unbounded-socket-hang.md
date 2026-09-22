# baostock 重启用后首次更新挂起 40+ 分钟：无超时裸 socket 缺陷与修复

- 日期：2026-09-19
- 数据集：`CURRENT = 01c74bee15b4…` 全日未变（四次更新尝试均未到达发布；无新版本）
- 触发：owner 当日在 `project/configs/sources.yml` 将 baostock 重启用（该源
  2026-09-05 起不可达、当日恢复应答），随后的全窗口更新
  （`data update --start 2015-01-05`，run 07:27 起）走入了
  `data_pipeline._fetch_validation_daily`。

## 事故

tushare/akshare 侧全部抓取完成（约 2670 个 raw 快照落盘）后，baostock 校验
日线的**第一条** `login()` 在 `sock.recv` 上无限挂起 40+ 分钟（采样栈停在
`sock_recv_guts`，TCP 到 `public-api.baostock.com:10030` 为 ESTABLISHED，
CPU 停止累计），进程被人工终止。按失败语义：无新数据集版本，
已落盘 raw 快照保留。

## 根因（SDK 0.9.3，`baostock==0.9.3` 钉版）

1. **无超时**：SDK 的 `socketutil` 直接 `socket.socket(...)` + `connect()` +
   `recv()`，从不设超时；`base.py` 的 `default_request_timeout` 只覆盖
   requests 系调用，其文档自述 baostock 是"stays as unbounded"的特例。
   `sources.yml` 的 `timeout_seconds: 30` 对它此前完全不起作用。
2. **吞错**：`socketutil.send_msg` 捕获一切异常，打印后返回 `None`：
   - `login()` 对 `None` 返回 `error_code=BSERR_RECVSOCK_FAIL`、
     `error_msg="网络接收错误。"` —— 适配器 `_raise_for_response` 可翻译为
     可重试 `ServerError`，此路径本来是安全的；
   - **分页路径不安全**：`ResultData.next()` 对 `None` 静默 `return False`，
     与正常取尽不可区分 —— 一次翻页超时会产生**截断的 DataFrame 且
     error_code 仍为 0**，违反"不得把错误静默变成干净数据"的不变量。

## 修复（`src/stock_quant/data_sources/baostock.py`）

1. `fetch()` 期间把进程级 `socket.setdefaulttimeout` 绑到
   `config.timeout_seconds`，`finally` 还原。抓取串行执行，与
   `default_request_timeout` 相同的"进程级、用完还原"论证。
2. `redirect_stdout` 捕获 SDK 打印（同时屏蔽 `login success!` 噪音）：
   命中失败标记（`服务器连接失败`／`接收数据异常`／`you don't login`／
   `当前页面编号不正确`）即抛 `ServerError`，把吞错转成显式可重试失败，
   杜绝截断帧。
3. logout 失败无害：每次 fetch 建新 socket，下一次 login 不继承坏连接。

## 验证

- 单测：绑定与还原（`applied == [30, None]`）、SDK 静音断言、
  吞错→`ServerError`、既有中文网络错误映射，全部通过
  （`tests/integration/test_source_contracts.py` 47 passed）。
- 黑洞地址实测（`BAOSTOCK_SERVER_IP=10.255.255.1`，
  `timeout_seconds=3`）：**9.0s 内有界 `ServerError`**（修复前为无限挂起）。
- 真实端点回归：`sz.002131` 2026-09-10..18 小窗口 3.8s 正常取 7 行。

## 同日环境故障（与本缺陷无关，未结）

09:40 起本机出网劣化，当日共 6 次更新尝试全部未发布：

1. **Clash 系统代理对 `jiaoch.top` 的 TLS 握手长时间停摆**（faulthandler 栈实
   证卡在 `do_handshake`→`poll`，远超 30s 请求超时），`no_proxy='*'` 绕开后
   握手正常。
2. **relay 对持续调用有长惩罚限速**：短促探测全天 15/21 成功（1–17s，探测间
   有间隔），但更新进程的连续抓取循环自 run 2 起无一完成。run 1 耗尽配额后，
   后续每次尝试都在慢速滴漏/停滞中空转（run 7 实测 15 分钟 CPU 仅 +2s）。
   重启越频繁，恢复越慢。注意：raw 存储对相同内容去重跳过写入，重抓进度
   不能用文件 mtime 判断（当日多次误判即由此起）。
3. **（更正早间记录）relay 的 `trade_cal` 覆盖完整 9 月**（09-01..09-30，
   21 个开市日）。早间"只到 09-01"是按恒定列排序后取 `iloc[-1]` 的误读，
   `resolved_end_date=2026-08-28` 实为已发布数据集自身的日历终点。日历不是
   障碍——唯一待解的是上述限速冷却。

### 当日顺手修复（run 5 的直接教训）

`000712.SZ` 的 daily 响应被 relay 中途掐断（`"Response ended prematurely"`），
`translate_supplier_error` 的瞬时标记表没有该词，被误判为永久错误、零重试
直接 FATAL。已在 `base.py` 的 `server_markers` 补上 `prematurely`／
`incomplete`／`disconnected`（协议级瞬断），使既有的 max_retries=3 策略按
设计生效；分类单测见 `tests/unit/test_source_retry.py`。

### 重跑决策规则

长冷却（隔夜级）之后的**当天第一次**尝试是唯一可靠窗口（run 1 即为此种条
件）；当天内反复重启只会重置冷却。启动前先用 2–3 次带间隔的 daily 短探测
确认 relay 应答正常，随后一次性跑完。raw 快照在盘，重跑安全。
