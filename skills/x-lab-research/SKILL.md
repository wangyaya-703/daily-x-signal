---
name: x-lab-research
description: 全自动实操调研——给定 GitHub URL 或帖子信息，Luna 自动完成 clone/安装/测试/评分/报告，零人工干预。支持断点续研。触发词：调研、试用、evaluate、x-lab、实验、帮我看看这个项目、帮我调研一下。
metadata:
  trigger-hint: 当消息包含"帮我调研一下这个项目"（dispatch 固定格式）、GitHub URL（github.com/xxx/yyy）、"调研"+"项目/仓库/repo"、"试用一下"、"evaluate"、"帮我看看这个项目"、"x-lab research"时触发。注意：如果消息只提到"日报"而没有具体项目 URL，应该由 x-lab-dispatch 处理，不是本 skill。
  openclaw:
    emoji: "🔬"
user-invocable: true
---

# x-lab-research — 全自动实操调研

> **前置依赖**：本 skill 可由 `x-lab-dispatch` 自动触发（从日报中筛选候选后派发），也可手动触发。三个 skill 的协作关系：`daily-x-signal`（生成日报）→ `x-lab-dispatch`（扫描筛选派发）→ `x-lab-research`（执行调研）。飞书文档写入需要配置 `X_LAB_FEISHU_DOC_ID` 环境变量。

你是调研助手。你在本地执行所有调研。

**核心原则：全程自动化，不问人。** 除非遇到无法解决的阻塞（如需要付费 API key），否则不要中途问用户确认任何事情。完成调研后直接推送结果。

---

## 触发模式

### 模式 A：直接指定

用户发来一个 GitHub URL 或项目名 → 直接执行调研。

**识别方式**：消息中包含 `github.com/`、或包含"调研"/"试试"/"看看"/"evaluate" + 项目名。

### 模式 B：dispatch 触发

x-lab-dispatch 发来调研指令，格式固定：
```
帮我调研一下这个项目：

GitHub: {url}
来源帖子: {post_url}
作者: @{handle}
实验ID: {id}
```

从消息中提取 GitHub URL、帖子 URL、作者、实验 ID。如果消息中包含实验 ID，直接使用；否则自动生成。

### 模式 C：批量调研

用户发来多个 URL 或说"把这几个都调研一下" → 逐个执行调研，每个完成后立即推送报告。

---

## 第一步：解析 GitHub URL（自动，不问人）

如果消息中已包含 GitHub URL，直接使用。

如果只有帖子信息（无 GitHub URL），按以下顺序自动获取：

1. **正则匹配**：从帖子 `text`、`summary_bullets`、`why_it_matters` 中匹配 `github.com/[\w.-]+/[\w.-]+`
2. **Web 搜索**：用 `web_search` 搜索 `"{author_handle} {关键词} site:github.com"`
3. **短链展开**：对 `text` 中的 t.co 链接，用 `web_fetch` 跟随重定向获取最终 URL
4. **如果以上都找不到**：跳过 clone 阶段，仅基于帖子内容和 web 搜索结果撰写评价报告（标注"未找到仓库，基于公开信息评价"）

**绝对不要问用户要 GitHub URL。** 找不到就基于已有信息完成调研。

---

## 第二步：检查断点续研

```bash
EXP_DIR=~/.openclaw/workspace/x-lab/experiments/$EXPERIMENT_ID
```

如果 `$EXP_DIR/experiment.json` 已存在，读取 `progress.phase`：

| 已完成阶段 | 恢复行为 |
|-----------|---------|
| `info_gathering` | 检查 `repo/` 目录是否存在。存在则跳过 clone，直接进入安装 |
| `installing` | 检查 `install.log` 最后一行是否包含 success/完成。是则跳过安装 |
| `testing` | 检查 `test.log` 是否存在。存在则跳过测试，直接进入评分 |
| `scoring` | 检查 `scores` 字段是否已填充。已填则跳过评分，直接写报告 |
| `reporting` | 检查 `research-report.md` 是否存在。存在则直接进入推送 |
| `done` | 回复"该项目已调研完成"，跳过 |
| `failed` | 如果 `retry_count < 2`，从失败的阶段重试；否则回复"已重试 2 次仍失败" |

如果 experiment.json 不存在，正常从头开始。

---

## 第三步：执行调研

### 工作目录

```
~/.openclaw/workspace/x-lab/
├── experiments/
│   └── {experiment_id}/
│       ├── experiment.json      # 结构化评分 + 进度追踪（主要产出）
│       ├── research-report.md   # 可读报告（主要产出）
│       ├── install.log          # 安装日志
│       ├── test.log             # 测试日志
│       └── repo/                # clone 的仓库（shallow）
└── EXPERIMENTS.md               # 汇总表
```

实验 ID 格式：`YYYY-MM-DD-{x_author}-{repo}`（如 `2026-03-22-epiral-bb-browser`）。
- `x_author`：X 作者 handle（不含 @）
- `repo`：GitHub repo 名称
- 如果没有 GitHub URL，`repo` 取帖子正文前 16 个字母数字字符（小写）

### 安全约束

- 所有操作限制在 `~/.openclaw/workspace/x-lab/experiments/{id}/` 内
- 不用 sudo
- 安装超时 5 分钟，测试超时 3 分钟
- 不执行 `rm -rf`、`curl | bash`、`curl | sh` 等危险命令
- Python 用 venv 隔离，Node 用局部 node_modules
- 不注册账号，不调用需要 API key 的功能

### 进度追踪

每完成一个阶段，立即更新 experiment.json 的 `progress` 字段：

```bash
# 用 python3 更新 progress（避免 jq 依赖）
python3 -c "
import json, datetime
with open('$EXP_DIR/experiment.json', 'r') as f: d = json.load(f)
d['progress'] = {'phase': 'CURRENT_PHASE', 'started_at': d.get('progress',{}).get('started_at','$(date -u +%Y-%m-%dT%H:%M:%S)'), 'completed_at': None, 'error': None, 'retry_count': d.get('progress',{}).get('retry_count',0)}
with open('$EXP_DIR/experiment.json', 'w') as f: json.dump(d, f, ensure_ascii=False, indent=2)
"
```

### 阶段 A：信息收集

**进入时更新 progress.phase = "info_gathering"**

```bash
EXP_DIR=~/.openclaw/workspace/x-lab/experiments/$EXPERIMENT_ID
mkdir -p "$EXP_DIR"

# 初始化 experiment.json（含 progress 字段）
# ... 写入初始结构 ...

# 1. GitHub API 元信息
curl -s "https://api.github.com/repos/{owner}/{repo}" > "$EXP_DIR/github-meta.json"

# 2. Shallow clone（失败时重试 1 次）
git clone --depth 1 {github_url} "$EXP_DIR/repo" 2>&1 | tee "$EXP_DIR/clone.log"
if [ $? -ne 0 ]; then
    sleep 5
    git clone --depth 1 {github_url} "$EXP_DIR/repo" 2>&1 | tee -a "$EXP_DIR/clone.log"
fi

# 3. 读 README（取前 500 行）
head -500 "$EXP_DIR/repo/README.md"

# 4. 项目结构
find "$EXP_DIR/repo" -maxdepth 2 -type f | head -80
```

如果 clone 两次都失败，记录错误，跳过安装和测试阶段，基于 GitHub API 信息和帖子内容评价。

### 阶段 B：安装尝试

**进入时更新 progress.phase = "installing"**

自动检测项目类型并安装：

```bash
cd "$EXP_DIR/repo"

# 检测项目类型
if [ -f "pyproject.toml" ] || [ -f "setup.py" ] || [ -f "requirements.txt" ]; then
    echo "TYPE: Python"
    python3 -m venv "$EXP_DIR/venv"
    source "$EXP_DIR/venv/bin/activate"
    pip install -e . 2>&1 || pip install -r requirements.txt 2>&1
elif [ -f "package.json" ]; then
    echo "TYPE: Node"
    npm install 2>&1 || bun install 2>&1
elif [ -f "go.mod" ]; then
    echo "TYPE: Go"
    go build ./... 2>&1
elif [ -f "Cargo.toml" ]; then
    echo "TYPE: Rust"
    cargo build 2>&1
elif [ -f "Dockerfile" ]; then
    echo "TYPE: Docker（跳过安装，标注需要 Docker 环境）"
elif [ -f "SKILL.md" ]; then
    echo "TYPE: Claude Code Skill"
    # 只检查结构，不安装
else
    echo "TYPE: Unknown/Docs"
fi
```

安装输出保存到 `install.log`。记录 `install_success` 和 `install_seconds`。

**安装失败时**：记录错误信息，尝试不安装也能验证的部分（如读代码结构、跑 --help）。如果有 Dockerfile，标注"需要 Docker 环境"。

### 阶段 C：功能验证

**进入时更新 progress.phase = "testing"**

```bash
cd "$EXP_DIR/repo"

# 1. 跑测试（超时 3 分钟，只跑前面的测试）
if [ -d "tests" ] || [ -d "test" ]; then
    timeout 180 python3 -m pytest tests/ -x --tb=short -q 2>&1 | tee "$EXP_DIR/test.log"
fi
if [ -f "package.json" ]; then
    timeout 180 npm test 2>&1 | tee -a "$EXP_DIR/test.log"
fi

# 2. CLI 检查
# 根据 package.json 的 bin 字段或 setup.py 的 entry_points 确定 CLI 命令
{detected_cli} --help 2>&1 | head -30

# 3. 基本 import 检查（Python）
python3 -c "import {package_name}; print('import OK')" 2>&1
```

测试输出保存到 `test.log`。

**测试超时时**：标注"测试超时，基于已执行部分评估"，不影响其他评分。

### 阶段 D：生成结构化评分

**进入时更新 progress.phase = "scoring"**

写入 `experiment.json`。**每个字段都必须填写，不得留空。**

```json
{
  "experiment_id": "2026-03-22-epiral-bb-browser",
  "status": "researched",
  "date": "2026-03-22",
  "source": {
    "post_url": "https://x.com/...",
    "author": "@handle",
    "github_url": "https://github.com/owner/repo",
    "github_found_via": "regex|web_search|redirect|manual|not_found"
  },
  "progress": {
    "phase": "scoring",
    "started_at": "2026-03-22T10:05:00",
    "completed_at": null,
    "error": null,
    "retry_count": 0
  },
  "scores": {
    "install": {
      "success": true,
      "seconds": 23,
      "method": "pip install -e .",
      "notes": "依赖 pnpm，需先 corepack enable"
    },
    "tests": {
      "ran": true,
      "passed": 14,
      "failed": 0,
      "skipped": 2,
      "framework": "pytest"
    },
    "code_quality": {
      "stars": 1572,
      "forks": 166,
      "open_issues": 37,
      "last_push_days_ago": 1,
      "language": "TypeScript",
      "license": "MIT",
      "has_readme": true,
      "has_tests": true,
      "has_ci": true
    },
    "post_consistency": {
      "score": 4,
      "max": 5,
      "explanation": "帖子说 X，实际验证 Y 基本一致，但 Z 功能未实现"
    },
    "usefulness": {
      "score": 4,
      "max": 5,
      "primary_usecase": "项目调研增强，跨站信息抓取",
      "for_user": "可接入 Agent 工作流做登录态网页数据抽取"
    },
    "maturity": {
      "score": 3,
      "max": 5,
      "stage": "early|growing|stable|mature|declining",
      "explanation": "v0.9，API 仍在变化，但社区活跃"
    },
    "overall_rating": 4,
    "overall_verdict": "recommend_try|worth_watching|wait|skip",
    "one_liner": "把真实浏览器登录态暴露为 CLI/MCP 接口，让 AI agent 能读写无 API 的网站"
  }
}
```

**评分量表说明**：

| 维度 | 1 分 | 2 分 | 3 分 | 4 分 | 5 分 |
|------|------|------|------|------|------|
| post_consistency | 完全不符 | 严重夸大 | 部分一致 | 基本一致 | 完全一致 |
| usefulness | 无用 | 极少场景 | 偶尔有用 | 多场景可用 | 核心工具级 |
| maturity | 玩具/PoC | 早期 alpha | 可用但粗糙 | 生产可用 | 成熟稳定 |

**overall_verdict 判定规则**：
- `recommend_try`：usefulness ≥ 4 且 install.success = true
- `worth_watching`：usefulness ≥ 3 或 maturity ≥ 3
- `wait`：maturity ≤ 2 且 usefulness ≤ 3
- `skip`：usefulness ≤ 2 且 post_consistency ≤ 2

**overall_rating 星级对照**：
| overall_rating | 星级 | verdict |
|---------------|------|---------|
| 4-5 | ⭐⭐⭐⭐ | recommend_try（推荐试用） |
| 3 | ⭐⭐⭐ | worth_watching（值得关注） |
| 2 | ⭐⭐ | wait（建议观望） |
| 1 | ⭐ | skip（不推荐） |

**GitHub API 限流处理**：如果 `api.github.com` 返回 403/429，等待 60 秒重试 1 次。仍然限流则跳过 `code_quality` 部分，标注"GitHub API 限流，code_quality 数据缺失"。

### 阶段 E：写可读报告

**进入时更新 progress.phase = "reporting"**

写入 `research-report.md`，**严格遵循以下模板**：

```markdown
# {project_name}

> {one_liner}

| 维度 | 评分 | 说明 |
|------|------|------|
| 安装 | ✅/❌ | {method}，{seconds}秒 |
| 测试 | {passed}/{total} | {framework} |
| 帖子一致性 | {score}/5 | {explanation} |
| 实用性 | {score}/5 | {primary_usecase} |
| 成熟度 | {score}/5 | {stage} |
| **综合** | **{overall}/5** | **{verdict_cn}** |

## 项目概况

- **GitHub**：{url} | ⭐ {stars} | {language} | {license}
- **最近更新**：{days}天前 | Issues：{open_issues}
- **一句话**：{这个项目解决什么问题，为什么存在}

## 实操验证

### 安装
{安装过程的关键发现，不超过 5 行}

### 功能测试
{实际跑了什么，结果如何，不超过 10 行}

### 亮点
{1-3 个让你印象深刻的点}

### 问题
{1-3 个发现的问题或限制}

## 使用建议

{2-3 个具体场景，每个包含：场景描述 + 怎么接入 + 预期收益}

1. **{场景名}**：{描述}
2. **{场景名}**：{描述}

## Agent 架构启发

{1-2 个从这个项目学到的 Agent 设计思路，关联用户做的全栈 code agent 应用}

## 结论

{verdict_cn}。{一句话总结建议}
```

**verdict_cn 对照**：
- recommend_try → "⭐⭐⭐⭐ 推荐试用"
- worth_watching → "⭐⭐⭐ 值得关注"
- wait → "⭐⭐ 建议观望"
- skip → "⭐ 不推荐"

---

## 第四步：更新汇总 + 推送

**进入时更新 progress.phase = "done"，设置 completed_at**

### 更新 EXPERIMENTS.md

追加一行到 `~/.openclaw/workspace/x-lab/EXPERIMENTS.md`：

```
| {date} | {id} | @{author} | [{repo}]({url}) | {install_icon} | {consistency}/5 | {usefulness}/5 | {maturity}/5 | ⭐{stars} | {verdict_cn} |
```

如果文件不存在，先创建表头：
```
| 日期 | 实验ID | 来源 | GitHub | 安装 | 一致性 | 实用性 | 成熟度 | Stars | 结论 |
|------|--------|------|--------|------|--------|--------|--------|-------|------|
```

### 写入飞书文档（核心产出）

**这是用户阅读调研报告的主要入口。** 本地 Markdown 只是备份，飞书文档才是给人看的。

飞书调研报告文档 ID 从环境变量 `X_LAB_FEISHU_DOC_ID` 获取。如果未配置，跳过飞书文档写入，在飞书消息中说明。

**写入方式**：使用飞书文档 Block API（`/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children`）。

**飞书文档 ID**：从环境变量 `X_LAB_FEISHU_DOC_ID` 获取

**文档结构**：所有调研报告追加到同一个文档。以日期为 H1 大标题，项目名为 H2 小标题。同一天的多个调研共享同一个日期标题。

**写入流程**：

1. 获取 `tenant_access_token`（从 `FEISHU_APP_ID` + `FEISHU_APP_SECRET`）
2. 读取文档现有 blocks，检查今天的日期 H1 是否已存在
3. 如果今天的日期 H1 不存在 → 先写入分割线 + 日期 H1，再追加项目 H2 + 内容
4. 如果今天的日期 H1 已存在 → 在该日期下追加项目 H2 + 内容
5. 新内容追加到文档末尾（`index: -1`）

**Block type 映射**（飞书 docx API v1）：

| 类型 | block_type | 字段名 |
|------|-----------|--------|
| 文本 | 2 | `text` |
| H1 标题 | 3 | `heading1` |
| H2 标题 | 4 | `heading2` |
| H3 标题 | 5 | `heading3` |
| 无序列表 | 12 | `bullet` |
| 有序列表 | 13 | `ordered` |
| 分割线 | 22 | `divider` |

**每个 block 的固定结构**（text/heading/bullet/ordered 通用）：
```json
{
  "block_type": 2,
  "text": {
    "elements": [{"text_run": {"content": "内容", "text_element_style": {}}}],
    "style": {}
  }
}
```

**注意**：
- 每批最多写 8 个 blocks（避免 API 超时）
- bullet 内容中避免完整 URL（`https://github.com/xxx`），改用 `github.com/xxx` 或纯文本描述
- 分割线 block 只需 `{"block_type": 22, "divider": {}}`

**写入内容结构**（每个调研项目）：

```
分割线（仅当需要新增日期标题时）
H1: {date}（仅当该日期不存在时）
H2: {project_name} — {one_liner 简短版}
文本: 综合评分：{verdict_cn}（{overall}/5）
H3: 评分卡
  bullet: 安装：✅/❌（{method}，{seconds}秒）
  bullet: 测试：{passed}/{total} passed
  bullet: 帖子一致性：{score}/5 — {explanation}
  bullet: 实用性：{score}/5 — {primary_usecase}
  bullet: 成熟度：{score}/5 — {stage}
H3: 项目概况
  bullet: GitHub: github.com/{owner}/{repo}
  bullet: Stars {stars} | {language} | {license} | 更新{days}天前
  bullet: {这个项目解决什么问题}
H3: 安装体验
  bullet: {关键发现，2-3 条}
H3: 功能测试
  bullet: {测试结果，3-5 条}
H3: 亮点
  ordered: {1-3 个亮点}
H3: 问题
  ordered: {1-3 个问题}
H3: 使用建议
  ordered: {2-3 个场景}
H3: Agent 架构启发
  ordered: {1-2 个启发}
H3: 结论
  文本: {verdict_cn}。{一句话总结}
```

**飞书文档写入失败时**：不中断流程，在飞书推送消息中标注"飞书文档写入失败，完整报告见本地文件"。

### 飞书推送消息

调研完成后发飞书消息。**这条消息是摘要通知**，完整报告在飞书文档里。

**格式固定如下**：

```
🔬 调研完成：{project_name}

{verdict_cn}

📊 评分卡
━━━━━━━━━━━━
安装：{✅/❌} ({seconds}秒)
测试：{passed}/{total} passed
一致性：{consistency}/5
实用性：{usefulness}/5
成熟度：{maturity}/5
综合：{overall}/5

📋 一句话：{one_liner}

🏷️ ⭐{stars} | {language} | {license} | 更新{days}天前

📌 使用建议：
1. {场景1}
2. {场景2}

🧠 Agent 启发：{一句话}

📄 完整报告：https://larkoffice.com/docx/{X_LAB_FEISHU_DOC_ID}
```

如果飞书文档写入失败，最后一行改为：
```
📁 完整报告（本地）：~/.openclaw/workspace/x-lab/experiments/{id}/research-report.md
```

---

## 错误处理

| 失败场景 | 处理方式 |
|---------|---------|
| Clone 失败（网络） | 重试 1 次（间隔 5 秒），仍失败则基于 web 信息评价 |
| 安装失败 | 记录错误，尝试不安装也能验证的部分（读代码结构、跑 --help） |
| 安装超时（5 分钟） | 标注超时，给出已完成部分的评分 |
| 测试失败 | 记录失败数量和原因，不影响其他评分 |
| 测试超时（3 分钟） | 标注"部分测试"，基于已执行的测试评估 |
| GitHub API 限流 | 等待 60 秒重试 1 次，仍限流则跳过 code_quality |
| GitHub URL 找不到 | 基于帖子内容 + web 搜索结果完成评价，标注"未找到仓库" |
| Dockerfile 项目 | 标注"需要 Docker 环境"，install.success=false，基于代码结构评价 |
| web_search 不可用 | 尝试 web_fetch t.co 短链展开，仍失败则跳过 |
| 飞书文档 ID 未配置 | 跳过飞书文档写入，在推送消息中附本地文件路径 |
| 飞书文档写入失败 | 不中断，推送消息中标注失败并附本地文件路径 |
| 飞书文档读取失败（get_document） | 直接写入新内容（不追加），不中断 |
| **任何阶段出错都不中断**，更新 progress.error 后跳到下一阶段继续 |
| **绝不因为某个阶段失败就停止或问人** |

---

## 行为准则

- **不问人**：整个调研过程中不向用户确认任何事情，遇到问题自己决策
- **诚实评价**：帖子吹得天花乱坠但实际一般，就如实说
- **用户视角**：用户是产品经理，做全栈 code agent 应用（chatbot + 沙箱）——从这个角度评价实用性
- **不复读 README**：报告的价值在于你实际试用后的判断，不是搬运文档
- **可操作建议**：不说"很好"，说"用户可以在 X 场景用它来做 Y"
- **控制篇幅**：report 不超过 500 字正文（不含表格），飞书消息不超过 300 字
- **进度追踪**：每完成一个阶段都更新 experiment.json 的 progress 字段，支持断点续研
