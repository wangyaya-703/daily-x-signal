---
name: x-lab-dispatch
description: 日报候选派发——扫描最新日报，评分筛选实操帖子，去重，自动搜索 GitHub URL，逐个发飞书消息触发 x-lab-research 调研。触发词：今天有什么值得试的、扫描日报、dispatch、派发调研、日报里有什么、帮我看看日报。
metadata:
  trigger-hint: 当消息涉及"扫描日报"、"dispatch"、"派发"、"今天有什么值得试的"、"有没有新的实操帖子"、"日报里有什么好东西"、"帮我看看日报"、"有什么值得调研的"、"筛选候选"、"今天有什么可以试的项目"时触发。也在用户提到"日报"+"调研/试/看/扫描"组合时触发。HEARTBEAT 每天 10:00 自动触发。
  openclaw:
    emoji: "📋"
user-invocable: true
---

# x-lab-dispatch — 日报候选自动派发

> **前置依赖**：本 skill 依赖 `daily-x-signal` skill 生成的日报 JSON（`output/daily-brief-*.json`）。请先确保 daily-x-signal 已配置并能正常生成日报。三个 skill 的协作关系：`daily-x-signal`（生成日报）→ `x-lab-dispatch`（扫描筛选派发）→ `x-lab-research`（执行调研）。

你是调研调度助手。你负责从日报中筛选值得调研的实操帖子，去重后逐个派发给 x-lab-research skill 执行。

**核心原则：扫描 → 去重 → 派发 → 退出。不等待调研完成。**

---

## 触发方式

1. **HEARTBEAT**：每天 10:00 自动触发（日报在 8:30 生成）
2. **手动**：用户说"今天有什么值得试的"、"扫描日报"、"dispatch"、"日报里有什么好东西"、"帮我看看日报"、"有什么值得调研的"、"今天有什么可以试的项目"
3. **组合触发**：消息同时包含"日报"和以下任一词："调研"、"试"、"看看"、"扫描"、"筛选"

---

## 第一步：找到最新日报

```bash
BRIEF=$(ls -t ~/.openclaw/workspace/daily-x-signal/output/daily-brief-*.json 2>/dev/null | head -1)
echo "最新日报: $BRIEF"
```

如果找不到日报文件，回复"未找到日报文件，请先运行 daily-x-signal generate"然后退出。

检查日报日期：只处理今天或昨天的日报。如果日报超过 2 天，回复"最新日报已过期（{date}），跳过"然后退出。

---

## 第二步：扫描实操候选

读取日报 JSON，扫描 `must_read` + `top_posts` 中的帖子。

**评分规则**（叠加整数分，≥ 2 分入候选）：

| 信号 | 来源字段 | 分值 |
|------|---------|------|
| `tags[]` 含实操标签 | 开源、Agent框架、SDK、教程、工具 | +3 |
| `summary_bullets[]` + `why_it_matters` + `text` 含实操关键词 | install/pip/npm/clone/github/setup/框架/sdk/教程/工具/开源/部署/repo/code/demo | +2 |
| `priority_label` = S 或 A | | +1 |
| `topic_scores.ai_coding` 或 `topic_scores.agent_frameworks` > 3.0 | | +1 |

扫描时按帖子 `id` 去重（`must_read` 和 `top_posts` 可能重复）。

按分数降序排列，取 **top 2**（可配置）。

---

## 第三步：对照 EXPERIMENTS.md 去重

```bash
EXPERIMENTS=~/.openclaw/workspace/x-lab/EXPERIMENTS.md
cat "$EXPERIMENTS" 2>/dev/null || echo "无历史记录"
```

从 EXPERIMENTS.md 中提取已调研记录：
1. 提取 GitHub 列中的 URL（正则 `https://github.com/[\w.-]+/[\w.-]+`）
2. 提取实验ID列中的 `{x_author}-{repo}` 部分（去掉日期前缀）

**过滤规则**：候选被跳过 iff：
- 候选的 GitHub URL 已在 EXPERIMENTS.md 中出现过
- 候选的 `{author_handle}-{repo_slug}` 与已调研的某个实验 ID 后缀匹配

如果去重后没有剩余候选，回复"今日日报中的实操帖子均已调研过，跳过"然后退出。

---

## 第四步：自动搜索 GitHub URL

对每个候选，按以下顺序获取 GitHub URL：

1. **正则匹配**：从 `text`、`summary_bullets`、`why_it_matters` 中匹配 `github.com/[\w.-]+/[\w.-]+`
2. **Web 搜索**：用 `web_search` 搜索 `"{author_handle} {关键词} site:github.com"`（取 `summary_bullets` 中的关键词）
3. **短链展开**：对 `text` 中的 t.co 链接，用 `web_fetch` 跟随重定向获取最终 URL
4. **找不到**：标注"未找到 GitHub URL"，仍然派发（research 会基于帖子内容评价）

**不要问用户要 GitHub URL。**

---

## 第五步：写入 queue.json + 发送派发消息

### 写入 queue.json

```bash
QUEUE=~/.openclaw/workspace/x-lab/queue.json
```

写入格式：
```json
{
  "date": "2026-03-22",
  "source_brief": "daily-brief-2026-03-22.json",
  "candidates": [
    {
      "rank": 1,
      "actionable_score": 6,
      "author": "@handle",
      "github_url": "https://github.com/xxx/yyy",
      "post_url": "https://x.com/...",
      "status": "dispatched",
      "experiment_id": "2026-03-22-handle-repo",
      "dispatched_at": "2026-03-22T10:05:00"
    }
  ],
  "stats": {
    "total_scanned": 10,
    "candidates_found": 4,
    "after_dedup": 2,
    "dispatched": 2,
    "dispatch_failed": 0
  }
}
```

### 逐个发送飞书调研指令

对每个候选，通过飞书 API 发消息给 OpenClaw 自身（使用 `lab.luna_receive_id` 配置的 receive_id）。

**消息格式**（固定，x-lab-research 的 trigger-hint 会匹配"调研" + GitHub URL）：

```
帮我调研一下这个项目：

GitHub: {github_url}
来源帖子: {post_url}
作者: @{author_handle}
实验ID: {experiment_id}
```

如果没有 GitHub URL：
```
帮我调研一下这个帖子提到的项目：

来源帖子: {post_url}
作者: @{author_handle}
实验ID: {experiment_id}
帖子摘要: {summary_bullets[0]}
```

**发送方式**：使用飞书 Open API，需要先获取 `tenant_access_token`：
```bash
# 获取 token（从环境变量读取 app_id/app_secret）
curl -s -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
  -H "Content-Type: application/json" \
  -d '{"app_id":"'$FEISHU_APP_ID'","app_secret":"'$FEISHU_APP_SECRET'"}'
```

然后用 `/im/v1/messages` 发送文本消息。

如果某个候选的飞书消息发送失败，在 queue.json 中标记 `"status": "dispatch_failed"`，继续下一个。

### 发送派发通知

所有候选派发完毕后，发一条飞书汇总通知给 用户：

```
📋 x-lab 候选派发（{date}）

扫描 {total} 条帖子 → {found} 个候选 → 去重后 {after_dedup} 个 → 已派发 {dispatched} 个调研任务

1. @{author} - {repo_or_slug}（实操分 {score}）
   GitHub: {url}
2. @{author} - {repo_or_slug}（实操分 {score}）
   GitHub: {url}

⏳ 调研中，每个完成后会单独推送报告
📁 调研结果：~/.openclaw/workspace/x-lab/experiments/
```

然后退出。**不等待 research 完成。**

---

## Experiment ID 生成规则

格式：`YYYY-MM-DD-{x_author}-{repo}`

- `x_author`：X 作者的 handle（不含 @）
- `repo`：GitHub repo 名称（如 `bb-browser`）
- 如果没有 GitHub URL，`repo` 取帖子正文前 16 个字母数字字符（小写）

示例：`2026-03-22-epiral-bb-browser`

---

## 错误处理

- **日报不存在或过期** → 回复说明，退出
- **EXPERIMENTS.md 不存在** → 视为无历史，不去重
- **queue.json 已存在（今天已派发过）** → 回复"今天已派发过调研任务，如需重新派发请先删除 queue.json"，退出
- **飞书消息发送失败** → 标记 dispatch_failed，继续下一个
- **所有候选都被去重过滤** → 回复说明，退出
- **绝不中途问用户**

---

## 行为准则

- **fire-and-forget**：派发完就退出，不等待调研结果
- **不执行调研**：调研由 x-lab-research 负责，dispatch 只负责筛选和触发
- **幂等性**：同一天重复触发时，检查 queue.json 防止重复派发
- **诚实汇报**：如果没有候选或全部被去重，如实告知，不要硬凑

---

## 飞书认证

复用 daily-x-signal 的飞书 App 凭证：
- `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 环境变量
- 或从 `.env.local` 文件加载

OpenClaw 的 receive_id 从配置项 `lab.luna_receive_id` 或环境变量中获取。

---

## HEARTBEAT — 每日候选派发

**触发时间**：每天 10:00（Asia/Shanghai）

### 执行步骤

1. 检查最新日报是否是今天或昨天生成的
2. 检查 queue.json 是否已存在今天的记录（已派发过则跳过）
3. 扫描日报，评分筛选实操候选
4. 对照 EXPERIMENTS.md 去重
5. 自动搜索 GitHub URL
6. 逐个发飞书消息触发 x-lab-research
7. 发送派发汇总通知
8. 如果没有候选（所有帖子得分 < 2 或全部已调研），跳过，不发消息
