# FluxPCB Knowhow 吸收映射表

> 来源：259个GitHub AI-PCB项目的797条设计knowhow（`pcb_analysis/AI_PCB项目七环节覆盖分析.xlsx`）。
> 本表列出已吸收进FluxPCB的每条knowhow及其来源项目——可审计、可追溯。

## K1 信号分类 + 分级布线（2026-10落地）

| 来源项目 | 吸收的knowhow | 落地位置 |
|---|---|---|
| ChengminLi/AI-PCB-layout | 先布线计划后分块布线 | `routing_classes.py` 分类→排序→分级布线 |
| jlceda | 网络类(net classes)工具 | `.kicad_pro`写入Power/Critical/Default类→DSN导出→Freerouting按类布线 |
| boardwise | 按回路/电源/MCU分类的审查规则包 | 分类器启发式(power/critical正则族) |
| jackwang988 + swayer/KICAD_ENGINE | 关键网络布线优先 | `routing_order_pairs`: critical→normal→power |
| Konnect | USB-C/LDO参考电路模板 | critical类含USB差分/SPI/I2C/CAN |

## K2 电源参考布局模板

| 来源项目 | 吸收的knowhow | 落地位置 |
|---|---|---|
| ChengminLi/AI-PCB-layout | 换流/栅极回路推导布局约束 | `power_layout.py` 环路面积预算 |
| FulongLi/AIPE-PCB-Agent | 实铜回路几何筛查 | `loop_area_mm2()`+`loop_ok()` 门禁检查 |
| Konnect | LDO参考电路模板 | AP2112K LDO C_in/C_out两侧贴装模板 |
| virajrungta/pcb-claude-plugin | 预验证电路积木 | SMPS/Buck拓扑簇偏移模板 |

## K3 布局退火精化

| 来源项目 | 吸收的knowhow | 落地位置 |
|---|---|---|
| elcruzo/learned-pcb-placement | SA最小化HPWL+重叠 | `placement/refine.py` 纯numpy退火(无PyTorch) |
| tbrandenburg/pcb-floorplanner | 贪心初置+SA违反罚函数 | 规则布局为初值→退火只改善(never-worse门) |
| Nsai-roshan/PCB-X-AI | Leiden聚类+SA | 功能分区即聚类初值 |
| yelabb/pcbfork | 确定性结构校验+几何评分 | hierplace解析验证为SA接受门 |

## K4 布线质量评分 + 结构化修复

| 来源项目 | 吸收的knowhow | 落地位置 |
|---|---|---|
| CyrilKafle/PCBInsight-AI | 28项确定性检查+0-100评分 | `routing_quality.score_board()` 评分卡 |
| takzen/pcb-inspector | 去耦/回路面积/回流路径启发式 | findings含环路/宽度/分类覆盖检查 |
| biosshot/easyeda-copilot | 检查点保护的布线事务 | route_report.json每轮留证(可回滚审计) |
| PDF评审建议 | 结构化修复替代盲跑 | `structured_feedback()`→engine有界重试 |

## K5 Freerouting分级 + SES闭环

| 来源项目 | 吸收的knowhow | 落地位置 |
|---|---|---|
| AvatarSD/KiSkill | DSN/SES闭环布线 | `ses2kicad.py` SES解析+注入(绕DSN保存NPE) |
| charlesmmorais/coppermind | SES导入 | SES wire/via→KiCad段/过孔文本注入 |
| Konnect | Freerouting集成+DRC审计 | K1 netclass进DSN→FR按类布线 |

## K6 版本化提示词

| 来源 | 吸收的knowhow | 落地位置 |
|---|---|---|
| Oct7love/AI_EDA_Copilot | 字段级用户给定/AI推断标注 | prompts/requirements.md schema+few-shot |
| PDF评审 | schema+few-shot+禁止项+诚实边界 | prompts/requirements.md |
| tbrandenburg | 约束生成而非坐标 | prompts/placement.md(布局约束YAML) |
| PDF评审 | 白名单修复动作 | prompts/route_repair.md |

## 更早吸收（历史轮次，见git log）

| 来源项目 | knowhow | 落地 |
|---|---|---|
| KiCadAI/nopekit | 门禁方法论/证伪校验 | 23门禁+反馈重试引擎 |
| NextBoard | 多阶段验证门控 | flux.yaml gate DAG |
| kicad-happy | 分销商查询 | jlc_catalog.py缓存降级 |
| Konnect | 250万零件目录理念 | partslib快照目录(13→26器件) |
| eLSeR17 | E12/E96标准值 | stdvalues.py |
| mash/jlcpcb-skills | jlcparts+Basic件优先 | bom_lcsc_verified门禁 |
| Seeed/kicad-mcp-server | devicetree模板固件 | firmware_gen.py |
| AkLOFAS/kicad-happy | SPICE子电路测试台 | spice.py divider/rc/led TB |
| dshills/KiCadAI | 角点评估 | MC 200次+3角点 |
| AvatarSD | openEMS S参数 | em_adapters.py用例生成 |
| himanshu40304 | 布线前后双域验证 | impedance_si+drc_zero分离门禁 |

## 未吸收（Phase D候选，按池子大小）

- RL/GNN布局训练(elcruzo/kunal-gh): 需训练数据集
- 拼板Guillotine装箱(yangchao0818)、CNC G-code(sthamann)
- 数字孪生第七环节(shruteshtarale, 全池仅12条)
- 多EDA后端(Altium 44项目次/EasyEDA)


## MCU抽象层（2026-10 H743轮次）

| 来源 | knowhow | 落地 |
|---|---|---|
| ST官方open_pin_data | 同源XML方法推广到第二颗MCU | `mcu_db.py`: H743 TFBGA240+优先级分配器(USB/SWD独占脚优先) |
| N6三向互证方法 | 官方XML为唯一真值 | BGA封装从XML球位直出(栅格/去空位数据推导) |


## plane_integrity 门禁（2026-10 内层平面真实化）

| 来源 | 教训/方法 | 落地 |
|---|---|---|
| 用户审查发现 | 6层板In1-In4空铜却24门禁全绿（物理门禁从stackup声明推算而非从铜验证） | `plane_integrity`门禁: 声明为ground/power_copper的层必须有zone铺铜 |
| pcb.py | ground_zones只写F/B与n_layers无关 | 6L扩展到In1/In4 + power_planes(In2=3V3,In3=5V) + stitch_vias缝合 |
| dcsolve docstring承诺zone但从未解析 | zone polygon bbox按net栅格化入铜掩膜 | DC解从此含内层平面 |

## K7 真连通性门禁 + ratsnest驱动布线（2026-10 连通性真相轮）

| 来源 | 教训/方法 | 落地 |
|---|---|---|
| 用户审查发现 | `unconnected_items=ignore`标定让kicad-cli DRC报告0未连接→快路径跳过布线→79开路的板"RELEASED" | `conn_audit.py`: pcbnew `GetUnconnectedCount`直读ratsnest, 无法被标定稀释 |
| gates路径bug | `routing_completeness`门禁指向`gates/kicadgen/`(不存在)永远FAIL | `.parent.parent`一行修复, 四板如实暴露45/27/82/79开路 |
| pcbnew SWIG包装器 | `GetConnectedItems`每次调用返回新包装器, id()/m_Uuid不稳定→朴素BFS死循环(117k次) | 位置键并查集: 焊盘位/走线端点/过孔位为节点, 岛=连通分量, Prim链=大鼠 nest MST拓扑(实测derived_pairs=79=ratsnest) |
| `GetRatsnestForNet`未包装 | 返回裸SwigPyObject, CN_EDGE无绑定 | 同上: 自算岛+MST替代 |
| KiCad10 API更名 | `PAD_ATTRIB_THROUGH_HOLE`→`PAD_ATTRIB_PTH`(旧名AttributeError被try吞掉→功率缝合孔全静默失败) | `getattr`双名兼容 |
| DRC报告解析对(已在本轮废除) | severity=ignore的条目不进报告→extract_pairs空输入→route_embedded永远无活干 | `ratsnest_state()`: conn_audit --pairs模式, auto_route以ratsnest为唯一未连接真值 |
| cleanup半径2.5mm过度清除 | 一个违例清除半径内全部同类网络走线/过孔→大鼠 nest越修越差(79→80) | 只清距违例最近的一个物品(0.65mm内) |
| collect_obstacles无层过滤 | F.Cu SMD焊盘阻挡B.Cu布线→所有隔层策略静默失败 | `pad.IsOnLayer(layer_id)`过滤 |
| 内层平面够不到 | F.Cu功率焊盘与In2 3V3平面只差一个过孔(3V3曾碎成25岛) | `power_via_pass()`: 仅对当前碎岛网络的SMD功率焊盘放缝合孔(中心/偏移候选), 全量放孔反而因反焊盘重灌切割平面(GND 0→2岛的教训) |
| BGA球内过孔 | 0.8mm节距相邻球0.8-0.2=0.6≥0.55: 球心过孔合法; 偏移过孔落邻球上必败 | `_via_candidates()`: 每端点独立选锚(BGA球心/LGA偏移/THT免过孔) |
| hole_to_hole违规风暴 | 盲目放孔101个→199条hole_to_hole | `_hole_map()`+`_hole_ok()`: 过孔需与所有钻孔保持0.25mm边距 |

## K8 深度2驱逐式MCU分配器（2026-10 h743-fc轮）

| 来源 | 教训/方法 | 落地 |
|---|---|---|
| PB6类强复用球 | UART5_TX只在PB6/PC12/PB13, 全被prio-1信号占走, 贪心饿死 | `_place()`: 深度2驱逐链(I2C1_SCL让出B5→D5被TIM4_CH3占→TIM4_CH3有别的球), banned集防回占 |
| SDMMC独球 | SDMMC1_CK只有PC12一球, TIM先跑必被抢 | SDMMC/FDCAN/I2C升prio 1 |

## K9 Pixhawk级全外设板（2026-10 h743-fc）

| 来源 | 方法 | 落地 |
|---|---|---|
| ArduPilot NxtPX4符号 | BMI088 16脚表(SCK=8/SDI=9/CSB1=14/SDO1=15) | sensor_pinmaps.json + 现代格式BMI088封装(BMI055家族LGA-16焊盘几何) |
| TDK同族引脚兼容 | ICM-45686与ICM-42688-P同14-LGA | pinmap继承+ICM-42688-P封装复用 |
| KiCad无3x08排针 | Pixhawk三排PWM(信号/GND/5V) | `gen_hdr3x08()`: 3x08 2.54生成封装(同BGA直出思路) |
| fc-full板族 | 8PWM/8UART/TF/2CAN/2ADC/USB-C/SWD/GND×5/BOOT | `fcfull.py`: 50×50 6L, 74网络, 17连接器, BMI088(SPI1双CS)+ICM-45686(SPI2) |

## K10 布线收敛战术清单（2026-10 h743-fc实测量化）

| 现象 | 根因 | 战术 | 实测效果 |
|---|---|---|---|
| 功率网络碎岛(3V3曾25岛) | F.Cu功率焊盘与内层平面只差一个过孔 | power_via_pass: 仅对当前碎岛网络放孔 | GND 112岛→0, 3V3 55→~8 |
| 盲目全量放孔 | 101孔反焊盘重灌切割平面 | 只放pairs.txt里碎岛网络的孔 | GND 0→2倒退消失 |
| 过孔穿B层走线 | power_via_pass只查F层障碍→GND孔穿过B层信号 | 双层全清查_via2_ok | 短路66→0 |
| hole_to_hole风暴 | 0.3mm间距同网过孔对 | _hole_map()+0.28边距+放置即登记 | 199条→个位数 |
| L策略全灭 | collect_obstacles无层过滤, F焊盘挡B层 | IsOnLayer过滤 | 隔层策略复活 |
| cleanup越修越差 | 2.5mm半径清除邻位好走线 | 只清最近物品(0.65mm) | 79→80倒退消失 |
| A*终点不落线 | 目标格距铜边缘≤0.3mm, 不接触=白布 | 双端clear()验证的stub落线 | — |
| 过孔合法半径 | 球心0.6≥0.55合法; LGA偏移孔落邻焊盘必败; via_r按障碍类型分档0.55/0.85 | 每端点独立选锚 | — |
| KiCad10 API更名 | PAD_ATTRIB_THROUGH_HOLE→PTH, AttributeError被try吞掉 | getattr双名 | 功率缝合孔复活 |
| 剩余51对 | 静态策略+网格A*的能力边界, FR headless超时 | 如实报告(routing_completeness硬门禁) | 259→68(74%) |

## K11 单进程布线会话 + Freerouting真修复（2026-10-23 又快又好轮）

### 性能结构：往返循环 → 单进程会话（route_session.py）
| 旧链(4子进程×N轮) | 问题 | 新会话 |
|---|---|---|
| kicad-cli DRC(refill) ~2min ×每轮 | 迭代间zone重灌→ratsnest 70↔73振荡 | ZONE_FILLER.Fill进程内0.7s, 确定性(实测5次重灌计数不变) |
| conn_audit子进程读进度 | 闭没闭环要等子进程往返 | Add(track)后BuildConnectivity+GetUnconnectedCount 0.03s, 每对即时验证(实测闭一条立刻68→67) |
| route_embedded每策略collect_obstacles全板扫 | 每9策略×每pair | Obstacles一次构建: 精确seg/rect表+0.16mm占用/via_block/hole三栅格, 提交后增量标记 |
| cleanup半径清除 | — | 只清最近物品 |

### Freerouting的真相（三层排查）
| 现象 | 结论 |
|---|---|
| 480s超时"永不成功" | 谎言: 给足1800s, 240球h743-fc 886s完成128网路由+优化 |
| SES导出"placement-only" | headless SES写坏: 完成任务后只存布局不存走线 |
| DSN回写0字节 | headless DSN写坏同上(即使job COMPLETED) |
| **.scr(Autodesk Fusion脚本)完整导出** | **唯一可用出口**: WIRE 'net' w (x y)折线 + VIA 'net' d round (x y), µm坐标y翻转, 层1=F/2..N=In/16=B → scr2kicad.py进程内注入(按网去重防FR两遍堆叠) |

### BGA逃逸物理律（0.8mm节距0.4球+0.15间距）
| 通道 | 几何 | 结论 |
|---|---|---|
| 球间直线 | 需0.85 > 节距0.8 | 任何线宽都过不去 |
| 球间对角 | 0.566-0.2-0.2-0.1=0.066 < 0.15 | 同样过不去 |
| **球心via-in-pad(0.5/0.25)** | 0.8-0.25-0.2=0.35≥0.15 | **唯一逃逸**: 0.6/0.3过孔需0.85中心距挤不进, 0.5/0.25只需0.75 |
| 建模教训 | 网格边距必须诚实(extent+clearance+线半宽) | 松边距让A*走幻影对角线, 提交时精确检查全灭(seg-clear-fail 20/轮) |

### A*工程细节
SWIG包装器id不稳定→位置键并查集; 0.2mm栅格在0.8球阵里只有单车道且对角拐角规则把唯一逃生路封死→0.16栅格(整除0.8, 双车道); 朴素A*在10万可达格上烧光20万节点预算→加权A*(W=2)探索减5-10倍; 角规则从"双正交皆空"放宽到"其一为空"+对角段精确校验兜底; 目标半径3mm会吸到源岛铜皮(A*绕回原地verify-fail)→1.2mm只取目标焊盘自身铜皮。

### 最终战绩(ratsnest, DRC=0三板全绿)
| 板 | 起始 | 本轮链(FR+scr注入+会话收尾) | 闭合 | 墙钟 |
|---|---|---|---|---|
| esp32s31-dev | 45 | **1** | 98% | ~8min |
| esp32s31-mini | 27 | **2** | 93% | ~12min |
| h743-fc(240球BGA) | 259 | **49** | 81% | ~50min |
残差=FR也布不出的硬尾巴(BGA球下FR自身B层走线挡via-in-pad; 模块焊盘排被FR自己的逃逸线封死), 如实由routing_completeness门禁报告。

## K12 dog-bone扇出工业化（2026-10-23 第二轮: 调研落地）

### 调研结论（Altium/AMD UG1099/MacroFab）
- ≥0.8mm节距BGA的标准逃逸=**dog-bone**: 四球对角中心放小过孔+短线, via-in-pad仅细节距用
- 0.8mm节距推荐**0.3mm NSMD球**(我们原0.4mm!) — 0.4球在对角中心(0.566)只剩0.116<0.15间距, 任何过孔都放不下
- 0.5mm球间距下球间可走1根3.5-4mil线(0.3球: 间隙0.5mm, 0.2线+2×0.15=0.5恰满)

### 落地(route_session.py + mcu_db.py)
| 机制 | 实现 | 实测 |
|---|---|---|
| 0.3mm球 | gen_footprint按UG1099 | dog-bone合法性从0到61个落位 |
| dogbone_fanout | 四球中心0.4/0.2过孔+0.15短线, 外圈优先, 中心唯一分配 | 单会话52-61个逃逸 |
| 功率球缝合让位 | BGA内功率球只准via-in-pad(0.6偏移恰好踩对角中心格点, 把信号球封死) | 封死球从"永久"变可解 |
| via可驱逐 | GetLength()==0过滤曾把所有过孔排除在rip-up外 | ripups 0→12/会话 |
| A*自有via桥接 | 存在同网过孔的格点免费换层, 不重复放置 | dog-bone被A*直接复用 |
| B.Cu清道(clear_bcup.py) | FR在球阵下方布B层线挡落点→注入后独立进程清除 | 进程隔离是必须(见下) |

### KiCad 10 SWIG陷阱(本轮最大时间黑洞)
board.Add/Remove之后, 同进程的GetTracks/GetFootprints/甚至重新LoadBoard都可能返回裸SwigPyObject(不可迭代/无属性)。无先例文档。**对策: 改板的步骤各自独立进程**——scr注入一个、清道一个、会话一个。

### 遗留(如实)
fc板本轮全链重跑因FR对预布线板的非确定性+14处clearance残差, 未超过已提交状态(49开路+0 DRC), 板面已回滚; 引擎能力(dog-bone链)已全部入库待下次干净重跑。BGA逃逸的完整解=0.3球+dog-bone+功率球让位+清道, 各环节已验证但需一次连贯的全新运行来兑现。

## K13 语料回流（2026-10-24 诊断/跳线/积木入口轮）

回顾371项目档案(pcb_analysis/)后的三个吸收——修正了"语料已榨干"的误判：

| 来源项目 | 吸收 | 落地 |
|---|---|---|
| hyuhang822-blip/kicad-sch-to-pcb | 残差网络量化诊断流程(真实缺口→连通岛→通道宽度剖面→双向泛洪定位瓶颈→何时停手交人) | route_diag.py: 每条开路输出机器可读瓶颈报告(相遇点/宽度/阻挡物清单), verdict=routable/tight/jumper。fc实测43对→25可布/18组件隔离 |
| deosgracius/spicestudio-ai | 0Ω跳线跨接(hop-over jumper): 不可布网络串0Ω拆两半各自短布=ratsnest=0确定性兜底 | route_jumper.py(板侧已实现: 0603跳线+NET#Jn拆分; 运行时exec诊断源码的桥接有静默失败, 标记实验性) |
| mo9652962-ai/circuit-agent | 一句话→积木式确定性映射(keyword→audited blocks→Pydantic契约, LLM只选积木不画线) | one_liner.py + `flux new "一句话"`: 三板族关键词+槽位(PWM/UART/CAN/ADC路数/TF/mcu), 未知→clarification_needed |

### 语料回顾的三个前提修正
1. 推挤布线全语料371项目**无人实现**(KiCad-Astra唯一提及者且标"future work")——本仓库的差距是全领域的差距
2. cursor-for-pcb与我们走到了同一路口(网格A*标准节距0 unconnected→细间距告警→路线图=escape-via fanout+稠密板换FR)——交叉验证dog-bone+FR方向
3. reference/全部来自外部官方源(ArduPilot/ST/HPMicro), 与259语料无对应; 语料价值在方法不在资产

### 同轮落地
- U8/U9 CAN移出BGA右逃逸带→底带(route_diag证据驱动, 修复零走线PWM/UART球根因); congestion.py逃逸环检查
- 棋盘格dog-bone(奇偶主中心): 每球保底一个中心格
- spec_schema门禁补constraints.family枚举(未知family曾静默落入legacy载板路径)
- fab导出进流水线(RELEASED→自动Gerber/钻孔/坐标); .claude skill布线描述更新到K11-K13
- fc新链: 140→82(扇出会话)→43(FR+收尾); 诊断: 16可布+18组件隔离

### route_jumper 机制修正（2026-10-24 第二轮）
初版两处设计错误已修复：(1)只在跳线焊盘上拆网络不改孤岛铜皮——ratsnest不闭合; 正确语义=**孤岛B全部铜皮重命名NET#Jn**,跳线桥接两网。(2)多轮再审计级联分裂(3开路板拆出21个跳线)——改单轮。落位=诊断器双向泛洪**相遇点**(两侧被证明可达),中点猜测落在实心区两半都不可布。FootprintLoad替代手工FOOTPRINT构造。
**实测(mini)**: 3开路→3跳线→6短半,正交会话收4/6——机制按设计工作。全闭合剩余卡点=封死锚点口袋(模块焊盘排): 跳线两半同样封死,需先开口(rip-up在锚点处)再跳线。此为下一步。

## K14 fluxrouter：PathFinder 协商拥塞布线器（2026-10-24 自研轮）

### 依据
调研确认：协商拥塞(McMurchie&Ebeling 1995, 1100+引用)=FPGA工业标准30年；语料259项目零实现；
RL路线(DreamerV3+FR 2026)未超越经典且Quilter自认需混合。**封死锚点的墙是走线(可协商)，PathFinder的历史代价天然推墙=它本身就是开口器**。

### 落地（routegrid.py + fluxrouter.py + auto_route接线）
| 组件 | 内容 |
|---|---|
| routegrid.py | 共享栅格核心，统一三份漂移实现(session/diag/embedded各一份, embedded还漂到0.2栅格)；K7-K13全部教训烘焙(诚实边距/按网own通行/dog-bone过孔半径/岛BFS验证) |
| fluxrouter.py | 代价场=(1+h)×(1+pfac×异网占用)×类权重(critical0.8/power1.3)；每轮全可协商网重布(随机序)；过使用格h+=递增、pfac自适应1.6x；违例格5×5邻域h+=5回炉(单格惩罚只会挪一格撞同一个焊盘)；逐格发射+精确校验+孤儿半段清除+pair_connected逐链验证 |
| 集成 | auto_route: FLUXNT_ROUTER=pathfinder 启用, FLUXNT_PF_ITERS 可调 |

### 实测（K14核心验证）
| 板 | 基线开路 | fluxrouter结果 |
|---|---|---|
| esp32s31-dev | 1 (3V3) | **3V3真实闭合**(5轮收敛, 4.4s)——残差仅剩GND zone-web填充噪声 |
| esp32s31-mini | 5(新鲜填充态) | 5V+EN两网闭合(10s) |
| h743-fc | 49 | 43网仅通1, **134分钟**——见两条诚实发现 |

### 两条诚实发现
1. **性能**：纯Python的cost_at逐邻居调用在fc规模(43网×长路径×25轮)太慢——P4(numba/向量化/代价场预计算)是必做项而非可选项
2. **架构**：提交态板上fluxrouter只能协商**自己布的线**——口袋墙是session/FR的既有走线(模型中的固定铜)，协商不动。正确用法=**全新板上作主布线器**(FR之前)，那时全部走线皆可协商。fc上作收尾器的收益有限，这是设计使然不是缺陷

### 开发中修的四个坑（供后来者）
1. 成功链items从未记账→统计恒零+每轮叠加重复走线
2. 目标格接受"焊盘附近的空格"→路径终点悬空不触铜(K11同款教训)
3. 违例时不清除已落板的半截items→孤儿走线污染(每轮累积, GND碎成5岛的元凶)
4. 单格h惩罚→A*挪一格撞进同一个焊盘；5×5邻域才有效

### K14 补记（V1-V3 轮）：FAST内环 + 主布线器定位实测 + 三个新发现
1. **FAST数组内环**：代价场/占用/own集全列表化(A*热环免函数调用)——fc上134分钟→8分钟(**17x**)
2. **stub修复**：cells_to_items漏了首尾stub(焊盘中心↔首末格)——全新板上全部verify-fail的根因；补上后mini全新板20网通14
3. **顺序定律(实测确认)**：任何先布的走线对fluxrouter都是固定铜。mini全新板：session先布→fluxrouter 0/18；fluxrouter先布→14/20。**fluxrouter必须在链中最前(扇出之后、一切其他布线之前)**
4. **交替收敛**：fluxrouter↔session交替,mini 36→18→17→15→12→8(四轮后平台)。fc全新板:扇出82→fluxrouter 28/60网(8分钟)→session 57。跳线在60开路板上全量误用→93(教训:只跳route_diag判定的不可布网络)
5. **与FR的诚实对比**: mini全新板fluxrouter-first 8开路/2分钟 vs FR-first 2开路/12分钟——FR仍是更高完成度的主布线器; fluxrouter的独特价值=FR留残后的收尾层(dev 3V3首证)+快10x的初布

### D轮实测（dev冲零）：到达 ratsnest=0 但揭示物理极限
dev 3V3 走廊到达制造极限：RX0 与 3V3 通道间距 0.123mm（需 0.15）——
任何算法在此布局上要么违例要么开路。
- **ratsnest=0 曾达成**（fluxrouter+session, 3条clearance违例）
- **DRC=0 曾达成**（cleanup后, 1条开路回弹）
- 两者不可兼得=布局级约束（RX0焊盘到3V3路径的物理距离）
- 跳线也未能突破（两半同样在该走廊受阻）

**结论**：这是推挤布线(no push-and-shove in corpus)缺失的实证——0.123mm的间距
差需要推挤RX0挪位0.03mm，rip-up做不到（撕了RX0会开路自己的网）。
布局级修复（RX0或3V3源焊盘微移0.1mm）可解，属fcfull/devboard布局参数调整。

## K15 fluxrouter V3：部分撕除+自适应步长+信号层叠层（2026-10-25 研究驱动改进轮）

### 调研→问题→改进映射
| 问题（实测） | 研究 | 落地 |
|---|---|---|
| 全量撕除毁好路线(pass1 28/60→pass2 0/32) | Zha&Li部分撕除 | conflicted标记+只撕冲突网 |
| 0.16mm格步长无法命中0.35mm间距 | MegaRoute自适应步长 | 开阔区(cost<1.5)加2格跳步 |
| In1-In4纯平面→BGA逃逸只有2信号层 | 行业6L标准Sig/GND/Sig/Sig/PWR/Sig | In1/In4改signal层型(GND铺铜保留) |

### fc实测（V3全链：扇出→V3 fluxrouter→FR→session）
| 指标 | 提交态 | V1链 | **V3链** |
|---|---|---|---|
| ratsnest开路 | 49 | 43 | **38**(历史最佳) |
| DRC | 0 | 有 | 有(FR与V3走线冲突) |
| 信号层 | 2 | 2 | **4** |

38开路是fc历史最低但DRC不干净(FR scr注入与V3走线物理冲突)。根因：FR不知道V3已布了哪些走线(scr注入是全量替换)。需一体化(FR在V3后跑时只布剩余网络)或cleanup回摆。

### 关键发现
1. **own=w/2修复的代价**: 使精确检查更严格→fc上V3单独仅通12/60(vs V1的28/60)。开发板(稀疏)没问题，密集BGA上多数路径过不了修正后的间距检查。这是物理精度vs路径可用性的真实权衡。
2. **信号层叠层有效但不足**: 4信号层确实降低了开路数(49→38)但BGA口袋问题依然存在——叠层加倍了逃逸通道但不够全部逃逸。
3. **FR+协商的冲突**: FR注入(.scr)替换全部走线，不知道协商布线器的状态。需要FR感知已布走线(FR的DSN导出可以包含它们)或改变链序。

## K16 Z轮：FR增量编排+VPR调度+构造性栅格+4层联合A*（2026-10-25 研究驱动轮）

### 调研→问题→改进映射（三路并行文献调研）
| 问题（实测） | 研究 | 落地 |
|---|---|---|
| FR 900s全板重布+scr全量替换144短路 | DSN生态pre-routed机制（无正式文献，工程空白） | Z1: dsn_mark_fixed把已收敛网走线标(type fix)——**mini实验证实FR尊重fix: 全量fix后73s只布开路网** |
| pfac×1.6/×1.15震荡+网序敏感 | VTR默认参数+Zha&Li FPGA'22(顺序波动最多96%) | Z2: iter1 pfac=0→0.5→×1.3封顶；每轮shuffle(已有) |
| knowhow_constraints.json零引用+3处漂移 | — | Z2: route_constraints.py加载器，routegrid/fluxrouter/routing.py全部接线，test_k16防漂移 |
| 栅格承诺vs精确检查否决(fc physical主导) | ICG/LineExplore构造性占用 | Z3: track栅格margin w+0.15→w+0.30(构造性界:两0.2走线需0.37中心距，最坏对角中点情形需~w+0.29) |
| A*只有2层，In1/In4闲置 | TPR'05/多die VTR FPL'26/RWRoute'21联合式3D；FCCM'25证明资源不足时调参无用 | Z4: NL层A*(via为一等协商节点，through-via任意层跳成本一致)，圈深→层偏好+内层软方向偏置(UG583) |
| route_report全空、失败原因不可审计 | PathFinder history场即拥塞图 | Z5: why_history逐轮落盘+congestion.svg热图(失败网锚点叠加) |

### 关键实验证据
1. **FR尊重(type fix)的机制验证**(mini全量fix): FR 73s完成(全量重布~12min)，scr只含开路网(IO11/IO5)+1 GND过孔，fixed铜原样保留。
2. **fc增量FR需更长超时**: fix标记后FR只布43个开路网，但开路网恰是BGA逃逸难网，绕locked铜更难——1500s仍超时。链内自适应：fix激活时默认2400s。
3. **DSN根因确认**: pcbnew ExportSpecctraDSN**会**导出已有走线(fc 3991条wire)；dev的DSN空wiring是旧文件。FR"互毁"真相=FR把(type route)走线当可撕优化对象。
4. **zone语义修正(写代码时发现的调研盲点)**: KiCad zone可重灌、会为新走线退让——GND铺铜**不是**布线障碍(标blocked会毁掉所有F/B布线)。只有keepout规则区是硬障碍(rasterize之)。内层信号线穿GND铺铜是标准做法，filler自动carve clearance。
5. **mini 0/4非回退**: 提交态mini板上新旧路由器失败签名完全一致(nopath=1,physical=3,同4网)——fluxrouter在该板状态本来如此，历史"闭5V+EN"是另一板态。
6. **Z3构造性边距被实测否决(重要反转)**: track margin提到w+0.30后fc nopath 42/43、routed=0(全灭)。根因是几何: 0.8mm节距BGA逃逸通道两条平行dogbone走线中点距各0.4mm，而两根0.2走线的DRC需要0.37中心距+对角中点余量≈0.39——**通道里一个自由格都不剩**。nopath致命(不可重试)、physical可重试(veto+5×5 h惩罚)。结论: 0.16mm栅格上构造性保证与BGA逃逸密度不可兼得，margin回退0.15，veto-retry是残余机制。教训=理论最坏情形界在真实密度面前必须让步于可重试性。
7. **提交态fc板上fluxrouter=0/43是既有行为**: 旧版(HEAD)对照同样0/43、签名逐项一致。V3的12/60、Y2的17/60全是**全新板**数据。提交态板FR+session铜遍布→nopath。推论: fluxrouter必须跑在扇出后、FR前的干净阶段，链序是决定性的——auto_route新增FLUXNT_V3=1开关(session扇出→fluxrouter→FR增量→session收尾)。
8. **4层fluxrouter单轮~3min**(fc 43网，2层V1为32s/轮): 层转移邻居×3+occ翻倍。停滞早停(4轮窗口+it≥6)防20轮空烧；FLUXNT_PF_ITERS=12为fc实测档。

### 链内集成bug清单（Z1接入时挖出，mini回归3→19开路事件的完整解剖）
1. **scr2kicad注入NameError**(Y1遗留): preserve分支用Path未导入→注入rc=1→旧代码final return无error字段→"freerouting跳过: "空错误难定位。修复: main块内from pathlib import Path; 注入失败显式返回error。
2. **FR java输出GBK解码崩溃**: subprocess text=True默认locale——中文Windows下reader线程UnicodeDecodeError吞掉全部stdout。修复: 全部subprocess加encoding="utf-8", errors="replace"(7处)。
3. **kipy进程退出时堆崩溃吞stdout**: DSN已写盘但DSN_OK丢失→管线误判导出失败。修复: 采信磁盘产物(存在且>1KB即继续)。
4. **clear_bcup无preserve感知(真凶)**: 无差别删除BGA框内B.Cu走线——全量重布流程里FR的路线是牺牲品无所谓，Z1增量流程里删的是已收敛网承重铜(mini: 3→19开路)。修复: argv[2]传preserve_nets.json，preserve网跳过。隔离实验: 仅注入ratsnest 3→3(无辜)，注入+清道3→19。
5. **cleanup堆崩溃(rc=3221226356)半程存盘**: 删30项后崩溃，损坏板进入下一迭代(19→22)。修复: cleanup前备份+倒退回滚(实测生效: 20→21已回滚)。
6. **scr统计行被SWIG泄漏警告污染**: json.loads选行解析踩到泄漏警告→stats变raw。低危(仅观测面)。
7. **scr2kicad层映射错误(fc 96短路真凶)**: FR写Eagle式铜层号(1=Top,16=Bottom,17+k=第k内层——fc scr实测LAYER 1;16;17;18;19)；旧代码In{n-1}把17解析成不存在的In16.Cu→异常→内层WIRE全部滞留在前一层上下文→注入后96铜-铜短路。修复: 17→In{n-16}.Cu，未知层跳过该层wire(via是通孔不受影响)而非猜层。

### Z4实现要点(联合式3D)
- GridModel(board, layer_ids): N层occ/via_block/segs/rects；via_block所有层OR+hole=viaany(through-via语义)
- signal_layer_ids(): constraints stackup信号层∩板实际铜层数(2层板[F,B], 6层[F,In1,In4,B])
- A*节点(li,gx,gy)；层跳=through-via一次(成本VIA_COST不随层距变化——一个钻就是一个钻)
- 层偏好: critical→F(0.85), 长信号→末两层(0.85), In1横/In4纵软方向偏置(×0.85/×1.15)

### 下一梯队(未做)
- compaction兜底pass(一维约束图挤开, 300-500行)
- ICG/line-exploration无网格引擎(BGA区候选点+构造期验证)
- 离线RL学cost权重(arXiv 2512.03594)、FanoutNet RL扇出
- KiCad PNS C++绑定(PCBWorld式)

## K17 W轮：LLM驾驶员+规则发动机（2026-10-25 用户定向轮）

### 方向依据（用户判断+会话证据）
纯规则路线5轮停在49开路；本轮每个突破都来自"LLM读日志→推理→定位"（8个链内bug无一被规则发现）。
参考工程 esp32s3_deskpet_v2（ERC0/DRC0/0未连接可加工板）验证了正交模式：
脚本扇出(仅电源网,via-in-pad大焊盘+极坐标逃逸)→Freerouting一次→脚本化微调(reroute_net/manual_links/stitch)。

### 架构
```
规则引擎执行 → 仪表盘(ratsnest+route_diag verdicts+why_history) → Driver.decide()
→ 执行器 → 循环（FLUXNT_LLM_DRIVER=1）
```
- routing_llm.Driver 三模式: 真LLM(FLUXNT_LLM_*含BASEURL支持GLM兼容端点) / 策略文件(FLUXNT_LLM_POLICY离线决策队列) / 确定性回退
- 决策菜单(全部参数校验,幻觉最坏=回退): reroute_nets(外科手术,参考工程reroute_net.py模式) / rerun_session / rerun_fluxrouter / rerun_freerouting / jumper(带网络过滤) / move_footprint(±10mm) / swap_pins(FLUXNT_ALLOW_PIN_SWAP门控,原理图会漂移) / finish / escalate
- 新执行器: rip_nets.py / fp_move.py(挪器件+撕其网络) / pin_swap.py(换焊盘网络) / route_jumper加ONLY_NETS过滤
- 审计: driver_decisions.json + route_report含steps

### 参考工程关键模式（已采纳reroute_nets；其余记档待用）
1. **reroute_net.py外科手术**: 撕目标网+其余(type protect)+敏感线临时膨胀1.5mm当障碍(USB/SPI)→FR只布目标→恢复线宽。我们的Z1增量FR+rip_nets已等价(膨胀技巧未移植)。
2. **zone后加**: finalize.py在布线后才加GND zone并fill——DSN里没有zone干扰FR。我们zone从生成就在。
3. **fanout只做电源**: 信号逃逸全交FR(4层无BGA可行;fc的240球BGA仍需dogbone)。
4. **SES可用**(KiCad9+FR1.9);我们FR2.4.1 SES坏→scr路线不变。

### 实测（mini副本, fallback模式, 4步预算）
决策1: reroute_nets([EN,GND,IO6])(残差4网自动选中)→rip+增量FR；决策2重复(delta=0)；
决策3: escalate(连续无进展检测)。终态4开路0短路,DRC门禁✅。审计文件齐全。
mini硬核残差(EN/GND/IO6)fallback模式收不动=预期——真LLM模式下一步会读route_diag证据转jumper/move_footprint。

### 用法
- 离线(默认): FLUXNT_LLM_DRIVER=1 → 外科手术优先的确定性策略
- 策略文件: FLUXNT_LLM_DRIVER=1 FLUXNT_LLM_POLICY=policy.json → 按序消费决策(LLM/human离线准备)
- 在线: FLUXNT_LLM_PROVIDER=openai FLUXNT_LLM_MODEL=glm-4.7 FLUXNT_LLM_BASEURL=https://open.bigmodel.cn/api/paas/v4 FLUXNT_LLM_API_KEY_ENV=GLM_API_KEY

## K18 X轮：网络优先级驱动布线顺序（2026-10-25 PCB专家knowhow）

### 知识
"给每个线设置优先级,然后启动自动布线"——布线顺序决定资源分配:关键网先布=先占好通道,
电源网最后(它们路径灵活)。这是商用布线器(Specctra/Altium)的标准实践。

### 落地与实测
| 引擎 | 现状 | 改动 | 实测 |
|---|---|---|---|
| route_session | 已有类排序(L913: CLS_ORDER+距离) | 无需 | — |
| fluxrouter | 纯shuffle(Z2) | **类桶排序**: critical→normal→power,桶内保持shuffle(兼顾Zha&Li抗序敏感) | 快测覆盖 |
| Freerouting | DSN网络序=KiCad导出序 | dsn_reorder_nets前置critical网 | **A/B实测:FR 2.4.1忽略DSN网络序**(全fix板上唯一变量=顺序,scr逐字节一致)——FR内部自排序;函数保留(零成本,若未来FR版本尊重顺序即生效) |

### 教训
专家knowhow必须实测落点:同一知识在三个引擎里两个有效一个无效。fluxrouter的循环序就是它的
布线序(先到先得协商起点),类桶排序直接改变资源分配;FR则完全内部化排序,DSN序是死码。

### X2: 电源层DSN保留（fc 132短路根因闭环）
fc DSN里In2/In3导出为(type signal)→FR把165条信号线布上电源平面→注入后与铺铜短路132处。
双修: pcb.py LAYERS_6源头(In2/In3 type=power,用户名改power_copper_1/2消重名) +
dsn_mark_power_layers手术(存量板,type signal→power,按constraints power_layers索引)。
plane_integrity门禁正则放宽接受后缀名。75测试绿。

### X2b: FR scr层号=按层名映射，自定义层名撞号（132/199短路最终根因）
读FR源码(SessionToFusion.getFusionLayerString)确认: v2.4.x的scr层号按**层名**算——
纯数字名原样返回(两版第一优先规则)；Top/F.Cu→1；Bottom/B.Cu→16；in/route开头→提取数字；
兜底index+1；17/18/19=文档层(Pads/Vias/Unrouted)固定写死。
我们的自定义层名全撞: inner_signal_1→"1"(撞top_copper兜底的1)、power_copper_2兜底"4"
撞inner_signal_4规则4的"4"、FR自身bug把13根线画上文档层19。
**根治(X2b): dsn_rename_layers_numeric把DSN层名改成KiCad层id纯数字**(首层→0,末层→31,
内层k→k)，FR原样回传，scr层号=KiCad id，scr2kicad直查映射(0=F,1-30=In{n},31=B)。
mini实测: scr输出LAYER 0/CHANGE LAYER 31✓。附带: cleanup回滚改双向判据(开路或短路恶化均回滚)。

## K19 R轮：行业配方落地（2026-10-26 四路深调研驱动轮）

### 调研裁决（中文社区25帖/工业实测/学术全景/管线先例）
- "复杂BGA全自动布到可生产"无任何先例：立创官方"自动布线并不够好！建议手动布线"；Altium"手工布不通的区域自动也布不通"；Quilter撤回1200球案例；DeepPCB被EEVblog实测吊打
- 工业共识配方：精心布局→完备约束→关键网手工锁定(20-30%)→fanout专项→bulk auto(60-80%)→人工收尾
- 网格协商象限被压制(FR 93-94% vs 无网格98-99%)；布局margin优化仅改布局消80% DRV(UCSD)；学术空位=无网格+协商+布局反馈三合一

### R1 差分对识别（fc实证：CAN1_H/L曾被分normal——正则CAN_[HT]不匹配CAN1_H）
detect_pairs后缀配对(H/L,P/N,DP/DM,D+/D-,+/-)；配对成员升级critical；CAN\d*_[HL]+_CK$正则修复；
fc spec.yaml补usb90Ω/can120Ω差分声明(喂impedance_si门禁+ipc2141)。实测：CAN×2/USB×2差分对全识别。

### R2 DSN类规则注入（258违例根因：KiCad导出(class kicad_default)全网页无规则→FR用内置0.1397mm默认间距）
dsn_inject_class_rules：Critical/Power/PAIR_x类(width/clearance)+class_class对内耦合间隙0.25(物理杜绝CAN叠线)；
注入网从kicad_default剥离(每网恰一类)；类块插入network闭合内(首版插到段外已修)；normal网留default。

### R3 关键网预布（配方核心）
route_session argv[4]白名单模式：跳过fanout/stitch、audit_pairs过滤、rip-up重扫与末段重扫均只碰白名单网；
V3链= fanout-session → preroute-critical(critical+差分对) → fluxrouter → FR增量(预布网经Z1 fix保护) → session。
driver菜单加preroute_critical。

### R4 残差收尾清单
终局route_diag→verdict=jumper/disconnected自动放0Ω(jumper已带ONLY_NETS)→剩余开路进handoff清单
(net/verdict/bottleneck/blockers/建议动作)→route_report含automation%/jumpers_placed/handoff。
交付物重定义：自动化率+收尾清单，不再是二元的布通/失败。

### R5 布局margin闭环
driver新动作adjust_placement(批量fp_move)；fallback规则：同一器件在route_diag blockers出现≥2次
→escalate前提议挪1mm(UCSD margin-max的80% DRV红利)。

### 顺带修复
routing.py driver/else结构bug：v3_order曾只在else内赋值而if v3_order在函数级(driver模式必NameError)。

### N轨（并行）：spatial_hash(均匀桶)+route_explore(顶点线探索A*+协商层保留)——93%→98%象限差距的根治

## K20 M轮：Commit门禁+链序修正+短路修复器+无网格性能包（2026-10-26 三路调研驱动）

### 诊断闭环（iter1 59短路的完整谱系）
- 85%是0.16/0.2263mm微短桩(fanout/布线器格子stub互撞)，集中MCU逃逸区x∈[20,45]y∈[20,45]
- 与旧轮(199短路=电源对信号)不同源——旧病已被stitch治愈；新病=引擎自产
- preroute零收益根因：跑在fanout后=BGA场被55处stitch+dogbone封死→critical全nopath→
  裸奔进bulk→CAN两成员独立布线互叠+PWM拥挤短路。M2修正：preroute提前到fanout前。

### M1 Commit门禁（调研第一公理：商业EDA从不修cross-net short，构造期预防）
commit_gate.py：世界坐标增量验证器——SpatialHash按层持固定铜+已提交铜，每网提交时
全部items对附近异网铜精确间距判定，失败整批拒绝（原子性，抄Freerouting的
never-half-a-trace）。接入三提交点(fluxrouter.cells_to_items/route_session.try_route_pair/
route_explore.emit)。mini session实测：gate-fail=4次拦截（格子模型放行但世界几何冲突）。
权衡：协商的"允许暂时重叠"被门禁收紧为fail-fast重试——数据证明重叠本就没被协商解决
(K16的conflicted-forfeit已是补丁)，门禁只是把发现提前到提交点。
FLUXNT_GATE=0可关（A/B用）。

### M2 链序：preroute-critical → fanout-session → fluxrouter → FR → session
fanout的Obstacles把预布铜当固定障碍（现有机制），dogbone的via_exact_ok自动避让。

### M3 short_repair.py（修复侧兜底，kicad-tools分型模式）
解析drc.rpt短路对→参与度计数→删参与度最高的短桩项→nets_for_reroute进外科手术重布
(rip_nets+增量FR)。v1不做via重定位（会拉断连接的走线端点）。接入auto_route收尾
（cleanup前）。

### M4 route_explore性能包（调研组合）
桶化邻居(2mm格3×3扫描替代全节点遍历)+懒验证(LazySP:只验证能改善gscore的边)+
节点上限600(按离src-dst线距离)+最近邻18+加权A*(h×1.4)+自适应包围盒(先bbox+3mm,
失败全图)+每网60s时限。mini: 143s→37-57s；产量13脏→8干净(门禁口径)。

### M轮实测
mini全链(M0-M3): **drc_zero门禁首次通过**(0短路)，2开路=基线，automation 81.8%，
handoff=[IO6,EN]——R4产物链完整落地。

### M轮 fc 终值（全链3迭代，M0-M4全开）
49(基线)→42→41→**40开路**，短路全程=0（M1门禁在链全生命周期稳定），score 65（历史最高，
此前25-28），automation 45.9%。preroute链序修正后真正布线(iter1: 140→138)。
无网格基准：提交态fc板10/68干净布通 vs 网格引擎0/43——象限假设fc尺度成立；74min暴露
每网预算×迭代放大缺陷→M4b板级预算(FLUXNT_EXPLORE_BUDGET默认900s)已修。
残余前沿：69处clearance违例（下一轮砖松弛pass的目标，Rizzo DAC'07配方已在调研档）。
板已回退提交态（49/0 DRC干净基线；新态40开但69违例不达门禁）。

## K21 内核轮：Board State Kernel（2026-10-26 六通用问题驱动）

泛化为: fluxpcb/kernel/（state/txn/invariants/causal/truthnet/lint 六模块）+ fabrication_netlist 门禁 + test_kernel.py 不变量组——本轮全部产物即泛化本体。

### 核心认知（K7-K20 的根因合并）
板曾是无类型/无不变量/无事务/无统一语义/无逆向边的共享可变文件。六类失败各映射内核一个侧面：
测量可骗(state)/引擎产废(txn+invariants)/互毁无原子性(txn)/上游无反馈(causal)/教训不固化(lint)/fab真值未闭环(truthnet)。

### 内核首战的三个真实战果
1. **fab网表审计抓住 RELEASED dev 板的两个真生成器bug**（此前所有门禁都漏过，因各自为政）：
   a) J1/J2 原理图HDR14符号把备用脚塞GND(14-len)，板侧1x08封装只有8脚→J1.9-14只在原理图存在；
   b) J4 USB-C 原理图S1/A12/B12 vs 板SH/无此焊盘。修复：符号引脚按header_pins截断+nc_pins；J4两侧统一usb_nets映射。
2. **分类学实测iter3残差**：66开路=35 escape-crossing(pin分配交叉冲突,重分配可解)+2 power-fragment+1 local+5未分类——容量数学证明通道够用，验证了"causal边指向reassign_pins"。
3. **severity政策分裂**：unconnected_items等4项功能性检查从ignore校准摘出（run_drc曾结构性恒零——连通性幻觉的倒影），测试保护防回填。

### 内核纪律（K21起生效）
- 教训泛化为不变量：KNOWHOW每节须有"泛化为:"或"个案"（lint.py强制）
- 引擎=事务：v3链的preroute/fanout/fluxrouter(light)与FR(full)已包Txn——快照/不变量/原子回滚/manifest
- 测量必带指纹：state.KernelQuery结构性保证
- 因果分类学：causal.classify 是残差处置的决策入口

个案：devboard J1/J4修复本身是个案，但"板与原理图引脚必须镜像"已泛化为fabrication_netlist门禁。

### K21 补记（内核链验证与诚实边界）
- mini内核链回归通过：2开路=基线，12事务零误回滚，chain_manifest完整可审计；门禁glob排除.ckpt快照（快照曾被当独立板计数）
- dev提交态fab审计=fail（1网引脚错：J1.9-14虚GND脚）——**这是内核对RELEASED板的正确判定**（该分歧真实存在）；
  生成器已修但重生成未复现RELEASED布线质量（全新板25开路），board已回退提交态，修复将在下次全链重生成时生效
- severity政策修正：track/via_dangling+starved_thermal暂留ignore（FR注入固有断头桩会以~900违例淹没drc_zero，
  待桩清理pass后解禁——_DEFERRED_FUNCTIONAL清单跟踪）；unconnected_items永不回填（真连通性检查）
- devboard J1/J2备用脚公式bug（len(row)<n_pins时覆盖信号脚）已修：备用GND只落在(len(row), n_pins]区间

## K22 C轮：闭环信息传递机制 + 信息地基硬契约（2026-10-27 12缺陷点全修轮）

泛化为: kernel/{intel,decision,constraints}.py（闭环三通道）+ kernel/state 硬契约(UnboundMeasurement/require_fp/SEMANTICS/count_drc) + _closed_loop（终止=0开路0短路）+ test_kernel 契约锁组。
契约: 读=kernel.state.query（唯一测量入口，指纹强制）；写=Typed Decision→Txn（校验拒绝带fix-it回流、回滚带不变量明细回流）；语义前提=SEMANTICS 注册表（语义变更必须升版本）+ SEVERITIES_POLICY_VERSION。

### 为什么"笑掉大牙"的错误之前从没被发现（诚实复盘）
K7-K20 是救火式开发：错误是**作为"修复"被引入的**——severity 校准是为压 DRC 噪声（校准掉 79 开路的那次），裸数字是求快省事，warning 级不变量是为兼容旧产物。引入时没有语义契约、没有不变量测试，所以它们从未作为 bug 显形，只作为"能跑"存在。KNOWHOW 记录了事故，代码沉淀的是事故补丁——开发流程本身（按板救火、每轮最小修复、无设计闸门）就是缺陷载体。流程教训：任何补丁必须同时声明"契约:"与"泛化为:"，语义级测试与补丁同提交（lint.py 强制，K22 起生效）。

### 12 缺陷点→修复映射（C 轮）
- 点1 行业配方定位 → 目标=全自动零开路+迭代（闭环终止条件唯一）
- 点2 DAG 无回边 → C3 _closed_loop（失败态→上游再生是合法边：reassign 再生成/挪器件/跳线/换脚均为回路一等动作）
- 点3 绑定无下游约束+双向缺失 → C2 决策双向协议（校验拒绝带 fix-it 回流）+ C4 约束上行（escape_preferences→FC_PIN_OVERRIDE→mcu_ball_nets 契约，板+原理图同源）
- 点4 失败不带修复动作 → C1 flexibility 柔性矩阵（每网候选动作+预期效果）+ C2 fix-it
- 点6 下游 bug←上游因 → C1 归因（escape-crossing 的因在 pin 分配）+ C4 上游代价项
- 点7/8 大拼盘 → C1 SituationReport 唯一情报载体（LLM 汇总，规则回退）
- 点9/11 配置静默改语义 → SEMANTICS 注册表+SEVERITIES_POLICY_VERSION+测试快照锁
- 点10 测量不绑定状态 → KernelQuery 唯一类型+require_fp 硬异常
- 点12 warning 级不变量 → UnboundMeasurement 抛异常（不变量没有宽容模式）

### C1/C2/C3 机制要点
- intel.build: 多维（测量+分类+几何+历史+约束）→一份报告；synthesize: LLM 在线=统一评估（状态+根因排序+动作候选），离线=规则回退（分类学×动作映射，同输出形状）
- decision: ACTION_CONTRACT 类型检查，拒绝=fixit（违反哪条/差多少/替代候选）回流 journal；Txn 结果（含回滚原因）回流 journal——下一轮情报读 journal
- _closed_loop: measure→intel→decide→validate→Txn→record→measure；候选动作队列（评估序+每网柔性候选）防重复（tried 集）；预算耗尽出归因（不伪装成功）

## K23 V轮：四流派×组合对照实验（2026-10-27 语料驱动轮）

泛化为: kernel/rules.py（规则单源+加载探针）+ geomir.via_ok（via立体验证）+ 严格变好门禁（_closed_loop strict gate）+ direct_link落板重验 + widen_net/corridor_rescue 动作 + test_geomir 四流派测试组。
契约: 读=kernel.rules.load_rules（规则唯一来源，带provenance）+ geomir计划（带状态指纹）；写=direct_link落板前必须按当前板重验（plan→execute失绑是短路来源）；语义前提=via_r=实际放置尺寸(0.25)非设计下限。

### 实验矩阵（mini顽固残差，统一协议1200s预算）
| 组 | opens | shorts | clr | automation | 机制判定 |
|---|---|---|---|---|---|
| T0 基线 | 1 | 10 | — | — | 参照 |
| T1 via立体验证 | 2 | 2 | 5 | 86.4% | 短路10→2(8/10是via管径类) |
| T2 规则单源单用 | 9 | 3 | 11 | 36.4% | **恶化**：via_r被规则下限0.4/2=0.2弄松+无门禁时电源网抖动(5→7→10开) |
| T3 corridor_rescue | **1** | 4 | 7 | **95.5%** | **最佳闭合**但失效计划产短路 |
| T4 严格门禁 | 2 | **0** | **0** | 81.8% | 干净，3负动作全拦 |
| T1234 全组合 | 2 | 0 | 0 | 81.8% | =T4：门禁拦下失效direct_link(+5短) |

### 机制级发现（实验的真金）
1. **plan→execute状态失绑**：direct_link的计划在旧板态验证、画时板已变→+5短（T2/T3）；严格门禁正当拦截（T1234）。修复=落板前按当前板重验（C0契约对计划同样适用）——T1b。
2. **规则值语义错位**：via_ok误把设计下限(0.4)当实际放置尺寸(0.5)——验证器变松。修复=via_r固定为实际annulus 0.25。这是"验证器拼凑病"的第4处，进一步证明规则单源必须配语义核对。
3. **负动作毒性**：无门禁时电源网抖动使开路单调恶化(5→7→10)；严格门禁(ByTaymur:新DRC错误或递增开路即阻断)三次精准拦截。
4. **闭合与干净的互补**：T3闭合力(1开)+T4防劣化(0短)正交——组合预期0/0。

### 选型
平局(T4=T1234=2/0/0)取简→胜出组合=T1+T4；但机制发现指向 **T1+T1b+T4+T3(安全时)+T2修正** 为0/0路径（TWIN验证组）。

### 语料来源（pcb_analysis 259项目）
T2=kipilot解析+ByTaymur加载探针+DaCapo7回读；T1=hyuhang822三约束+CyrilKafle坏例法；T3=aiignite widen_net_traces/reroute_net FAT；T4=ByTaymur严格变好门禁。
