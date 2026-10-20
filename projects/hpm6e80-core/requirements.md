# FluxNeurotransmitter — hpm6e80-core 设计需求

树莓派Zero尺寸的HPM6E80工业控制核心板 + 载板,由六环节全自动流水线设计。

## 锁定决策(2026-10-20,三轮AskUserQuestion确认)

1. **两板架构**:核心板65×30mm(树莓派Zero尺寸)+ 载板(网口RJ45/天线/USB-C)
2. **主控**:HPM6E80IVM1(RISC-V双核600MHz,片上2MB SRAM无DDR,内嵌Beckhoff授权EtherCAT ESC,3千兆MAC)+ 16MB QSPI NOR(MXIC 25L12833,EVK同款)
3. **网络3口**:EtherCAT×2(IN/OUT菊花链,ESC口,100M MII PHY=JL1111,EVK同款)+ TSN×1(千兆,RGMII PHY=RTL8211E-VB-CG,EVK同款);**PHY全部在核心板**,B2B连接器走变压器前MDI差分对
4. **协处理器**:ESP32-S31-WROOM-3U模块(双核RISC-V 320MHz,WiFi6+BT5.4),IPEX线缆把天线移到载板边缘;与HPM6E80 SPI互联(HPM主/ESP从,4线+DMA+2根握手GPIO,目标40MHz)
5. **电源**:载板USB-C 5V输入 → 核心板多路buck(3V3/1V8/1V1),整机功耗约3-5W
6. **EDA**:KiCad + 6层板,嘉立创6层工艺,IPC-II(HPM6E80封装<0.65mm BGA时升8层,唯一预留变更)
7. **调试**:SWD 2×5 1.27mm + UART0(PA00/PA01,EVK同款)
8. **门禁**:全部阻塞(ERC/DRC零错+阻抗/SI+IR-drop≤3%+热≤85℃+EMC预合规+固件真实编译)
9. **固件范围**:协议栈完整级(EtherCAT对象字典可配置+PDO演示+入OP;TSN 802.1AS/Qbv;ESP32 WiFi6 STA+SPI透传+吞吐测试)

## 参考库

- reference/evk_bsp/:hpm_sdk官方hpm6e00evk板级支持(pinmux/board,引脚配置权威来源)
- reference/HPM6E00EVK_UG.pdf:EVK用户手册(PHY型号/网络架构来源;PDF不入库,按需下载)
