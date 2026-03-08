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

## 依赖与认证

运行这个项目，至少需要下面这些依赖：

- `python3.11`
- `xreach` CLI
- `requests`、`PyYAML`（安装项目时会自动带上）

### 关于 X / Twitter 登录态

仅仅安装这个 Skill 或仓库，并不会自动获得你的 X 登录态，也不会自动帮你拿到浏览器 cookies。

你必须额外完成一次 `xreach` 认证，常见方式有：

```bash
xreach auth extract --browser chrome
```

或者手动设置：

```bash
xreach auth set --auth-token '你的_auth_token' --ct0 '你的_ct0'
```

如果没有可用的 X 登录态：

- `home timeline`
- `following`
- `tweets`

这些依赖登录态的能力都会失败，日报质量也会明显下降。

### 关于飞书

如果你要启用飞书推送，还需要额外准备：

- 飞书 App 的 `app_id`
- 飞书 App 的 `app_secret`
- 一个有效的接收目标，例如 `email` / `open_id` / `chat_id`

项目本身不会替你创建飞书机器人，也不会自动推断你的接收目标。

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

## 验证方式

### 1. 验证 X 认证

```bash
xreach auth check
daily-x-signal sync-authors --override-config config/local.yaml
```

### 2. 验证 LLM

```bash
daily-x-signal generate --window-mode rolling_24h --top-n 10 --override-config config/local.yaml
```

生成后检查：

- `output/daily-brief-YYYY-MM-DD.md`
- `output/daily-brief-YYYY-MM-DD.json`

如果 JSON 里是 `llm_enabled: true`，说明模型摘要已生效。

### 3. 验证飞书

生成后检查：

- `output/feishu-preview/daily-brief-YYYY-MM-DD.json`

如果启用了真实推送，再确认飞书里是否收到卡片。

## 常用命令

生成固定窗口日报：

```bash
daily-x-signal generate --override-config config/local.yaml
```

调度补偿检查：

```bash
daily-x-signal schedule-tick --override-config config/local.yaml
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

## launchd 生产调度

推荐使用 macOS 的 `launchd` 作为生产调度器，而不是手工挂一个终端窗口。当前项目采用的是“轮询补偿 + 防重复发送”的模式：

- `launchd` 每 `15` 分钟触发一次
- 真正是否发送，由 `schedule-tick` 判断
- 每天 `08:30` 之后进入可发送窗口
- 如果 `08:30` 因为睡眠、断网或临时错误错过，会在 `11:30` 前自动补发
- 如果当天已经成功发送过，会自动跳过，避免重复推送

安装方式：

```bash
bash scripts/install_launchd.sh
```

如果你的飞书接收目标、飞书 App 凭证或模型密钥仍然是通过环境变量提供，推荐把这些变量写进项目根目录的私有 `.env.local`，而不是依赖日常 shell 配置。`launchd` 会优先读取这个文件。

安装完成后，可用下面命令检查：

```bash
launchctl print gui/$(id -u)/com.wangyaya.daily-x-signal
cat state/scheduler_state.json
tail -n 50 state/logs/launchd.stdout.log
tail -n 50 state/logs/launchd.stderr.log
```

如果你只想手动触发一次调度逻辑，而不是直接生成日报，可执行：

```bash
bash scripts/scheduler_tick.sh
```

## GitHub Actions 兜底补发

仓库内置了一个 GitHub Actions 工作流：

- 文件位置：`.github/workflows/daily-fallback.yml`
- 触发时间：每天北京时间 `11:35`
- 作用：只在本地主链路当天没有成功发送时，才执行一次云端补发

它的工作机制是：

- 本地 `launchd` 成功发送后，会自动回写两个 GitHub Actions 仓库变量
  - `LAST_DAILY_X_SIGNAL_SUCCESS_DATE`
  - `LAST_DAILY_X_SIGNAL_SUCCESS_AT`
- GitHub Actions 到 `11:35` 先检查这两个变量
- 如果今天已经成功发送，直接跳过
- 如果今天还没有成功标记，再用云端 Secrets 补发一次

### 需要配置的 GitHub Secrets

- `X_AUTH_TOKEN`
- `X_CT0`
- `DAILY_X_SIGNAL_API_KEY`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `DAILY_X_SIGNAL_FEISHU_RECEIVE_ID`

### 需要配置的 GitHub Variables

- `DAILY_X_SIGNAL_BASE_URL`

说明：

- `GitHub Actions` 只建议做兜底，不建议替代本地 `launchd`
- 如果你不希望把 X 登录态同步到 GitHub Secrets，可以只保留本地主链路，不启用云端补发
- 本地主链路回写的是两个“是否成功发送”的仓库变量，不包含你的敏感内容
  - `LAST_DAILY_X_SIGNAL_SUCCESS_DATE`
  - `LAST_DAILY_X_SIGNAL_SUCCESS_AT`
- 如果 GitHub Actions 缺少必要 Secrets，它会安全跳过，不会报错失败

## 输出文件

- Markdown 日报：`output/daily-brief-YYYY-MM-DD.md`
- JSON 日报：`output/daily-brief-YYYY-MM-DD.json`
- 飞书卡片预览：`output/feishu-preview/daily-brief-YYYY-MM-DD.json`
- 关注缓存：`state/following_cache.json`
- 历史统计：`state/history.json`
- 重点作者池：`state/core_authors.json`
- 调度状态：`state/scheduler_state.json`
- 调度日志：`state/logs/`

## 公开仓安全说明

- 公开仓只保留示例配置，不保留真实密钥
- `config/local.yaml` 不会进入 Git
- `.env.local` 不会进入 Git
- 账号 ID、绝对本机路径、私有接收目标都不应该写进可提交文件
- 安装 Skill 本身不会自动获取 X cookies，认证必须额外单独完成

## Skill

内置 Skill 定义在：
[skills/daily-x-signal/SKILL.md](skills/daily-x-signal/SKILL.md)
