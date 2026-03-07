# daily-x-signal

`daily-x-signal` 是一个面向中文用户的 X 高信号日报工具。它会从你关注的人里筛出更值得看的帖子，做优先级排序，给出中文摘要，并挑出当天最值得细读的一条。

## 能做什么

- 扫描关注账号在固定时间窗口或过去 24 小时内的帖子
- 按主题相关性、干货程度、社交信号排序
- 输出 Top 10、今日必读、建议额外关注
- 自动维护重点作者池
- 支持使用 OpenAI 兼容接口做更高质量的中文摘要
- 支持把结果输出到本地文件或飞书

## 当前能力

- 基于 `xreach` 抓取关注账号内容
- following 同步失败时回退到本地缓存
- 支持 `reply_like_threshold` 过滤高质量回复
- 默认调度窗口：昨天 `08:00` 到今天 `08:30`（`Asia/Shanghai`）
- 支持手动按过去 `24h` 生成临时报
- 输出 Markdown 和 JSON
- 自动维护 core author 历史
- 支持飞书 Webhook 或飞书 App 方式推送
- 支持 GPT-5.4 或任意 OpenAI 兼容接口

## 快速开始

```bash
git clone https://github.com/wangyaya-703/daily-x-signal.git
cd daily-x-signal
python3.11 -m pip install -e .

daily-x-signal generate
daily-x-signal generate --window-mode rolling_24h
daily-x-signal show-core-authors
```

## 配置方式

基础配置文件：
[config/default.yaml](config/default.yaml)

本地私有配置文件：
[config/local.example.yaml](config/local.example.yaml)

复制方式：

```bash
cp config/local.example.yaml config/local.yaml
```

`config/local.yaml` 已经加入 `.gitignore`，不会上传到 GitHub。真实密钥、个人账号信息、私有推送目标都应该只写在这个文件里。

### 你至少需要补的字段

```yaml
x:
  viewer_handle: your_x_handle
  viewer_user_id: "your_x_user_id"

llm:
  enabled: true
  provider: openai_compatible
  api_style: responses
  model: gpt-5.4
  base_url: https://api.openai.com/v1
  api_key: your-local-api-key
```

## LLM 配置

支持两种方式：

1. 直接写在 `config/local.yaml`

```yaml
llm:
  api_key: your-local-api-key
```

2. 使用环境变量

```yaml
llm:
  api_key_env: DAILY_X_SIGNAL_API_KEY
```

## 飞书配置

如果要推送到飞书，建议只在 `config/local.yaml` 中启用：

```yaml
outputs:
  feishu:
    enabled: true
    delivery_type: app
    app_id_env: FEISHU_APP_ID
    app_secret_env: FEISHU_APP_SECRET
    receive_id_env: DAILY_X_SIGNAL_FEISHU_RECEIVE_ID
    receive_id_type: email
```

## 常用命令

生成固定窗口日报：

```bash
daily-x-signal generate --override-config config/local.yaml
```

生成过去 24 小时报：

```bash
daily-x-signal generate --window-mode rolling_24h --override-config config/local.yaml
```

查看重点作者池：

```bash
daily-x-signal show-core-authors
```

同步关注列表：

```bash
daily-x-signal sync-authors --override-config config/local.yaml
```

## 输出文件

- Markdown 日报：`output/daily-brief-YYYY-MM-DD.md`
- JSON 日报：`output/daily-brief-YYYY-MM-DD.json`
- 飞书卡片预览：`output/feishu-preview/daily-brief-YYYY-MM-DD.json`
- 关注缓存：`state/following_cache.json`
- 历史统计：`state/history.json`
- 重点作者池：`state/core_authors.json`

## 公开仓安全说明

- 公开仓只保留示例配置，不保留真实密钥
- `config/local.yaml` 不会进入 Git
- `.env.local` 不会进入 Git
- 账号 ID、绝对本机路径、私有接收目标都不应该写进可提交文件

## Skill

内置 Skill 定义在：
[skills/daily-x-signal/SKILL.md](skills/daily-x-signal/SKILL.md)
