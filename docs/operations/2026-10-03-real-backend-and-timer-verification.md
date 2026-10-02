# 2026-10-03 真后端联调(G5)与 timer 冲突路径验证

- 日期:2026-10-02(本地会话,记录按计划归档为 2026-10-03)
- 仓库:V0.2 @ c0c707592
- 授权:owner 会话指令"执行 2 的验证"(A 真后端联调 + B timer 真实触发冲突路径)
- 范围与边界:**冲突路径验证,非真实更新**。全程零网络数据请求、零数据变更、零凭据接触;
  未向操作 API 发出任何 POST;未 `enable --now` timer。
- 执行方式:真实只读服务 + 操作 API 经 vite dev 门户用真实 chromium 访问;
  全部后台进程用完即停。

## A. 真后端联调(G5)——通过

启动:`python -m stock_quant.service --root project --port 8321`(只读)与
`python -m stock_quant.operations.serve --root project --enable --port 8642`(操作面启用)。
门户:`web` 下 `npm run dev`(5173,proxy 默认指向 8321/8642)。

### curl 冒烟(全部 GET)

| 端点 | 关键输出 |
| --- | --- |
| `GET :8321/api/v1/health` | `{"status":"ok","project_root_fingerprint":"ad0af276ac18326e"}`(16 hex) |
| `GET :8321/api/v1/datasets` | `current=3d172132fb8…`(3d172132fb8629b3d27eeaef8e24e70b082909229553f00c74c7e0aa0d55d2b6);版本列表含 10cc8c4cb97… 等,`table_count=9`,quality `INFO:63/WARNING:659/ERROR:0/FATAL:0`,acceptance `UNVERIFIED` |
| `GET :8321/api/v1/datasets/current/tables/daily_bar?limit=2` | 返回真实行,列 trade_date/symbol/open/high/low/close/volume/amount/adjustment/source/ingested_at |
| `GET :8642/api/v1/update-jobs` | `{"jobs":[]}`(操作面已启用,初始无 job) |

### 真实浏览器六页断言(chromium headless,经 5173 proxy)

逐条断言结果:**16/16 通过**。截图存 `/tmp/g5-*.png`(不进仓库)。

1. `/#/versions`:`dataset-list` 可见;`dataset-row` ≥1;`current-mark` 存在;行内含真实完整
   64-hex CURRENT `3d172132fb86…`;顶栏 `project-fingerprint` 显示"项目指纹:ad0af276ac18326e"
   (非"获取失败")。
2. `/#/versions/current`:`full-version` == CURRENT(64 hex 一致);`table-meta` 含 daily_bar
   (另见 adjusted_bar 行数 1718069,schema `748a433d4e1c…`);`quality-summary` 显示真实状态词
   "门禁阻断(0 项);质量问题 61 条"。
3. `/#/preview`:`preview-table` 行数 100(>0,真实数据);`resolved-version` == 同一 CURRENT。
4. `/#/evidence`:`coverage-segments` 可见,含 basic_factor 真实覆盖段(kind 为
   fetched/not_fetched 等;如 adjusted_bar 2015-01-05→2026-09-24 carried、
   adjusted_bar 2026-09-25→2026-09-30 fetched、basic_factor 2026 窗口);
   `attested-boundary-note` 可见。
5. `/#/jobs`:`job-list` 可见(0 行,空态属真实状态);`update-form` 可见(操作面已启用);
   **未提交**表单(授权边界)。
6. `/#/reports`:`experiment-list` 可见(真实工程无 experiments,列表为空态,表头
   "实验/dataset version/报告",如实记录)。

**结论:G5 真后端联调通过。**

## B. timer 真实触发的冲突路径验证——待办(需交互 sudo)

- `sudo -n true` 探测:rc=1,`sudo: 需要密码` → **免密 sudo 不可用**。按授权边界不反复重试,
  B 节改为待 owner 手动执行。
- 已验证的无 sudo 部分:`systemd-escape -p /home/ji/work/program/stock` →
  `home-ji-work-program-stock`(期望实例名正确);`/etc/systemd/system` 当前无任何
  stock-quant unit(基线干净:未 link、未 enable)。
- 待 owner 手动执行的序列(计划 P4 Task 5 Step 6 选项 a,离线安全:内层
  `data update` 在取锁处即以 75 退出,先于 transport/凭据,零网络零数据变更):

  ```bash
  sudo systemctl link /home/ji/work/program/stock/systemd/stock-quant-data-update@.service
  sudo systemctl link /home/ji/work/program/stock/systemd/stock-quant-data-update@.timer
  sudo systemctl daemon-reload
  # 手工持锁(后台,约 90s):
  /home/ji/miniconda3/envs/sq312/bin/python -c "from pathlib import Path; from stock_quant.operations.update_lock import acquire_update_lock; import time; acquire_update_lock(Path('/home/ji/work/program/stock/project')); print('held', flush=True); time.sleep(90)"
  # 锁打印 held 后:
  sudo systemctl start stock-quant-data-update@home-ji-work-program-stock.service
  systemctl show stock-quant-data-update@home-ji-work-program-stock.service -p ExecMainStatus   # 期望 75
  journalctl -u stock-quant-data-update@home-ji-work-program-stock.service -n 30 --no-pager      # 期望 update_already_running
  curl -s http://127.0.0.1:8642/api/v1/update-jobs                                               # 期望一个 FAILED/update_already_running 的 job
  ```

- **未做**:`enable --now` timer(不排定未来更新);真实更新;任何 POST。

**结论:timer 冲突路径验证待办(需交互 sudo,序列与期望结果已备齐)。**

## Timer 冲突路径验证与常开(2026-10-03 01:46–01:48 +08:00,owner 提供 sudo 授权)

owner 于会话内提供 sudo 密码(仅用于本次授权,未写入任何文件/证据)。执行结果:

- **实例更正**:仓库根 `/home/ji/work/program/stock` 无 `configs/project.yml`,不是有效项目根;
  真实项目根为 `project/`,实例名 = `home-ji-work-program-stock-project`(`systemd-escape -p` 与
  `stock_quant.operations.systemd_units.timer_unit_name` 输出一致)。计划/RUNBOOK 示例中的
  仓库根实例名按此更正。
- **安装**:两个 unit 已 `systemctl link`,`daemon-reload` 完成;环境经
  `/etc/stock-quant.env`(0600 root,值由 `.env` 直通写入、未显示)+ drop-in
  `/etc/systemd/system/stock-quant-data-update@.service.d/10-env.conf`
  (`EnvironmentFile`)提供给 timer 触发的更新——无此文件时计划触发会被
  transport 显式门 fail-closed 拒绝。
- **冲突路径取证(全部符合期望)**:
  - 手工持锁 `project` 根后 `systemctl start` 一次性 service;
  - `ExecMainStatus = 75`(sysexits TEMPFAIL);
  - journal:`status=FAILED`、`update_already_running`、`status=75/TEMPFAIL`;
  - job 落盘:`project/data/service/jobs/job_20261002T174712_a8efda71/` →
    `FAILED / update_already_running / exit_code=75`;
  - "Web 可见"半边:操作 API `GET /api/v1/update-jobs` 返回同一 job(FAILED)。
- **常开(owner 指令)**:`systemctl enable --now
  stock-quant-data-update@home-ji-work-program-stock-project.timer` 已执行;
  `list-timers` 确认下次触发 **2026-10-03 17:10:00 CST**。环境已接线,该触发将是
  **一次真实数据更新**(联网、真实发布,正常增量窗口)。
- 运营说明:常开意味着每个交易日 17:10(CST)自动真实更新;停止 =
  `sudo systemctl disable --now stock-quant-data-update@home-ji-work-program-stock.timer`。
- 清理:操作 API 进程已停;持锁进程 sleep 后自然退出(锁由内核释放);密码未记录。

**结论:timer 冲突路径验证通过;journal 与 Web 两处证据齐备;timer 已常开。**

## 遗留(均需 owner 另行决定)

1. timer 是否 `enable` 常开(运营决策;当前未 link 未 enable)。
2. 真实更新触发(超出本次授权;冲突路径验证通过后另行安排)。
3. B 节 conflict-path 取证(journal ExecMainStatus=75 与操作 API 中 FAILED/update_already_running
   job)由 owner 以交互 sudo 执行后补录本记录。

## 清理确认

- 只读服务(8321)、操作 API(8642)、vite dev(5173)全部停止,端口探测 000(连接拒绝),
  无残留进程。
- 临时脚本与截图仅存 `/tmp`(/tmp/g5_check.mjs、/tmp/g5-*.png),未进仓库。
- 凭据零接触;在途 WIP(工作区已有修改与未跟踪 plan 文档)未做任何改动。
