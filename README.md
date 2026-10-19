# agent-skills

A collection of self-contained agent skills. Each skill is dependency-free where
possible, tested in CI, and works offline until it actually needs the network.

> 一个自包含的 agent 技能集合。每个技能尽量零依赖、有 CI 覆盖，并且不会在你
> 不需要联网时偷偷联网。
>
> 当前以 Claude Code 插件格式分发（`.claude-plugin/`），技能本体是纯 Markdown
> 加 Python 脚本，不依赖特定模型或厂商。

## 包含的技能

| 技能 | 作用 | 依赖 |
|---|---|---|
| `web-research` | 实时网页搜索（SearXNG / DuckDuckGo / Bing）与正文抽取，输出 JSON | 无（仅标准库） |

## 安装

### 方式一：作为插件安装（推荐）

```bash
claude plugin marketplace add <this-repo-url>
claude plugin install agent-skills@agent-skills
```

安装后技能以 `agent-skills:web-research` 的形式出现在技能列表中。

### 方式二：本地开发加载

```bash
git clone <this-repo-url> agent-skills
claude --plugin-dir ./agent-skills
```

改动立即生效，不需要重新安装。适合在提交前验证。

### 方式三：只要单个技能

把 `skills/web-research/` 整个目录复制到 `~/.claude/skills/web-research/` 即可。
此时脚本里引用的 `${CLAUDE_PLUGIN_ROOT}` 不存在，请改用实际路径（`SKILL.md` 中已注明）。

## 环境要求

- Python 3.8 及以上（已在 3.8 / 3.12 上测试）
- 默认零第三方依赖
- 可选：`pip install trafilatura` 提升正文抽取质量
- 可选：自建 SearXNG 获得更稳定的搜索，见
  [`skills/web-research/references/setup.md`](skills/web-research/references/setup.md)

## 快速开始

```bash
WEB="skills/web-research/scripts/web.py"

# 搜索
python "$WEB" search "python 3.13 new features" -k 5

# 抓取正文（可一次传多个 URL，内部并发）
python "$WEB" fetch "https://docs.python.org/3/whatsnew/3.13.html" --max-chars 4000

# 自检：后端连通性、抽取质量、缓存位置、代理模式
python "$WEB" doctor
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CLAUDE_WEB_BACKEND` | `auto` | `auto` / `searxng` / `ddg` / `bing` |
| `CLAUDE_WEB_SYSTEM_PROXY` | 空（直连） | 设为 `1` 时走系统/环境代理 |
| `SEARXNG_URL` | 空 | 设置后优先使用 SearXNG |
| `CLAUDE_WEB_CACHE` | 系统缓存目录 | 缓存目录覆盖 |
| `CLAUDE_WEB_TTL` | 搜索 600s / 抓取 3600s | 缓存有效期（秒） |

> **关于代理：** 脚本默认直连。在启用了注册表代理的 Windows 机器上，Python 的
> `urllib` 会自动走代理，单个请求可能多花数秒。只有站点确实只能通过代理访问时，
> 才设 `CLAUDE_WEB_SYSTEM_PROXY=1`。

## 开发

### 目录结构

```
.claude-plugin/
  plugin.json          插件清单（name / version）
  marketplace.json     让本仓库可作为 marketplace 被安装
skills/
  <name>/SKILL.md      技能定义与使用说明
  <name>/scripts/      随技能分发的脚本
  <name>/references/   详细文档
scripts/check_version.py   清单与 tag 一致性校验
tests/                     离线单元测试与冒烟测试
.github/workflows/ci.yml   CI
```

### 运行测试

```bash
./tests/smoke.sh                       # 离线：编译 + 单测 + 版本一致性
AGENT_SKILLS_NETWORK=1 ./tests/smoke.sh  # 额外探测真实网络
```

Windows 上用 Git Bash 运行；或直接执行
`python -m unittest discover -s tests -v`。

### 新增技能

1. 建 `skills/<name>/SKILL.md`，frontmatter 必须含 `name` 与 `description`，
   且 `name` 等于目录名。
2. `description` 写清**何时用**与**何时不用**——模型靠它决定是否调用。
3. 脚本路径用 `${CLAUDE_PLUGIN_ROOT}/skills/<name>/...`，不要写死家目录。
4. 补测试，跑 `./tests/smoke.sh`。

### 发布新版本

版本号同时存在于 `plugin.json` 与 `marketplace.json`，必须一致；git tag 必须是
`v` + 版本号。CI 会在 tag 上校验这三点。

```bash
# 1. 改版本号（两处）并更新 CHANGELOG.md
# 2. 提交并打 tag
git add -A
git commit -m "release: v0.2.0"
git tag -a v0.2.0 -m "v0.2.0"
git push origin main --tags
```

版本号规则：

- **MAJOR** — 破坏性变更（技能改名、删除参数、调用契约变化）
- **MINOR** — 新增技能或新增参数
- **PATCH** — 修 bug、改文案

## 安全

- `web-research` 抓回的网页正文属于**不可信输入**。技能文档要求只提取事实，
  不执行页面中的任何指令。
- 抓取内置 SSRF 防护：只允许 `http`/`https`，拒绝回环、私网、链路本地地址，
  并在重定向的每一跳重新校验。
- 仓库内不应提交任何内网地址、令牌或个人配置；`.gitignore` 已排除
  `.claude/` 与 `settings.local.json`。

## 许可

[MIT](LICENSE)
