# 四阶段编排（给初学者）

这条命令把四段接在一起：成员 A 的 Data、成员 B 的 Analysis、成员 B 的 Writer、成员 A 的 Reviewer。旧的 `python3 -m pipeline` 没有被替换。

支持 **MSFT**、**CSCO** 和课程示例 **AAPL**。其它代码会被拒绝。

## Mac 上一键运行

密钥只放在你自己电脑的 `.env` 里。这个文件已经在 `.gitignore` 中，**不要提交，也不要贴到聊天里**。

1. 在仓库根目录把示例复制成本地文件（已有 `.env` 时不要覆盖）：

```bash
cp .env.example .env
```

2. 用文本编辑器打开 `.env`。离线演示可以先空着。实况官方 DeepSeek 时填写：
   - `DEEPSEEK_API_KEY`：你在 DeepSeek 官网申请的密钥
   - `SEC_USER_AGENT`：项目名加你的真实联系邮箱，例如 `elec7082-research you@example.com`。SEC 要求请求带这个说明，它不是密钥，但仍然不要提交。

3. 选一种启动方式。默认都是 MSFT、`2026-11-13`、`Asia/Hong_Kong`。

- **VS Code / Cursor：** 打开“运行和调试”。`Integration: offline MSFT` 和 `Integration: live DeepSeek MSFT` 仍在。另外有 `Integration: offline AAPL` 和 `Integration: offline course pack`。它们都以模块方式从仓库根目录启动，不会去“运行当前文件”。第一次运行会先执行 `prepare-integration`：没有 `.env` 就从示例复制，然后安装 `requirements.txt`。
- **Finder 双击：** 双击 `Run-Integration.command`。回车是离线 MSFT，2 是实况 MSFT，3 是离线 AAPL，4 是三家离线演示包。

`python3 -m integration ...` 仍然可用。程序启动时会读取仓库根目录的 `.env`，已经存在的环境变量不会被覆盖。

## 一条不用密钥的命令

在仓库根目录执行：

```bash
python3 -m integration --ticker MSFT --as-of 2026-11-13 --timezone Asia/Hong_Kong --offline
```

不加 `--offline` 也是同一条离线路径。离线结果里的 `llm.mode` 是 `synthetic_templates`。它不是模型调用，不能拿去当课程里的 LLM 演示。

## 实况模型（需要你自己的密钥；这里没有替你跑通过）

官方 DeepSeek，不是港大网关：

```bash
export DEEPSEEK_API_KEY='你的官方密钥'
python3 -m integration --ticker MSFT --as-of 2026-11-13 --timezone Asia/Hong_Kong --live --provider deepseek
```

- 地址固定为 `https://api.deepseek.com/chat/completions`。默认模型 `deepseek-flash`，可用环境变量 `DEEPSEEK_MODEL` 改成文档里的 `deepseek-v4-pro`。
- 请求会关闭思考模式，并且只发送我们列出的函数。不会挂上 `deepseek_web_search` 或 `deepseek_read_url`。
- 港大网关仍可用：`--live --provider hku`，读取 `HKU_GENAI_API_KEY`。那个地址是 `https://api.hku.hk/foundry/student`，不是 DeepSeek 官方 API。
- 没有密钥、HTTP 失败、模型返回坏 JSON、调用了未登记工具、或超过工具次数，阶段状态都是 `failed`。程序不会改回模板句，也不会偷偷换成合成数据。
- 实况分析只会把少量已计算结论交给模型（大约不超过 8 条），不会把几百条重复摘录都要求一次写完。读取工具成功时日志记为 `ok`，只有真正的参数或提交错误才记为 `rejected`。
- 数字仍由 Python 计算。模型只能把已经算好的展示串原样写进句子，并引用数据包里已有的 evidence id。
- 实况成功时，`logs/llm_tools.jsonl` 会记下分析与写作的工具名和接受或拒绝。这是“确实发生过工具调用”的记录。`summary.json` 的 `llm_transport` 为 `deepseek_official`。
- Seeking Alpha 没有可用的自动检索。没有你自己准备的本地 manifest 时，覆盖状态是 `not_checked`。电话会逐字稿也一样，只导入你放进 manifest 的文件。不能把当前实现说成课程材料已经齐。

本次开发没有使用真实密钥，也没有访问 DeepSeek、港大、Yahoo 或 SEC。上面的实况命令需要你在自己的电脑上配置密钥后再验证。

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

不加 `--live` 时一直是离线演示，不会去访问港大或 DeepSeek。实况必须显式加上 `--live`，并且密钥来自本机 `.env`。

## 和旧流水线的差别

| | `python3 -m pipeline` | `python3 -m integration --offline` |
|---|---|---|
| 输入 | 只有 ticker | ticker + as_of + timezone |
| 截止时间 | `as_of` 只是结果上的日期标签，价格和新闻取当前数据 | `cutoff_at` 是该时区当天 23:59:59，行情按收盘时刻过滤 |
| 结果 | `reports/TICKER.md` 和一份 meta | 每一阶段一份 JSON，外加总表和日志 |
| 审核打回 | 不打回 | 按 `target_agent` 打回，最多两轮 |

## 真实网络没有在这里验证

不带 `--offline` 时，Data 和 Reviewer 会调用 `integration/member_a/tools/agent_llm.py`。默认地址是港大学生 Foundry 网关 `https://api.hku.hk/foundry/student`，路径是 `POST /models/chat/completions`，默认部署名是 `DeepSeek-V3.2`。这是学校网关上的名字，**不是 DeepSeek 官方 API**。本次改动没有用真实密钥，也没有访问 Yahoo、SEC 或该网关。

真实 Yahoo 历史价即使以后接上，也不是“当时看到的档案”，供应商以后仍可能修订旧 K 线。若本机的交易所日历比研究日更短，已下载的交易日仍会保留，收盘时刻改用美东 16:00，并在 `quality_checks` 里记一条警告；日历覆盖到的日子（含提前收盘）仍用交易所时刻。Yahoo 没有返回的日期不会被编造。Yahoo 本身返回空表时，这一步仍会失败。

人工对照原表的抽查仍然没有做。PDF 页数会在导出后自动核对，超过 10 页会失败。离线配置把人工抽查数量设为 0，并在限制里写明。

## 图表、10 页 PDF、三家公司演示包

成功写出的报告会带 SVG 图表（有已计算指标时）和 A4 PDF。`pdf_path` 与 `page_count` 是真实值。超过 10 页会失败。没有可画的指标时不画图，并在警告里说明。

电话会和 Seeking Alpha 只读 `imports/{公司}/` 里你自己放的文件，说明见 `imports/README.md`。没有文件就是 `not_checked`，不会编造。SEC 若返回 8-K，会标成 SEC 文件，不会冒充电话会。

离线三家演示（合成数据，不是正式交卷）：

```bash
python3 -m integration.pack --offline
```

结果在 `course_pack/offline_demo/`，目录里的 README 会再次标明这不是正式稿。正式稿要在本机填好 `.env` 后执行 `python3 -m integration.pack --live`。

10 分钟方法录像请照 `course_pack/METHODOLOGY_RECORDING_SCRIPT.md` 自己录。脚本讲框架和模型如何使用已取到的数据，不讲投资结论，也不出现密钥。录像文件不会由这个仓库生成。

## 测试

```bash
python3 -m unittest tests.test_c_orchestrator tests.test_live_llm_tools tests.test_course_delivery -v
```

测试不访问 Yahoo、SEC 或 DeepSeek，也不读取真实密钥。
