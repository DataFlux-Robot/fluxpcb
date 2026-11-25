# 随机 5 块板：物理审计链的通用性（2026-11-25）

0376 的审计链配的是**手写 spec**。这份记录回答：换 5 块随机板、不给任何电流/功耗/
θ，这条链还成立吗？结论先说：**7/7 块板（5 随机 + 2 定向）full 跑通、0 崩溃、页面与
JSON 齐全；5 块里 4 块铺铜前后 DRC 0→0，1 块（0599，基准本身的失败板）铺铜 +1
个 error，审计把该板标为"铺铜不可信"并照常报出两条开路轨。** 能测的（电阻、每安培
J、平面覆盖率、回流缺口、模频、耦合）都是铜皮上的测量；不能测的（绝对电流、结温）
一律 `unknown` 并写明缺什么。

## 一、抽签（可复现）

```python
ids = sorted({json.load(open(p))['board'] for p in
              glob.glob('E:/DATA/vscode/ZCODE/z_lab/fp/full4_run/cells/*__9a83911c1baa.json')})
random.Random(20261125).sample(ids, 5)
```

池 119 块，种子 **20261125**，抽到（按抽取顺序）：

| 板 | 面积 mm² | 层 | 网 | 焊盘 | 接地网名 | 备注 |
|---|---|---|---|---|---|---|
| 0376_cat-trainer_teensy_base_pcb | 3631.5 | 2 | 26 | 98 | GND | 已用手写 spec 审过 → **校准行** |
| 0599_RGBMatrixPanelCPLD… | 4235.3 | 2 | 45 | 269 | GND | 基准 D3-C 失败板（27 error + 28 开路） |
| 0002_rufs_simple… | 406.5 | 2 | 3 | 6 | GND | 最小板（VCC 一条轨） |
| 0041_FogDrive_attiny45_slim | 314.5 | 2 | 7 | 21 | GND | 无电压名电源轨（只有 +BATT） |
| 0058_scimpy_powersupply | 2434.8 | 2 | 5 | 26 | GND | ±15 V 双电源 |

池子里只有 1 块四层板（0067）且没抽中；5 块都有字面 `GND`（没覆盖"无 GND 名"和
"四层"两条路径）。按计划补两块**定向检查**（不占随机 5 块）：

| 补充板 | 为什么 |
|---|---|
| 0067_radio_saw_dcc6c | 唯一四层板；内层名 `2_gnd`/`3_gnd` |
| 0400_laptimer58_Chickadee | 接地网叫 **GNDPWR**，没有字面 GND |

## 二、为通用性改了什么

1. `tools/netnames.py`：`detect_ground` 按"字面 GND → 含 gnd/gndpwr/dgnd/agnd/pgnd/vss/0v
   （不分大小写）→ 焊盘最多的非电源网兜底"选接地网，`why` 写明依据；`rail_candidates`
   只认电压名或电源词，**排除 `Net-(...)`**；`parse_voltage` 认 `+3V3/-15V/3.3V`。
2. `phys_audit`：接地网贯通 `pour_plane`、`availability`、`solve_return`、`plane_voids`、
   `_return_maps`、`cavity_audit`、`em_map`；无接地网 → 铺铜 skipped、平面/回流/谐振
   `unknown`、EM 仍跑。
3. `pour_plane`：铜层取 `F/In*/B` 全部（原来只有 F/B），在走线最少的一层铺——
   0067 因此落在内层 `2_gnd`。
4. `tools/audit_spec.py`：从板子推导 spec（接地网、轨、两条最长信号及 driver/load、
   `U/Q/D` 器件、发射/受害网），**所有电流/功耗写成 `null`**，每条带 `source:
   'auto: net-name heuristic'`；`probe_a=0.5` 作为声明的探针电流。
5. 缺输入降级：探针轨照解，但 J/压降 finding 是 `estimate` + `needed_information:
   ['the rail current']`；新增输入无关的 `PHYS.rail_resistance`（mΩ）与 `j_per_a`；
   缺功耗 → 不做热解，逐器件 `THERMAL.tj unknown`；全无功耗时热节 `skipped`。
6. 开路识别：`cg.solve` 按连通块筛 sink；**孤岛上的 sink 焊盘不再被"最近铜格"硬拉
   到主连通块**（那会凭空造出连接），改为 `PHYS.rail_open` 点名（`confirmed`）。
   0599 的 +3V3 有 8 个开路 sink、VIN 有 3 个；修复前解出 7.5e13 mΩ 的假电阻。
7. 判"原板"不判"改造板"：回流 finding 取 **as routed** 的缺口与地电阻，铺铜后的数
   字留在对照表里——否则等于用审计改造给原板判分。
8. `tools/phys_batch.py`：种子抽签、逐板 try/except（崩溃是一行，不是整批失败）、
   按 `mode+board_fp` 续跑、`summary.json/md` + 离线 `index.html`。
9. 页头修一个 f-string 里 `{{}}` 被当成 set 的崩溃；`NC` 不再当信号网。

## 三、结果（full，探针 0.5 A）

| 板 | P0/P1/P2/P3 | confirmed/estimate/unknown | 铺铜 | 平面覆盖 | 回流缺口 as-routed → 铺铜 | DRC 前→后 | 用时 |
|---|---|---|---|---|---|---|---|
| 0376 | 0/4/7/10 | 5/10/6 | B.Cu 2796 mm² | 82.3% | 17.7 → 1.2 mm | 0→0 | 23.6 s |
| 0599 | 0/9/8/9 | 7/14/5 | B.Cu | 85.2% | 9.4 → 1.1 mm | **27→28**（不可信） | 32.0 s |
| 0002 | 0/1/2/3 | 2/3/1 | B.Cu | 89.5% | 3.9 → 1.2 mm | 0→0 | 5.1 s |
| 0041 | 0/2/2/1 | 3/1/1 | B.Cu | 75.6% | 10.8 → 0.9 mm | 0→0 | 5.3 s |
| 0058 | 0/1/2/9 | 3/7/2 | B.Cu | 91.0% | 5.2 → 1.2 mm | 0→0 | 5.9 s |
| 0067（补） | 0/0/2/6 | 5/1/2 | **内层 2_gnd** 546 mm² | 97.7% | 2.4 → 0.15 mm | 0→0 | 9.2 s |
| 0400（补） | 0/2/12/6 | 3/10/7 | F.Cu 1608 mm² | 77.9% | 7.4 → 1.6 mm | 0→0 | 5.9 s |

每块板的输入无关量（真测量）：

| 板 | 轨（探针） | R (mΩ) | J 每安培 (A/mm²/A) | 腔模 1 (GHz) |
|---|---|---|---|---|
| 0376 | +5V/+3.3V/+9V | 42.5 / 58.5 / 10.1 | 50.1 / 53.9 / 35.0 | 0.444 |
| 0599 | +5V/+3V3/VIN//INTVCC | 56.6 / 36.5 / 47.4 / 3.7 | 189 / 52 / 46 / 57 | 0.655 |
| 0002 | VCC | 19.1 | 114 | 2.017 |
| 0041 | （无电压名轨） | — | — | 2.669 |
| 0058 | +15V / −15V | 8.7 / 5.1 | 21.8 / 19.7 | 0.994 |
| 0067 | （无轨） | — | — | 2.521 |
| 0400 | +3V3 / +5V | 54.1 / 20.3 | 95.2 / 30.7 | 0.405 |

## 四、泛化了什么，没泛化什么

**成立**：
- 5/5 随机板 + 2/2 定向板的 full 链都跑完（铺铜副本、DRC 前后、四类求解、页面），
  0 崩溃；每块都产出 `audit.json` + `audit.html` + 铺铜副本 + DRC 报告。
- 接地网别名：0400 的 `GNDPWR` 被识别，铺铜/回流/谐振全部按 GNDPWR 跑通
  （平面 77.9%、缺口 1.6 mm）。
- 四层：0067 的铺铜自动落在走线最少的内层 `2_gnd`（546 mm²，97.7%），DRC 0→0。
- 输入无关的测量跨板可比：电阻、每安培 J、平面覆盖率、缺口、模频。
- 缺输入时**不编数字**：热在 7 块上全部 `skipped`/`unknown`；探针 J 全部 `estimate`
  且带 `needed_information`；开路轨是 `unknown`（连通性），不是 7.5e13 mΩ。
- 0599 的铺铜引入 1 个 error，审计立刻把该板结果标为不可信（`drc.ok=False`），
  而不是继续引用它的平面/回流的"通过"。

**没泛化**：
- **自动 spec 读不出自动命名的功能**。0376 的 SPI 时钟叫 `Net-(U3-Pad13)`，自动 spec
  没有发射源，EM 只能走"最长网兜底（−10 dB）"；4/5 随机板都如此。语义网名的板
  （0599/0400/0058）能识别 `/CLK`、`/UART_RX`、`/SPI_MISO`、`±15V`。
- **热对比只在 0376 手写 spec 上成立**（1.4 W、θ、散热片都是声明值）。随机板上没有
  功耗，二维热解没有输入——这正是"不确定就说不确定"的边界。
- **无接地网的板**（119 块里 17 块）没被抽中，只由单测覆盖 `detect_ground → None →
  pour skipped → unknown`。
- **四层只有 1 块**，且内层是空的；没有真正带内层平面的多层层栈可测。
- 自动 spec 的 driver/load 是"焊盘多的当 driver"的启发式；0002 的单信号没有 GND
  焊盘在同一对位号上，地电阻为 `None`（如实）。

## 五、这次批跑抓到的缺陷（都已修 + 有测试）

1. 接地网硬编码 `'GND'`：28/119 块会被误报"无平面"。
2. 铺铜只看 F/B：唯一四层板的内层被忽略。
3. 开路网解出 7.5e13 mΩ：sink 在孤岛上被"最近铜格"硬拉到主连通块，压降被夸大
   （0599 +5V 的 0.57% 曾报成 6.12%）。
4. 探针标志把场值换成几何上界：`estimate` 只该影响状态/措辞，不该替换测量。
5. 续跑键不含 `mode`：fast 的结果会被当成 full 复用。
6. 回流 finding 判的是铺铜后的板（审计改造），不是原板。
7. 页面 f-string 的 `{{}}` 被解析成 set → unhashable；`NC` 被当成信号网。

## 六、诚实边界

- 电流是**探针 0.5 A**，不是这些板的电流；J/压降/孔流 finding 全部 `estimate` 且
  标注。R 与 J-per-A 与电流无关，但源焊盘取"最靠板边"，sink 取"其余全部焊盘"，
  这是启发式——所以 R 也标 `estimate`。
- 功耗/θ 全无 → 热节 `skipped`，Tj 不报。
- 受扰图是代理核；4/5 随机板的发射源是兜底（最长网），不是真实源。
- 所有"铺铜后"的数字属于审计改造；结论按 **as routed** 出，改造数只作对照。
- 0599 的 DRC 前就是 27 error + 28 开路（基准 D3-C 失败板），铺铜 +1 error 后
  其平面/回流结果不采信；这是审计的正确行为，不是链路缺陷。

## 七、复现

```bash
# 随机 5 块（种子、抽签记录写进 draw.json）
python -m fluxpcb.tools.phys_batch <out> --n 5 --seed 20261125 --mode full
# 定向补充
python -m fluxpcb.tools.phys_batch <out>/supplementary \
  --boards 0067_radio_saw_dcc6c 0400_laptimer58_Chickadee --mode full
```

产物（仓库外）：`z_lab/fp/audit_batch/`——每板一个目录（`audit.json`、`audit.html`、
`auto_spec.json`、`board_poured.kicad_pcb`、`drc_before/after.json`）、`draw.json`、
`summary.json/md`、`index.html`。
