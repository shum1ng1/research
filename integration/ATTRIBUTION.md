# 代码来源

旧课程骨架仍在仓库根目录：`pipeline.py`、`agents/`、`tools/`、`llm_client.py`、`prompts/`。`python3 -m pipeline` 继续调用那里的 `collect`、`analyze`、`write_report`、`review`。

`integration/member_a/` 来自成员 A 的 Data 与 Reviewer 合同 v0.1（schema、SEC/Yahoo 适配器、审核器、离线夹具思路）。导入改成了 `integration.member_a`，避免和根目录的 `tools` 包重名。没有迁入成员 A 自己的 `finance_calculator.py`：那是另一套 Analysis，指标名和成员 B 不一致，四阶段里的 Analysis 只用成员 B。

`integration/member_b/` 来自成员 B 的 Analysis 与 Writer。导入改成了 `integration.member_b`。行情过滤改为比较带时区的收盘时刻。年化波动率不再填一个校验器无法重算的数。原来的 `mocks/` 使用过时字段，没有放进这个包；离线演示直接走成员 A 的 Data/Reviewer 代码和一份标明合成的传输替身。

`integration/orchestrator.py` 是新的编排，不重写两边的计算公式。
