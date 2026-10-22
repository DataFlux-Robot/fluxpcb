# stm32h743-px4 需求（原始自然语言输入）

> 用fluxpcb开发36mm×36mm的H743 bga PX4飞控。

（/fluxpcb 流程映射为 px4-fmu 板族 h743 变体；MCU数据来自ST官方STM32H743XIHx引脚数据库。）

**诚实记录**：
- 封装为 TFBGA240 0.8mm（17×17栅格，ST官方XML直出）
- H7片内1.8V LDO仅需VCAP电容（比N6的SMPS电感方案简单）
- 5V→3V3 为AP2112K LDO骨架（量产FC惯用buck，升级列ROADMAP）
