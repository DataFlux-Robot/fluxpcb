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
