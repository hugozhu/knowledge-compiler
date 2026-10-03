# knowledge-compiler

> **Personal Knowledge Compiler** — 把非结构化信息持续编译成可检索、可追溯、可执行的 Personal Context，为人和 AI Agent 服务。

运行于 [Arduino VENTUNO Q](https://www.arduino.cc/product-ventuno-q)（Qualcomm IQ8275 · Hexagon NPU 40 TOPS）本地节点，原则是 **Local First, Cloud When Needed**：确定性工作交给 CPU，语义抽取交给板上 4B 模型，复杂推理再上云。

**状态：✅ V1.0 已完成端到端验收（2026-10-03）——Context Builder / Memory 层 / Web API / 反馈闭环全部落地，四个版本（V0.1→V1.0）完结。开发记录见 Issues #1–#4。**

---

## 这是什么

不是"存资料的 Wiki"，而是一台编译器。核心循环：

```text
非结构化输入（md / pdf / html / 图片 / …）
   ↓ Inbox（低摩擦入口）
   ↓ Parse（确定性工具，CPU first）
   ↓ Chunk
   ↓ LLM 语义抽取（Knowledge IR：summary / entities / claims / relations）
   ↓ SQLite + FTS5（增量编译，Raw 永不变）
   ↓ Search / Ask（Claim 优先、来源可追溯的 Context）
```

与普通笔记系统的本质区别：管理的是 **"我知道什么"（Claim）**，不只是 "我读过什么（Chunk）"。

## 核心原则

| # | 原则 | 一句话 |
| --- | --- | --- |
| 1 | Inbox first | 分类交给编译器，人只决定"值不值得进来" |
| 2 | Raw immutable | 原始资料永远保留，AI 理解错了可以重编译 |
| 3 | CPU first, LLM second | 确定性任务不浪费模型 |
| 4 | Claim over Chunk | 知识 = 事实/观点/假设，带类型与置信度 |
| 5 | Source always attached | 任何 AI 产物都能追溯到原文 |
| 6 | Knowledge ≠ Memory | 稳定事实与个人动态状态分开 |
| 7 | Incremental compilation | 按 sha256 去重，只编译增量 |
| 8 | Hybrid retrieval | FTS + Vector + Entity（V0.1 先做 FTS） |
| 9 | Don't overbuild the graph | 先 SQLite，需求出现再上图数据库 |
| 10 | KB produces Context | 终点不是搜索，是持续生产高质量 Context |

## 计划中的用法

```bash
./kc init                                        # 建 ~/knowledge 目录与 index.db
./kc add article.pdf                             # 放进 inbox（低摩擦）
./kc compile                                     # inbox → parse → chunk → LLM 抽取 → SQLite/FTS5（+判重/向量/实体页）
./kc embed                                       # 增量回填向量（编译后自动执行）
./kc search "FTS5 中文检索"                       # hybrid：FTS+LIKE+向量+实体 四路 RRF（--mode fts 对照）
./kc search "..." --rerank                       # 可选：LLM 对候选重排
./kc ask "这份文档认为个人知识库的核心是什么？"     # 带来源引用的问答
./kc entities --suggest                          # LLM 实体归并建议 → entities --merge <A> <B>
./kc dedup --dry-run                             # claim 判重候选预览（四态：重复/更新/矛盾/全新）
./kc audit [--apply]                             # 存量矛盾巡检（--apply 落演化链）
./kc evolution                                   # 知识演化链视图（观点如何变化）
./kc graph [实体名]                               # 知识图谱概览 / 实体邻域
./kc digest [--weekly]                           # 日报/周报（LLM 综合，写入 daily/）
./kc remove <doc_id>                             # 删除文档（演化恢复，raw 默认保留）
./kc note "刚发现 X 的用法值得记下来"              # 随手记 → inbox → 编译回流（反馈闭环）
./kc memory add "正在做 Y" --kind project --expires 7   # 个人记忆（动态，不进知识）
./kc context "分析 Z 的产品机会"                   # Context Builder：任务 → 上下文包（--json/--out）
./kc serve --port 8300                           # Web API：/health /stats /search /ask /context /memory
./kc watch                                       # 轮询 inbox 自动编译
```

配置（环境变量）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KC_HOME` | `~/knowledge` | 数据目录（inbox/raw/documents/…/index.db） |
| `KC_LLM_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI 兼容端点（[qwen-server](../qwen-server)） |
| `KC_LLM_API_KEY` | `sk-local` | 端点鉴权 |
| `KC_LLM_MODEL` / `KC_LLM_VLM_MODEL` | `qwen3-4b` / `qwen3-vl-4b` | 文本抽取 / OCR 模型 |

## 接入 OpenCode（MCP）

知识库已作为 MCP 服务器接入 OpenCode，任何会话可直接调用（`opencode mcp add kc --global -- ~/Projects/knowledge-compiler/kc mcp` 已配置）：

```text
tools.kc.context(task, max_chars)   # 任务 → 上下文包（研究/执行前的推荐入口）
tools.kc.search(query, limit, scope) # 秒回检索（FTS+LIKE+向量+实体，无 LLM）
tools.kc.ask(question)              # 本地模型问答（慢，~1-2 分钟，带来源）
tools.kc.stats() / memory_list() / memory_add()
```

依赖：`kc serve` 需在后台运行（MCP 是它的 stdio 代理）：

```bash
cd ~/Projects/knowledge-compiler && nohup ./kc serve --port 8300 >/dev/null 2>&1 &
```

## 测试

```bash
./kc test          # 全套自动化测试（unittest + FakeLLM 离线替身，约 17s，不依赖 NPU 服务）
./kc test -v       # 逐用例输出
```

**110 个用例，18 个文件**，覆盖：

| 层 | 内容 |
| --- | --- |
| 单元 | 向量（cosine/topk）、分块（尺寸/覆盖/病态输入）、JSON 鲁棒解析、IR 校验与合并、norm 规范化 |
| 集成 | Schema 迁移（v0→最新零丢失）、向量增量回填与孤儿清理、FTS/LIKE/实体跳转/hybrid 检索、ask 引用、rerank 重排、实体归并与别名、判重四态、audit 候选与落库、remove 级联/演化恢复/re-add、graph/evolution、backlinks 幂等、digest 周期收集、Memory 生命周期与 ask 注入、Context Pack 结构与预算（多文档比例性）、Web API 全端点（含 401/404/400）、MCP stdio 协议（握手/工具清单/全部工具/错误恢复/serve 宕机友好提示） |
| 端到端 | V0.1–V1.0 手工验收的自动化复刻：compile → 增量 → force 重编译 → embed → search → ask → 判重 → backlinks → digest → remove → re-add；图片 OCR 管线；`--no-llm` 确定性路径；note 反馈闭环 |

所有 LLM 触点用 `FakeLLM` 替身（按 system prompt 路由的确定性响应），测试可随时离线重复执行。

## 代码布局（规划）

```text
knowledge-compiler/
├── kc                # 启动器（PYTHONPATH=src）
├── src/kc/           # config / db / parsers / chunker / llm / ir / compiler / retrieve / cli
├── docs/             # 设计提案与实施计划（本仓库当前内容）
└── AGENTS.md         # Agent 协作约定
```

数据与代码分离：`~/knowledge` 不在本仓库内。

## Roadmap

| 版本 | 内容 |
| --- | --- |
| 版本 | 内容 |
| --- | --- |
| V0.1 | inbox → parse → chunk → Qwen 抽取 → SQLite+FTS5 → search/ask |
| V0.2 | Embedding 基建（ngram/OpenAI 可插拔）、Hybrid Search（RRF + entity-hop）、Entity Resolution（别名归并）、Claim 判重（四态 + 演化链）、溯源引用 |
| V0.3 | 知识图谱视图、矛盾巡检（`kc audit`）、知识演化（`kc evolution`）、自动 backlinks（`entities/<type>/`）、日报/周报（`kc digest`）、`kc remove` |
| **V1.0**（当前） | Personal Knowledge Agent：Context Builder（`kc context` 任务上下文包）、Memory 层（`kc memory`，动态可过期）、Web API（`kc serve`）、反馈闭环（`kc note`） |

## 文档

- [`docs/personal-knowledge-base-best-practices.md`](docs/personal-knowledge-base-best-practices.md) — 总体提案（设计权威）
- [`docs/v0.1-plan.md`](docs/v0.1-plan.md) — V0.1 范围、Schema、验收标准
- [`AGENTS.md`](AGENTS.md) — 本机硬约束与协作约定

## 相关项目

- [`qwen-server`](../qwen-server) — 把 NPU 上的 Qwen3 封装为 OpenAI 兼容服务（本项目的 LLM 后端）
