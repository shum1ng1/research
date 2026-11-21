# 四阶段编排（给初学者）

这条命令把四段接在一起：成员 A 的 Data、成员 B 的 Analysis、成员 B 的 Writer、成员 A 的 Reviewer。旧的 `python3 -m pipeline` 没有被替换。

只支持 **MSFT** 和 **CSCO**。其它代码会被拒绝。

## 一条不用密钥的命令

在仓库根目录执行：

```bash
python3 -m integration --ticker MSFT --as-of 2026-11-13 --timezone Asia/Hong_Kong --offline
```

不加 `--offline` 也是同一条离线路径。只有显式加上 `--live` 才会尝试港大网关，而且那条路径没有在这里验证过。

- `--as-of` 是研究日，格式 `YYYY-MM-DD`。
- `--timezone` 是 IANA 时区名。程序把它收成当天 `23:59:59` 的 `cutoff_at`。香港是 `2026-11-13T23:59:59+08:00`，纽约是 `2026-11-13T23:59:59-05:00`，这两个时刻不是同一个瞬间。
- `--offline` 使用合成夹具。不需要 API key，也不会访问 Yahoo、SEC 或模型。

退出码：`0` 表示总状态 `success`，`2` 表示 `manual_review`，`1` 表示 `failed`。

## 跑完以后看这些文件

目录名是 `runs/MSFT_2026-11-13_Asia_Hong_Kong/`。

| 文件 | 作用 |
|---|---|
| `summary.json` | 总结果。`final_status` 是 success / failed / manual_review。`execution_status` 是程序跑得顺不顺。`review_decision` 是审核结论 pass / revise / manual_review。这两件事分开看。 |
| `report.md` | 给人看的草稿，开头多了一行“合成演示稿”。 |
| `reports/MSFT_draft.md` | Writer 交给 Reviewer 的正文，没有上面那行说明。 |
| `request.json` | 实际使用的 ticker、as_of、timezone、cutoff_at。 |
| `config.json` | 本轮配置。`project_root` 写成 `.`，不记录本机绝对路径。 |
| `attempts/00/data.json` | 第 1 次 Data 外壳。下一阶段只使用里面的 `payload`。 |
| `attempts/00/analysis.json` | 第 1 次 Analysis。 |
| `attempts/00/writer.json` | 第 1 次 Writer。 |
| `attempts/00/review.json` | 第 1 次 Reviewer。`payload.decision` 才是审核结论。 |
| `logs/orchestrator.jsonl` | 编排日志。 |
| `logs/data_tools.jsonl` 和 `logs/reviewer_tools.jsonl` | 成员 A 的工具日志。 |

如果审核结论是 `revise`，编排会从最早的 `target_agent` 重跑，并重跑它后面的阶段。初始这一次之外最多再跑 **两轮**。目录会是 `attempts/01/` 和 `attempts/02/`。两轮后仍然是 `revise`，总状态记为 `manual_review`，`review_decision` 仍保留最后一次的 `revise`。

`execution_status: partial` 同时 `final_status: success` 是预期情况：年化波动率没有填数（公式校验器不能重算对数和平方根），所以 Analysis/Writer 的程序状态是 partial，但审核结论可以是 pass。

## 这些数字不是公司真实财务

离线夹具里的财报、股价和“管理层”句子都是编出来的，文件里标了 `SYNTHETIC TEST FIXTURE`。语义判断也是替身，模型名记为 `synthetic_fixture`。不要把 `report.md` 当成微软或思科的研究报告。

没有密钥时不要去掉 `--offline`。那样会走港大学生网关，Data 阶段会因为没有 `HKU_GENAI_API_KEY` 失败，并且不会假装已经调用了模型。

## 和旧流水线的差别

| | `python3 -m pipeline` | `python3 -m integration --offline` |
|---|---|---|
| 输入 | 只有 ticker | ticker + as_of + timezone |
| 截止时间 | `as_of` 只是结果上的日期标签，价格和新闻取当前数据 | `cutoff_at` 是该时区当天 23:59:59，行情按收盘时刻过滤 |
| 结果 | `reports/TICKER.md` 和一份 meta | 每一阶段一份 JSON，外加总表和日志 |
| 审核打回 | 不打回 | 按 `target_agent` 打回，最多两轮 |

## 真实网络没有在这里验证

不带 `--offline` 时，Data 和 Reviewer 会调用 `integration/member_a/tools/agent_llm.py`。默认地址是港大学生 Foundry 网关 `https://api.hku.hk/foundry/student`，路径是 `POST /models/chat/completions`，默认部署名是 `DeepSeek-V3.2`。这是学校网关上的名字，**不是 DeepSeek 官方 API**。本次改动没有用真实密钥，也没有访问 Yahoo、SEC 或该网关。

真实 Yahoo 历史价即使以后接上，也不是“当时看到的档案”，供应商以后仍可能修订旧 K 线。

人工原表抽查和 PDF 页数没有做。离线配置把抽查数量设为 0，并在限制里写明。

## 测试

```bash
python3 -m unittest tests.test_c_orchestrator -v
```

测试只跑合成夹具和脚本化审核，不安装额外依赖，不访问网络。
