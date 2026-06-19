# AI Feishu Radar Bot

一个利用大语言模型（Agnes API）筛选技术动态与故障情报，并自动推送到飞书群 Webhook 的 Python 脚本。

## 功能特性

*   **数据抓取**：定时通过 RSS 获取 OpenAI、Anthropic、GitHub 官方的 Status 故障及工具链 Release 信息。
*   **双重过滤**：结合本地关键字规则预筛与大模型（Agnes AI）判定，剔除无价值信息和噪音，在大模型不可用时支持本地规则降级。
*   **事件聚合**：基于文本相似度计算，自动将同一时间段内相似的主题合并为一条消息推送，避免消息轰炸。
*   **无数据库运行**：利用 `seen.json` 文件作为去重缓存，在 GitHub Actions 中运行时会自动将缓存提交回仓库，无需维护外部数据库。

## 环境变量配置

请在运行环境或 GitHub Secrets 中配置以下变量：

| 变量名 | 是否必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `FEISHU_WEBHOOK` | **是** | - | 飞书自定义群机器人的 Webhook 地址。 |
| `AGNES_API_KEY` | 否 | - | Agnes AI 的 API Key（留空则自动使用本地规则降级运行）。 |
| `AGNES_BASE_URL` | 否 | `https://apihub.agnes-ai.com/v1` | 兼容 OpenAI 格式的 API 接口地址。 |
| `AGNES_MODEL` | 否 | `agnes-2.0-flash` | 使用的大模型名称。 |

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量并运行
export FEISHU_WEBHOOK="https://open.feishu.cn/..."
export AGNES_API_KEY="your-api-key"
python main.py
```

## GitHub Actions 部署

项目包含 `.github/workflows/ai-radar.yml` 定时任务，每次运行结束会自动将最新的 `seen.json` 缓存提交回仓库。

1.  在 GitHub 仓库中，进入 `Settings -> Secrets -> Actions` 配置上述环境变量。
2.  推荐使用外部 Cron 服务（如 `cron-job.org`）向 GitHub Actions 的 `workflow_dispatch` 接口发送 POST 请求，实现自定义频率的定时触发。

## 许可证

基于 [MIT License](./LICENSE) 协议开源。
