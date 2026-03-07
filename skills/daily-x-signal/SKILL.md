---
name: daily-x-signal
description: 从关注的 X 账号中生成高信号中文日报，包含 Top 10、今日必读、重点作者和建议关注名单。适用于 X/Twitter 晨报、过去 24 小时总结、重点作者发现和高信号帖子筛选。
metadata:
  trigger-hint: 当用户想看 X 日报、过去 24 小时动态、重点帖子排序、今日必读或重点作者池时使用。
---

# Daily X Signal

基于 `xreach`、可配置排序规则和可选 LLM 摘要能力，生成中文 X 高信号日报。

## 适用场景

- 每日晨报
- “帮我总结过去 24 小时 X 上值得看的内容”
- 不想按热度看内容，而是按干货和相关性排序
- 自动维护重点作者池

## 仓库工作流

1. 读取 `config/default.yaml` 与可选的本地覆盖配置。
2. 使用 `daily-x-signal generate` 生成 Markdown/JSON 日报。
3. 使用 `daily-x-signal show-core-authors` 查看自动生成的重点作者池。

## 常用命令

```bash
python3.11 -m pip install -e .
daily-x-signal generate
daily-x-signal generate --window-mode rolling_24h
daily-x-signal generate --mode core_authors
daily-x-signal show-core-authors
```

## 输出内容

- Markdown 日报：`output/daily-brief-YYYY-MM-DD.md`
- JSON 结果：`output/daily-brief-YYYY-MM-DD.json`
- 历史与重点作者状态：`state/history.json`

## 配置说明

- 默认调度窗口：昨天 `08:00` 到今天 `08:30`（`Asia/Shanghai`）
- 临时过去 24 小时报：`--window-mode rolling_24h`
- 回复过滤阈值：`x.reply_like_threshold`
- LLM 支持 OpenAI 兼容接口、自定义 `base_url`、`api_key` 或 `api_key_env`
- 飞书推送配置放在 `outputs.feishu`
- 真实账号信息和密钥应只保存在 `config/local.yaml`

## 依赖与前置条件

- 需要本机已安装 `python3.11`
- 需要本机已安装 `xreach`
- 如需 LLM 摘要，需要配置 `llm.api_key` 或 `llm.api_key_env`
- 如需飞书推送，需要额外配置飞书 App 凭证和接收目标

## 关于 X 登录态 / Cookies

安装这个 Skill 并不会自动获取 X/Twitter 的第三方登录 cookies，也不会自动从浏览器继承登录态。

你必须先在本机单独完成 `xreach` 认证，例如：

```bash
xreach auth extract --browser chrome
```

或：

```bash
xreach auth set --auth-token '你的_auth_token' --ct0 '你的_ct0'
```

只有在 `xreach auth check` 正常后，这个 Skill 才能稳定读取：

- 关注列表
- Home timeline
- 账号 tweets
- thread 内容

## 如果依赖其他插件 / 服务

- 飞书：需要 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和接收目标
- LLM：需要 OpenAI 兼容接口配置
- 浏览器 Cookie 提取：依赖 `xreach` 自身认证能力，而不是这个 Skill 自动完成

使用这个 Skill 时，要先确认依赖工具和认证链路已经完成，再开始生成日报。
