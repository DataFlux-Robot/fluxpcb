# h743-fc 里程碑绑定(工件/哈希/命令/工具)

每个里程碑绑定: 板文件+哈希 / 原始报告 / 运行命令 / 工具版本 —— 可复现。

## M1: FC_L13 收敛态(2026-11-03)

- **板文件**: z_lab/z_v/FC_L13/pcb/board/h743-fc.kicad_pcb(实验资产盘)
  - sha256[:16]: `01a3e96a3befdf0c`
  - 仓库快照: snapshots/M1_fc_l13/(板+报告副本)
- **实测态**: 8 断线 / 8 间距真残差 / 0 短路(kstate 实时 query 口径)
  - 注: 仓库内 pcb/board/route_report.json 的 unconnected:66 为**原始流水线快照**(2026-10 批次, 未收敛前), 不代表 M1 态
- **原始报告**: loop_final.json、fab_waivers.json、certs.jsonl
- **运行命令**: python tools/z_lab/z_clear_fc.py(清线); python tools/z_lab/z_harvest.py <board_dir> <net>(收割)
- **工具版本**: git ad66f75 + KiCad 10(pcbnew/kicad-cli)

## 发布判定语义(复审修复后)

RELEASED = 必需阶段全集通过 + 证据哈希对应当前输入 + 加工导出
成功并校验 —— 局部运行只记局部通过, 不再冒充发布。
