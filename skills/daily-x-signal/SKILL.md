---
name: daily-x-signal
version: "2026.03.22.3"
description: 从关注的 X 账号中生成高信号中文日报，并优先通过 CLI setup 完成引导式配置，再生成过去 24 小时或调度窗口内的日报。用户提到 X/Twitter 日报、过去 24 小时动态、重点帖子排序、今日必读、飞书卡片、帖子追踪表、首次配置日报机器人时，都应使用这个 skill，尤其是在 OpenClaw 远端环境中。
metadata:
  trigger-hint: 当用户想配置或生成 X 日报、检查 daily-x-signal 是否可运行、补齐飞书和 xreach 环境、查看过去 24 小时值得看的帖子时使用。
  openclaw:
    emoji: "📰"
user-invocable: true
---

# Daily X Signal

基于 `xreach`、本地粗排和可选 LLM 摘要能力，生成中文 X 高信号日报，并把结果发到飞书卡片与帖子追踪表。

这个 skill 在 OpenClaw 里应该优先做两件事：

1. 判断当前环境是否已经可运行。
2. 如果还不能运行，明确告诉用户缺什么，并优先引导走 `daily-x-signal setup`。

不要在依赖缺失时硬生成结果，更不要编造日报内容。

## 适用场景

- 每日晨报
- “帮我总结过去 24 小时 X 上值得看的内容”
- 第一次在 OpenClaw / 远端 Mac 上配置 X 日报
- 检查 `daily-x-signal` 为什么还不能生成日报
- 生成飞书卡片或写入帖子追踪表

## 工作目录

在 OpenClaw 远端环境里，默认把仓库放在：

```bash
~/.openclaw/workspace/daily-x-signal
```

执行任何命令前，先确认这个目录是否存在。若不存在，先报告仓库缺失；只有在用户明确同意初始化时，才执行 bootstrap。

## 先做代理自检

在 OpenClaw 远端环境里，如果 shell 里预设了这类本地代理变量：

```bash
http_proxy=http://127.0.0.1:8888
https_proxy=http://127.0.0.1:8888
all_proxy=socks5://127.0.0.1:8889
```

先检查对应端口是否真的有进程在监听。若没有，不要继续带着这些变量执行 `brew`、`pip`、`xreach`、`setup` 或 `generate`，而是先清理：

```bash
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
```

只有在本机代理进程真的可用时，才保留这些变量。

## 必须遵守的执行顺序

每次处理这个 skill，都按下面顺序走：

1. 检查仓库目录是否存在。
2. 检查本地代理变量是否失效；若失效，先 `unset`。
3. 检查依赖是否存在：`python3.11`、`xreach`、`git`、`daily-x-signal`。
4. 检查 `xreach auth check` 是否通过。
5. 检查 `config/local.yaml` 是否存在。
6. 如果是首次配置，优先运行 `daily-x-signal setup`，不要先让用户手改 YAML。
7. 只有在前置条件满足后，才运行 `generate`。

如果第 1-4 步任一步失败，先返回缺失清单，再决定是否继续 bootstrap。

## 缺失清单输出格式

当前环境不可运行时，优先用这种结构回复：

- `Repo`: present / missing
- `Python`: present / missing
- `xreach`: present / missing
- `xreach auth`: ok / failed
- `Proxy env`: clean / cleared / active
- `config/local.yaml`: present / missing
- `Next step`: 最小下一步动作

回复要直接、具体，不要泛泛地说“环境有问题”。

## 首次配置

如果仓库和依赖已齐，但还没有本地私有配置，优先执行：

```bash
cd ~/.openclaw/workspace/daily-x-signal
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi
daily-x-signal setup
```

如果用户希望把结果写入自定义文件，也可以用：

```bash
cd ~/.openclaw/workspace/daily-x-signal
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi
daily-x-signal setup --override-config config/local.yaml
```

引导重点：

- 先确认 following 是否读全
- 如果远端直连 X 失败，但有可用代理，优先把代理地址写入 `x.proxy_url`（例如 `http://127.0.0.1:7890` 或 `http://127.0.0.1:8888`）
- 再根据 following 和历史命中总结“推荐方向 + 代表关键词”
- 最后用一轮表单同时确认兴趣主题、补充关键词、屏蔽词、运行偏好、飞书卡片和飞书多维表格

不要把“先手写 `config/local.yaml`”当成默认路径，除非用户明确要求。

## OpenClaw 交互要求

在 OpenClaw 里不要把 `setup` 的每一个 CLI 问题机械地逐条转发给用户，尤其不要直接回显已有的 `viewer_handle` 或 `viewer_user_id` 原值。

正确做法是：

1. 整体上尽量控制在两轮内完成：
   - 第一轮：默认只收集 handle；只有 following 同步失败时，才补一张 user id / proxy 的高级访问表单
   - 第二轮：老用户只确认推荐兴趣方向；新用户才补少量选择项
2. 如果检测到已有 X 账号配置，只说“检测到已有 X 账号配置，是否沿用”，不要回显原值。
3. 如果需要用户填写账号，明确解释：
   - `X handle` 就是 `x.com/<handle>` 里的那段，例如 `https://x.com/sama` 对应 `sama`
   - 如果不确定，可以直接发主页链接
   - `X user id` 和 `proxy_url` 默认不要主动追问，只有同步 following 失败时再补
4. 在 following 同步完成后，先用自然语言总结：
   - 当前推断出的 2 到 4 个关注方向
   - 每个方向为什么被推荐
   - 默认会直接写入的关键词
5. 如果是老用户，优先走“快速更新”：
   - 沿用已有 handle、登录态、following 确认、输出配置和运行偏好
   - 只让用户确认推荐的兴趣方向
   - 如有需要，再补少量关键词
6. 如果是新用户，再用少量选择项收完配置，不要给长表单：

```text
topics=1,2,4
extra_keywords=paper, benchmark, product launch
style=balanced
output=card_and_table
```

如果用户没有额外修改项，允许直接回复“用默认推荐”。

不要说“看看 setup 下一步是否允许补关键词”这类不确定表述；当前流程支持直接补充关键词，并会直接写入配置。
像 `digest_top_n`、`include_replies`、`enable_bitable` 这类运行偏好，不要逐项追问。新用户用 `style` 和 `output` 两个选择覆盖即可；老用户默认沿用旧设置。
用户确认写入配置后，应该直接自动执行一次 `daily-x-signal generate --window-mode rolling_24h --override-config config/local.yaml`，让用户马上确认第一版日报是否符合预期。

## Skill 更新提示

如果仓库里的 `skills/daily-x-signal/SKILL.md` 比 `~/.openclaw/skills/daily-x-signal/SKILL.md` 更新，应该明确提示用户先同步 skill，再继续使用。

- 可以直接同步：
  - `cp /path/to/repo/skills/daily-x-signal/SKILL.md ~/.openclaw/skills/daily-x-signal/SKILL.md`
- 同步后建议新开一个 OpenClaw 会话，再继续配置或生成日报。

## 生成日报

环境已就绪后，常用命令是：

```bash
cd ~/.openclaw/workspace/daily-x-signal
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi
daily-x-signal generate --window-mode rolling_24h --override-config config/local.yaml
```

如需调度窗口日报：

```bash
cd ~/.openclaw/workspace/daily-x-signal
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi
daily-x-signal generate --override-config config/local.yaml
```

如需只检查 following：

```bash
cd ~/.openclaw/workspace/daily-x-signal
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi
daily-x-signal sync-authors --override-config config/local.yaml
```

## 结果读取

优先告诉用户去飞书里看最终内容，而不是在 CLI 里展开长结果。

主要输出：

- 飞书卡片：最终阅读入口
- 飞书多维表格：`帖子追踪`，1 个帖子 1 行
- 本地 Markdown：`output/daily-brief-YYYY-MM-DD.md`
- 本地 JSON：`output/daily-brief-YYYY-MM-DD.json`

CLI 里只需要简要说明：

- 是否生成成功
- 飞书卡片是否发送成功
- 帖子追踪表是否 upsert 成功
- 需要的话给出打开表格的链接

## OpenClaw 远端 Bootstrap

只有在用户明确表示“开始部署 / 初始化 / 安装”时，才执行下面的初始化命令：

```bash
mkdir -p ~/.openclaw/workspace
cd ~/.openclaw/workspace
git clone https://github.com/wangyaya-703/daily-x-signal.git
cd daily-x-signal
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

完成后立刻检查：

```bash
cd ~/.openclaw/workspace/daily-x-signal
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
source .venv/bin/activate
daily-x-signal --help
xreach auth check
```

如果 `xreach auth check` 失败，不要继续生成日报，先明确告诉用户必须先完成 X 登录态。

如果 `xreach auth check` 正常、但 `xreach user` / `following` / `tweets` 仍然失败，优先判断是否需要通过 `--proxy` 出海，并把可用代理地址落到 `x.proxy_url`，而不是反复重试直连。

## 关于 X 登录态 / Cookies

安装这个 skill 并不会自动获取 X/Twitter 的第三方登录 cookies，也不会自动从浏览器继承登录态。

你必须先在本机单独完成 `xreach` 认证，例如：

```bash
xreach auth extract --browser chrome
```

或：

```bash
xreach auth set --auth-token '你的_auth_token' --ct0 '你的_ct0'
```

只有在 `xreach auth check` 正常后，这个 skill 才能稳定读取：

- 关注列表
- Home timeline
- 账号 tweets
- thread 内容

## 行为约束

- 如果用户明确要求使用 `daily-x-signal`，优先处理这个任务，不要被无关的 HEARTBEAT、其他日程或历史任务带偏。
- 如果日报暂时生成不了，直接解释缺失项，不要输出猜测性的摘要。
- 除非用户明确要求自动部署，否则先检查、再汇报、再执行。
- 推荐使用本地粗排结果，不需要在这个 skill 里额外引入新的 rerank 流程。

## 最小成功标准

一次成功的交付至少应满足：

1. `daily-x-signal setup` 已跑通，或已有有效 `config/local.yaml`
2. `xreach auth check` 通过
3. `daily-x-signal generate --window-mode rolling_24h --override-config config/local.yaml` 成功
4. 飞书卡片成功发送，或至少本地输出成功并明确告知飞书缺失项
5. 帖子追踪表成功写入或明确说明 why not
