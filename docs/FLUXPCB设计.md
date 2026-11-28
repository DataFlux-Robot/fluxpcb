# FLUXPCB设计

对于fluxpcb，我们有如下规划

# 核心目标：让fluxpcb完全无人条件下的PCB设计并满足加工需求，形成通用的PCB设计工具，并基于完善后的fluxpcb完成当前飞控的设计直到可以满足加工需求

- 面向全流程的：

    - 需求分析

    - 器件选型

    - 原理图

    - PCB设计

    - 验收仿真

    - 固件开发

- 完全无人参与的自动化

- 针对任意pcb设计需求的，尤其是针对BGA

- 主要基于kicad



# 整体架构目标：

fluxpcb应该具有如下“两层loop结合rule based方法”的整体架构：

|组成|作用|参考|
|---|---|---|
|超越开发的loop及workflow|复杂pcb设计依然是前沿技术，尤其是bga对于人类开发工程师也是一个艺术，因此需要在实际测试中不全完善agent能力|HarnessPRO（https://arxiv\.org/html/2610\.26770、https://github\.com/pzs19/HarnessPro）|
|上上层超越loop和harness|目前skill可能不完美，需要使用过程中不断完善<br>这里要注意针对是任意pcb设计的通用能力而不是把具体方案写死|HarnessPRO（https://arxiv\.org/html/2610\.26770、https://github\.com/pzs19/HarnessPro）|
|具体执行开发的loop及workflow|复杂PCB，即使顶尖人类设计工程师也不可能一次就画好，因此必须一定涉及到反复结合具体现象进行迭代<br>这里要注意针对是任意pcb设计的通用能力而不是把具体方案写死|HarnessPRO（https://arxiv\.org/html/2610\.26770、https://github\.com/pzs19/HarnessPro）|
|具体执行总体pcb设计的loop和harness agent|引导llm进行具体的布线的总体方案决策，这里类似pcb的设计总体设计师<br>实现如下总体规划（一定要有有整体和总体的规划，要综合多个维度事情和反馈进行信息情报汇总）<br>1，叠层规划<br>2，阻抗规划<br>3，器件摆放位置和朝向规划（协同调整器件位置（就像是要在繁忙的交通线附近建立补给中心，而是随便找个地方建个补给中心，然后再让路修过来，当然补给中心和路是动态博弈最终区域均衡））<br>同时可选执行视觉LLM审版图（takzen）。，然后结合loop过程实现：不同线路地协商机制、回归推挤方法或者撕开重画|1，现有259个现有LLM\+PCB相关的项目，\[AI\_PCB项目七环节覆盖分析\]\(kimi\-cite://text?path=E%3A\\DATA\\vscode\\ZCODE\\pcb\_analysis\\AI\_PCB项目七环节覆盖分析\.xlsx\&text=数据来源%3A用户提供376条URL\)<br>2，KiCadAI「确定性搜索/仿真/验证，证不出即拒绝」<br>3，nopekit「证伪校验证据链」<br>4，Quilter<br>5，PCBRouteAgent<br>6，https://github\.com/liuem/LCEDA\-pcb\-bga\-decouple<br>7,https://zhuanlan\.zhihu\.com/p/608080867<br>8,https://resources\.altium\.com\.cn/p/how\-to\-successfully\-design\-a\-bga<br>9,NextBoard「7 阶段流程\+5 道验证门控」<br>10，AI\-PCB\-layout「九步人审门」<br>11，NextBoard 需求冻结与评审门控的追溯思路<br>12，nopekit 证据链、「门禁即规范」的诚实化落地<br>|
|总体agent和底层agent的通信runtime|保持上下层有效（不要出现错误）、高效（下层agent要进行总结，不要把所有都一股脑袋扔上来），这里参考dsh superagent<br>避免出现如下类似错误：<br>1，“根因 c——测量不绑定状态。 陈旧 zone 填充上的 40 与重灌后的 66，都是"开路数"但属于两个板状态——数字从不携带它所测的状态指纹，混用无法察觉。”这是重大设计缺陷，而且这是最基本地错误和软件设计逻辑，为什么之前从没有发现和解决？<br>2，我感觉目前连最基本地软件开发逻辑都忘记，尤其是“配置静默改变语义。 unconnected\_items: ignore 这类校准让同一个查询名（"unconnected"）在不同时间返回结构性不同的东西，下游却当同一个量消费。语义变了没人知道。”、“根因 c——测量不绑定状态。 陈旧 zone 填充上的 40 与重灌后的 66，都是"开路数"但属于两个板状态——数字从不携带它所测的状态指纹，混用无法察觉。”出去了让人笑掉大牙<br>3，“消费端拒绝无指纹数字：门禁和 driver 对没有 fp 的数值直接打 FAIL 而不是 warning（当前是 warning 级，要升硬约束）；”这个错误更是巨大愚蠢错误<br>4，“配置静默改变语义。 unconnected\_items: ignore 这类校准让同一个查询名（"unconnected"）在不同时间返回结构性不同的东西，下游却当同一个量消费。语义变了没人知道。”这是重大设计缺陷，而且这是最基本地错误和软件设计逻辑，为什么之前从没有发现和解决？|https://github\.com/deepseek\-ai/dsh\-superagent\-prototype|
|具体执行的subagent|结合具体布线器的反馈，执行如下关键决策：<br>1，信号分类，然后按顺序来，比如先走高速或者敏感线，再走普通线，再走电源线||
|llm\+和rule based方法下发runtime<br>|调用下面纯rule based方法的mcp，并结合子智能体或简单的对话进行简单的决策，比如：<br>2，是否引入过孔、盲孔、埋孔<br>3，布线类型：等长线、差分线、普通线<br>4，球数（如64/100/144/256，参考具体工艺限制）<br>5，过孔钻孔直径（默认0\.2 mm，参考具体工艺限制）<br>6，球间距（默认0\.8 mm，参考具体工艺限制）<br>7，逃逸线宽（默认0\.10 mm，参考具体工艺限制）<br>8，过孔焊盘外径（默认0\.45 mm，参考具体工艺限制）<br>9，BGA焊球焊盘直径（默认0\.4 mm，参考具体工艺限制）<br>|1，circuit\-agent\-client，calculate\_bga\_escape\_routing|
|llm\+和rule based方法反馈runtime|同时要打通“llm决策 和 rule based决策间”双向反馈，比如llm方案求解不出解/工艺实现不了||
|基于纯rule based方法的布线器|具体走线不需要llm在具体实现，毕竟目前有大量的专门的rule based的高效率布线方法（你不能指望纯llm通过手写坐标实现精准的差分和等长布线，太费token了），比如：<br>1，蒙特卡洛<br>2，A\*<br>3，模拟退火（tbrandenburg、M0\-AR）<br>4，力导向/聚类（param、Nsai\-roshan Leiden）<br>5，NSGA\-II Pareto（LorenzoTonet）<br>6，GAT/RL学习布局（elcruzo、kunal\-gh）<br>7，NCER 的精确布线<br>8，稠密板外接 Freerouting（DSN/SES 桥）<br>9，kicad\-cli 无头 DRC|1，FanoutNet<br>2，Konnect/easyeda\-agent 的 Freerouting 集成路线<br>3，PCBRouteAgent<br>4，参考光线追踪思想进行布线，光线追踪\(Eikonal\)和nvidia warp（https://github\.com/nvidia/warp）<br>5，星际争霸2单位巡线的思路（上百个单位并行规划，时间极短）<br>6，diffusion 模型|
|系列验证器|对如下等方面进行验证和仿真：<br>1，电源完整性<br>2，高速布线设计质量<br>3，散热仿真（OpenFOAM（chtMultiRegionFoam）、Elmer、ParaPower）<br>4，SPICE功能仿真<br>5，aklofas含PCB寄生与容差<br>6，tipoLi corner矩阵<br>7，ChengminLi功率级/闭环/冷启动<br>8，openEMS/HFSS场级S参数终验/ EM 场终验/信号完整性（AvatarSD gerber2ems、himanshu两阶段FDTD门禁）<br>9，yelabb去耦距离<br>10，敏感网隔离评分<br>11，CyrilKafle 28项检查<br>12，IR\-drop/热 FDM<br>13，PDN 目标阻抗法<br>14，ngspice 直流/瞬态/交流扫描\+蒙特卡洛<br>15，Verilator 协议 VIP；<br>16，角点评估<br>17,地平面回流仿真\(openEMS\)<br>18,地覆铜谐振仿真（openEMS（扫频）、Palace（FEM 本征求解器））<br>19,直流压降仿真（KiPIDA、padne（KiCad 插件/工具））<br>20,直流电流密度仿真，看一下电流是否拥挤\(KiPIDA、padne（KiCad 插件/工具）\)<br>21,电源分配网络，供电分析（openEMS \+ scikit\-rf \+ ngspice 流水线）<br>23,辐射方向图仿真（openEMS（远场变换）、Meep（near2far））|1，现有259个现有LLM\+PCB相关的项目，\[AI\_PCB项目七环节覆盖分析\]\(kimi\-cite://text?path=E%3A\\DATA\\vscode\\ZCODE\\pcb\_analysis\\AI\_PCB项目七环节覆盖分析\.xlsx\&text=数据来源%3A用户提供376条URL\)<br>2，NextBoard「7 阶段流程\+5 道验证门控」<br>3，AI\-PCB\-layout「九步人审门」<br>4，NextBoard 需求冻结与评审门控的追溯思路<br>5，nopekit 证据链、「门禁即规范」的诚实化落地<br>6，mcp\-pcb\-emcopilot <br>7，kicad\-happy 的 EMC 预合规思路<br>8,AI\-PCB\-Generator、kicad\-happy、KiSkill、hfss\-mcp\-server、openCologne\-PCIE、KiCadAI。\[AI\_PCB项目七环节覆盖分析\]\(kimi\-cite://text?path=E%3A\\DATA\\vscode\\ZCODE\\pcb\_analysis\\AI\_PCB项目七环节覆盖分析\.xlsx\&text=按子电路自动生成SPICE测试台验证滤波%2F分压%2F增益\)<br>9，openEMS（电流密度可视化）<br>10，OpenFOAM（chtMultiRegionFoam）、Elmer、ParaPower<br>11，openEMS（扫频）、Palace（FEM 本征求解器）<br>12，KiPIDA、padne（KiCad 插件/工具）<br>13，openEMS \+ scikit\-rf \+ ngspice 流水线<br>14，openEMS（远场变换）<br>15，Meep（near2far）|
|工艺检验，保证pcb方案可以直接加工并能用|满足外送加工标准等，比如：<br>1，拼板（yangchao0818 Guillotine装箱、jackc1224）<br>2，厂商DFM规则集/JLC fab\-limits DRU（h0lad、neuman、pcbaSprint）<br>3，STEP 3D输出<br>4，erber级连通性对IPC网表校验（studio\-edul）<br>5，IPC\-7351 封装审计；<br>6，IPC\-2221 十二项 DFM|1，https://jlcpcb\.com/hk/help/article/bga\-design\-guidelines\-\-\-pcb\-layout\-recommendations\-for\-bga\-packages<br>2，kicad\-mcp\-pro 的制造发布门禁<br>3，easyeda\-copilot<br>4，Konnect<br>5，eda\-agent<br>6，AI\-PCB\-Generator。<br>7，https://www\.jlc\.com/portal/vtechnology\.html<br>8，https://www\.jlc\.com/portal/vtechnology\.html<br>9，https://www\.jlc\.com/portal/vtechnology\.html<br>10，https://tools\.jlc\.com/jlcTools/\#/impedanceDefaultTemplate?spm=PCB\.Homepage\.functionbar\.1004\&jlc\_vid=ElJfBAIFEVkMVgYDRVJZVFUHT1ZWVFxXRgNaAlcFQVkxVlNfRFRXVlVeRVRYUjsOAxUeFF5JWBYZEEoBGA4JCwFIFA4DSDsOAxUeFF5JWBYZEEoMGwJABAsL\&jspm=pcb\.pp\.c\-pfw\.p\-1\-1|
|辅助功能|比如可视化|参考https://github\.com/neuman/nopekit补充一个本地网页的可视化的Grounding map，让用户可以监控“需求分析、器件选型、原理图、PCB设计、验收仿真（热等领域领域的逐个分析）、固件开发”的整个流程|

# 开展准则

## 迭代，一次一个完美的一次性技巧解决问题是不可能的

bga设计的确是一个复杂任务（人类工程师画起来都头疼，只能反复loop返工迭代）

总试图找一个完美的一次性技巧解决问题，如果没有解决就立即抛弃，但是实际上这里是一个loop和闭环迭代过程（即使是专业pcb设计者也不可能一次性就是设计完成，也是反复调整的），我感觉你可以参考RSI思想（参考https://github\.com/ExuberantWitness/awesome\-code\-as\-agent/blob/main/README\_CN\.md的RSI研究？比如https://github\.com/Gen\-Verse/RSI\-Anything/blob/main/README\.zh\-CN\.md），甚至还有更加激进的项目：https://github\.com/muhammadmapkar/KiCad\_AI\_LoRA

很好，继续磨山，你可以参考这个研究：https://arxiv\.org/html/2610\.26770，https://github\.com/pzs19/HarnessPro

同时参考“Automating ML for Science:Can Frontier Agents Climb Scientific Hills in the Wild?”

## 要进行基准测试

比如如下基准：

- autorouter\-bench

- pcbbench？

- autorouter\-bench/PCB\-Bench/eda\-bench/https://github\.com/LGAI\-Research/PCBWorld

## 优先修复fluxpcb，然后用修复后的fluxpcb开展具体的设计

我们目前核心是开发系统，目前具体设计案例仅



# 重要参考

## 关键工具

**1\. 地平面回流仿真 — openEMS（有开源实现）**



[openEMS](https://www.openems.de)（GPL，FDTD 法）是主力：用 CSXCAD 建立微带线 \+ 开槽地平面模型，端口激励后在金属表面加电流密度 dump（`AddDump('J')`），在 [ParaView](https://github.com/thliebig/openEMS) 里直接看回流在地平面的分布——能看到高频时回流集中在走线正下方、过缝时绕行产生的热点。官方 examples 仓库里有微带过孔、平面耦合等可直接改的例子。若只关心准静态（kHz\~低 MHz）回流分配，MIT 的 FastHenry 也能提取各导体分段电流。注意要点：FDTD 网格要能分辨介质层厚度，仿真域内存开销大，建议先做小尺寸局部模型而不是整板。



**2\. 散热仿真 — OpenFOAM / Elmer / ParaPower（有开源实现）**



- **OpenFOAM 的 ****`chtMultiRegionFoam`** 是最接近 Icepak 的方案：流体\+固体共轭传热，可算强迫风冷/自然对流。参考 [Tensor Engineering 的 PCB 冷却教程](https://tensorengineering.us/pcb-cooling-cfd-simulation-using-openfoam)和 [Antmicro 的电子设备主动冷却工作流](https://antmicro.com/blog/2026/04/simulating-active-cooling-using-a-cfd-based-flow)。

- [**Elmer FEM**](https://www.elmerfem.org)：[jrainimo 的教程](https://jrainimo.com/build/2024/11/oss-thermal-simulation-of-pcbs)演示了从 KiCad 导出铜皮做电流仿真，再把焦耳热耦合进热仿真的完整流程（这也是第 5 项电−热耦合的做法）。

- [**ParaPower**](https://github.com/USArmyResearchLab/ParaPower)（美陆军研究实验室，MATLAB）：3D 热阻网络模型，比 FEA 快约百倍、误差 \<2%，适合功率器件/板级的快速参数化扫设计空间。

- Antmicro 还有开源的 [热仿真脚本 \+ Blender 三维可视化](https://github.com/antmicro/thermal-simulation-scripts)。

    

与 Icepak 的差距：没有元器件热模型库、没有 ECAD 直接导入，铜皮/过孔/器件功耗都得自己建模成网格，前处理工作量大。只想做传导（自然冷却估算）时 Elmer/CalculiX 就够；要算风场就得用 OpenFOAM。



**3\. 地覆铜谐振仿真 — openEMS / Palace（有开源实现）**



- **openEMS**：对平面腔体（两铜皮 \+ 介质）做宽带 S 参数扫频，S21/输入阻抗曲线上直接读出腔体谐振频率；在谐振频点 dump 电流密度可看到驻波腹点——这就是 HFSS 谐振模式的时域等价做法。

- [**Palace**](https://github.com/awslabs/palace)（AWS 开源，GPU 加速 FEM）：有真正的**本征模求解器**（官方示例就有圆柱腔本征模），配合 Gmsh 划分铜板网格，是最接近 HFSS Eigenmode 的开源工具，大模型并行能力好。

- 补充：Meep 可用 harminv 从时域信号提取谐振频率；简单的矩形平面腔也可用解析腔模公式或 ngspike 传输线网格法估算。

    

**4\. 直流压降（IR drop）— KiPIDA / padne（有开源实现，且是现成工具）**



这一项开源生态最成熟，KiCad 生态里就有多个直接对标 SIwave DCIR 的工具：



- [**KiPIDA**](https://github.com/kbralten/KiPIDA)：原生 KiCad 插件，直接在版图上仿真压降、电流密度、功耗分布。

- [**padne**](https://forum.kicad.info/t/presenting-padne-a-tool-for-dc-power-delivery-network-analysis/64966)（2025 年 10 月发布）：FEM 法做 DC 压降仿真，KiCad 板直接导入。

- [**FYPA**](https://github.com/anarthrous-eda/FYPA)：支持 Altium 和 Gerber 输入的 PDN 压降/电流密度分析，带三维可视化。

- 另有 KiCad 论坛上的 [nikfemm 压降仿真插件](https://forum.kicad.info/t/fem-voltage-drop-simulator/57710)、据称对标过 HyperLynx PI 的 [KiPI](https://www.reddit.com/r/PCB/comments/1x3q658/kipi_kicad_pi_solver_plugin) 等。

    

如果不在 KiCad 里做，通用路线是：铜皮几何（DXF/Gerber/STEP）→ Gmsh 划网格 → Elmer 的 StatCurrentSolver 或 scikit\-fem/FEniCS 解 ∇·\(σ∇φ\)=0，自己得到完整 V\(x,y\) 分布。



**5\. 直流电流密度（电流拥挤）— 同上（有开源实现）**



压降和电流密度是同一个导电求解的两面（J = −σ∇V），上面 KiPIDA / padne / FYPA / Elmer 的输出都直接包含电流密度云图，可以看铜皮颈缩、过孔群、封装焊盘处的电流拥挤，以及每个过孔的分流电流。补充一个轻量工具：**FEMM**（2D FEM）有 current flow 求解类型，适合单层截面快速核查，但做不了多层堆叠的全板模型。



**6\. 电源分配网络（PDN 阻抗 Z\-target）— 无一体化开源工具，但有成熟流水线**



这是唯一没有现成“一键”开源工具的项，标准做法是三段拼接：



1. 用 **openEMS** 提取平面对的 S 参数（含过孔、去耦焊盘端口）；

2. 用 [**scikit\-rf 的 VectorFitting**](https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.vectorFitting.VectorFitting.write_spice_subcircuit_s.html) 把 S 参数拟合成有理宏模型并导出 SPICE 子电路（官方支持导出 LTspice 兼容网表，含无源性修正）；

3. 在 **ngspice / Xyce / Qucs\-S** 里把该子电路与 VRM 模型、去耦电容 ESL/ESR 串联，AC 扫频得到 Z\(f\)，对照目标阻抗曲线、扫去耦组合。

    

另有 [GT\-CHIPS 的 PDN\-Impedance\-Analysis](https://github.com/GT-CHIPS/PDN-Impedance-Analysis)（MATLAB）做互连/封装/PCB 平面的 PDN 阻抗分析。注意：芯片封装内电感、VRM 小信号模型需要器件厂商数据，这部分任何工具都替代不了。



**7\. 辐射方向图 — openEMS / Meep（有开源实现）**



- **openEMS** 内置近场到远场变换（nf2ff），官方例子就有 inset 馈电贴片天线的方向图、增益、S11 全套；[pyems](https://github.com/matthuszagh/pyems) 提供了更高层的 Python 封装（微带、天线、滤波器建模更省事）。

- [**Meep**](https://meep.readthedocs.io)（MIT）用 `add_near2far` / `get_farfields` 算远场，官方示例 [plot\_radiation\_pattern\_dipole\.py](https://github.com/NanoComp/meep/blob/master/python/examples/plot_radiation_pattern_dipole.py) 可直接改成 PCB 天线，还支持并行。

- 若是线天线（如 PCB 上的单极子/环形天线），xnec2c/NEC\-2 也可用，但对平面金属结构效率不如 FDTD。