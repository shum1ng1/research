# 本地电话会与 Seeking Alpha

程序不会登录 Seeking Alpha，也不会编造逐字稿。把你合法下载的文件放在这里，Data 才会把覆盖状态从 `not_checked` 改成已导入。

每个公司单独一个文件夹，文件名必须是下面两个：

```text
imports/MSFT/management_manifest.json
imports/MSFT/transcript.txt
imports/MSFT/seekingalpha_manifest.json
imports/MSFT/article.txt
```

`CSCO` 和 `AAPL` 同样处理。不要把示例复制成空壳后假装已经找到材料。

`management_manifest.json` 最小字段：

```json
{
  "documents": [
    {
      "company_id": "MSFT",
      "category": "management_materials",
      "source_type": "IR",
      "document_type": "earnings_transcript",
      "title": "换成真实标题",
      "source_url": "https://www.microsoft.com/换成真实公开地址",
      "language": "en",
      "available_at": "2026-10-30T21:00:00-04:00",
      "source_timezone": "America/New_York",
      "local_path": "imports/MSFT/transcript.txt",
      "access_method": "manual_download"
    }
  ]
}
```

Seeking Alpha 清单把 `category` 写成 `third_party_research`，`source_type` 写成 `SEEKING_ALPHA`，`source_url` 必须是 `seekingalpha.com` 的公开地址。

没有这两个清单时，覆盖状态保持 `not_checked`。SEC 自动取得的 8-K 会标成 SEC 文件，不会被叫成 Seeking Alpha 或电话会逐字稿。

实况运行（`--live`）会自动发现上述路径。离线演示仍使用合成夹具，不会把你的本地文件说成合成数据，也不会把合成数据说成正式稿。
