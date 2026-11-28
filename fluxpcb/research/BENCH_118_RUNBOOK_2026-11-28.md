# PCBWorld-Bench 118/119 工况：测试与对比 PCBRouteAgent 的完整 runbook

> 2026-11-28 · 对应代码 `02a65b3`（fluxpcb，autonomy-loops-20261124）
> 基准脚本 `23f2e03`（autorouter-bench，bench-autonomy-loops-20261124）
> 结果与方法详见 [`PCBWORLD_2026-11-25.md`](PCBWORLD_2026-11-25.md)、
> `autorouter-bench/pcbworld/RESULT_2026-11-25.md`。

## 0. 一句话

在 PCBWorld-Bench 的 118/119 块真实板上，每块板跑 **3 个独立格子**，判据与
PCBRouteAgent 完全一致（**error 级 DRC 违规 = 0 且未连接 = 0**）；当前全量结果是
D3-A 297/297、D3-B 30/30、D3-C 24/30（80%），357 格全部有证书、仪器问题 0。

## 1. 基准是什么，118 与 119 的区别

PCBWorld-Bench 有三个官方测试集，共 **119 块真实板**：

| 集合 | 板数 | 性质 |
|---|---|---|
| D3-A | 99 | 小板 |
| D3-B | 10 | 中等板 |
| D3-C | 10 | 难板 |

仓库里有两个 manifest，别混用：

| 文件 | 板数 | 用途 |
|---|---|---|
| `autorouter-bench/pcbworld/manifests/boards_full.json` | **118**（D3-C 9） | 早期 118 工况 campaign |
| `autorouter-bench/work/pcbworld/boards_full.json` | **119**（D3-C 10） | 2026-11-25 最终确认运行 |

“118 个工况”指前者；最终 357 格 = 119 板 × 3 次。两者除 D3-C 的 `0473` 外是同一批板。
**报数时必须写清用的是哪个 manifest 和哪个 commit**，否则 118/119、354/357 会对不上。

## 2. 目录、前提与环境

板子、routed 输出、DRC 报告和缓存约 1.7 GB，**不在 git 里**。开发机上数据根目录在
仓库外，`work/pcbworld` 通过链接指向它；脚本按自身位置解析 `boards/`、`runs/`、
`rounds/`，所以要在数据根目录下运行。

需要：

- KiCad 10：`kicad-cli` 与自带 Python（能 `import pcbnew`）；
- JDK 25 + `freerouting-2.5.0.jar`；
- 本仓库的 `fluxpcb` 包（脚本会把它加进 `sys.path`）；
- 环境变量：`KICAD_CLI`、`KICAD_PYTHON`、`FLUXPCB_JAVA_XMX=2g`。

JVM 堆上限是**必需**的：不设时 6 个并行路由器会耗尽内存，两块最大的板（0496/0599）
会无证书消失。加上限后 357 格零崩溃。

## 3. 怎么跑（命令）

```bash
cd autorouter-bench/pcbworld

# 0) 仪器自检：一块板、一个 arm，先证明仪器本身正常
python arms_run.py --run <run_dir> --selfcheck

# 1) 全量：manifest 里的板 × 3 个独立格子（每格一个子进程）
#    BUDGET 是每格墙钟预算；WORKERS 是并行格子数
FLUXPCB_JAVA_XMX=2g BUDGET=900 WORKERS=6 \
  python full_run.py <run_dir> full4 3

# 2) 出对比表：ERROR 口径与 STRICT 口径（悬空线头/过孔计入）
python table_md.py <run_dir> full4

# 3) 只重算分数（已有 runs/ 目录时）
python score_runs.py --manifest <manifest> --runs <run_dir>/runs \
  --out <run_dir>/score_rerun.json
```

- 单板调试：`run_bench.py --manifest <one-board.json> --splits d3a,d3b,d3c ...`；
- 断点续跑：`full_run.py` 会跳过“arm digest + 代码指纹”都新鲜的格子；
  `run_bench.py --append` 用于单板层级的续跑；
- D3-C 的 0496 会撞 900 s 预算，这是**测量结果**（budget），不是仪器故障。

## 4. 判据（逐字）

**每个格子**：

1. 在板子自己的 `circuit.kicad_pro` 下跑
   `kicad-cli pcb drc --format json --severity-error --refill-zones`；
2. 评分前隔离任何 `.kicad_dru`（修复生成的 waiver 不能当裁判）；
3. `errors == 0` 且 `unconnected == 0` → `clean`；否则 `fail`；
   撞墙钟预算 → `budget`（没证书，也计入分母）。

**STRICT 口径**额外要求悬空走线/过孔为 0（`track_dangling` / `via_dangling`），
它来自同一块板的 `--severity-all` 报告。

**板级**：每板 3 个独立格子（`<arm>~<label>r0/r1/r2`，不同进程）。

- “每次都过”（every time）= 3 格全 clean —— **上表用的是这个最严口径**；
- “至少一次”（any time）= 至少 1 格 clean。

## 5. 当前结果（2026-11-25，代码 `02a65b3`）

| 集合 | 板数 | fluxpcb（格子） | 每次都过 | 至少一次 | PCBRouteAgent |
|---|---|---|---|---|---|
| D3-A | 99 | **297/297 = 100.0%** | 99/99 | 99/99 | 94.9% |
| D3-B | 10 | **30/30 = 100.0%** | 10/10 | 10/10 | 100% |
| D3-C | 10 | **24/30 = 80.0%** | 8/10 | 8/10 | 40% |

- 357 格全部有证书，仪器问题 0、零崩溃；
- 71 分钟跑完；格子墙钟中位数 29 s；D3-A 平均 27 s（最长 88 s），
  D3-B 平均 104 s（最长 668 s），D3-C 平均 327 s（最长 1021 s）；
- STRICT 口径结果相同：A 297/297、B 30/30、C 24/30；
- 没过的两块板都在 D3-C：`0496`（预算 ×3，无证书）与 `0599`（27 错 + 28 开路，三次一致）。

## 6. 与 PCBRouteAgent 的对比口径

| | PCBRouteAgent | fluxpcb |
|---|---|---|
| 论文/实现 | arXiv:2611.08175v2，Opus 5.5，KiCad PNS + 重试 | 规则流水线（这条路径**不调用 LLM**） |
| 路由器 | KiCad 9.0.8 推挤引擎 | Freerouting 2.5.0，2 线程 15 遍，240 s 超时；超时后 8 线程再跑一次 |
| 修复 | LLM 智能体重试 | 规则算子 + 事务门，每板最多 3 轮 |
| 每板时间上限 | 未披露 | 900 s（+120 s 收尾） |
| 判据 | error 级 DRC + 零未连接 | 同 |
| 重复 | 每板 1 次 | 每板 3 个独立格子 |

对比目标（立项时定的）：**D3-A 与 D3-C 严格高于 PCBRouteAgent，D3-B 至少打平，
且在 3 次重复下成立**。上表满足：D3-A 100% > 94.9%，D3-B 100% = 100%，
D3-C 80% > 40%；每一项都是“每次都过”口径。

必须如实说明的三点：

1. 没有 PCBRouteAgent 的逐板通过名单，**只能比集合通过率，不是配对检验**；
2. 它每板跑一次、时间上限未披露；我们每板三次并按最严口径报；
3. DRC 在脏板上有随机漏报（同一文件多次运行读数可差数处），但“零错误”不会被
   漏报成“零错误”，通过/不通过的方向不受影响。

## 7. 常见坑

- **`python3` 不存在**：`run_bench.py` 默认用它跑 pcbnew 助手，Windows 上每格 0.2 s
  以 `router_failed` 结束；现在回退到当前解释器（且 `KICAD_PYTHON` 要指对）。
- **JVM 堆**：不设 `FLUXPCB_JAVA_XMX=2g`，并行大板会 OOM 崩溃。
- **陈旧证书**：driver 在跑之前会删掉旧的 `cert.json` 与 routed 板；自己写脚本时必须
  照做，否则会把上一轮的判决当本轮结果。
- **`unconnected_items: ignore`**：项目设置若把未连接设为 ignore，DRC 报告里根本
  不会有这项；评分脚本用 `--severity-error` 并在自己的报告里数 `unconnected_items`，
  不能被项目设置“校准掉”。
- **waiver**：评分前把 `*.kicad_dru` 挪走，修复生成的规则不能参与裁判。

## 8. 结果文件在哪

```
<run_dir>/
  cells/<board>__<arm>.json        # 每个格子的证书/分数/仪器检查
  summary_<label>.json             # 每 arm 的 clean/fail/budget/instrument_bad
  runs/<board>/
    <board>.kicad_pcb              # routed 输出
    cert.json                      # 布线+修复的事务证书
    routed-*.scr                   # 路由器原始输出
score_rerun.json / score_rerun_summary.json
```

`cells/*.json` 里带 `code_fp`（代码指纹）与 `loaded`（该格实际导入的文件），
换代码后旧的格子不会被误当新鲜结果。

## 9. 检查清单（发布一组数之前）

- [ ] `arms_run.py --selfcheck` 通过；
- [ ] `summary_<label>.json`：`cells == expected`，`instrument_bad_cells == 0`；
- [ ] 每板 3 格都在（`r0/r1/r2`），没有“消失的分母”；
- [ ] ERROR 与 STRICT 两张表都出，且结论一致；
- [ ] 失败板逐一点名并给出残留（0496 预算、0599 的 27+28）；
- [ ] 写清 manifest（118/119）、代码 commit、基准脚本 commit、JVM/预算/工人数。

## 10. 参考

- 结果与方法：[`PCBWORLD_2026-11-25.md`](PCBWORLD_2026-11-25.md)
- 实验记录 §6.12（逐算子事务负结果、JVM 上限）与 §6.13（最终确认）：
  [`EXPERIMENTS_2026-11-25.md`](EXPERIMENTS_2026-11-25.md)
- 基准侧：`autorouter-bench/pcbworld/RESULT_2026-11-25.md`、
  `FLUXPCB_RUNS.md`、`docs/ROUNDS_20261124.md`（含 109/118 轮次结果及其更正）
