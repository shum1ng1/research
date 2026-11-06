# AAPL 历史示例

这是 **2026-11-02（香港时间）** 模型生成的历史输出，不是当前行情，也不是已核实的财务事实或投资建议。

文件里写明的数据口径是财年 2025（报表截止 2025-09-30），市场数据截至 2026-10-30。本目录只是把那次结果放进仓库，方便没有 API key 的同学先看长什么样。这里没有重新跑模型。

| 文件 | 里面是什么 |
|------|------------|
| [AAPL.md](AAPL.md) | 研究报告草稿（当时的原文） |
| [AAPL.meta.json](AAPL.meta.json) | 比率、模型 assessment、审阅结果 |

输入、没有 key 怎么看、用自己的 key 怎么跑，写在仓库根目录说明里：[示例输出](../README.md#sample-output)。

下面几句是本目录的说明，没有写进上面两个示例文件：

- `review.ok` 是同一次流水线里的 LLM 审阅结论，不是独立的准确性核验。
- `assessment.confidence`（本例 `high`）是未经校准的模型自我报告。
- `assessment.assessment.overall_health`（本例 `4`）是模型自己打的健康分。
- `report_path` 原来是生成机器上的绝对路径 `/workspace/elec7082-agent/reports/AAPL.md`。入库时改成了相对仓库根目录的 `examples/AAPL.md`。除此以外，报告正文和 meta 的其余字段保持生成时的原文。
