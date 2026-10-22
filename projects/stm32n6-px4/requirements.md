# stm32n6-px4 需求（原始自然语言输入）

> 做一个55mm×28mm的STM32N6 PX4飞控：FMUv6全冗余传感器（双IMU+双气压+磁力计）、USB-C供电、CAN、GPS/遥测/RC串口、6路PWM输出、SWD调试口。

（/fluxpcb 流程映射为 px4-fmu 板族 spec 参数；MCU数据来自ST官方STM32N657X0HxQ引脚数据库。）

**诚实记录**：
- 封装为 TFBGA264 0.8mm（非UFBGA176）——双源验证的KiCad资产仅存在于该封装
- 双IMU冗余采用 ICM-42688-P×2（BMI055的双SPI引脚映射未验证，资产保留于reference/）
- 5V→3V3 为AP2112K LDO骨架（PX4量产板惯用buck，升级列入ROADMAP）
