# Personal Knowledge Base 最佳实践 （个人知识库 = Local Knowledge Compiler + Agent Context）

> **核心观点：个人知识库不是“存资料的 Wiki”，而是一个持续把非结构化信息编译成可检索、可理解、可执行 Context 的系统。**

---

## 1. 目标

一个好的个人知识库应该解决四件事：

```text
Capture
  ↓
Compile
  ↓
Retrieve
  ↓
Use
```

即：

1. **Capture**：任何信息都可以快速进入 Inbox
2. **Compile**：自动从非结构化数据中提取知识
3. **Retrieve**：能够快速找到相关事实、观点、历史上下文
4. **Use**：被人和 Agent 直接用于思考、决策和执行

最终形成：

```text
现实世界
   ↓
非结构化数据
   ↓
Knowledge Compiler
   ↓
结构化知识
   ↓
Personal Context
   ↓
Human / Agent
```

---

# 2. 不要从 Wiki 开始

传统个人知识管理通常是：

```text
笔记
 ↓
文件夹
 ↓
标签
 ↓
Wiki
```

这种方式最大的问题是：

> **要求人在输入时就决定信息应该属于哪里。**

AI 时代应该反过来：

```text
任何东西
   ↓
Inbox
   ↓
AI 自动理解
   ↓
自动分类 / 提取 / 建关联
   ↓
Knowledge Base
```

人的主要工作从：

> “这篇东西应该放在哪个目录？”

变成：

> “这个东西值得不值得进入我的知识系统？”

---

# 3. 推荐总体架构

```text
                         Personal Knowledge Base

 ┌────────────────────────────────────────────────────────┐
 │                    Capture Layer                       │
 │                                                        │
 │  Markdown / PDF / Web / Image / Audio / Chat / Email  │
 └──────────────────────────┬─────────────────────────────┘
                            ↓
 ┌────────────────────────────────────────────────────────┐
 │                    Raw Layer                           │
 │                                                        │
 │  原始文件 / 原始消息 / 原始图片 / 原始音频              │
 └──────────────────────────┬─────────────────────────────┘
                            ↓
 ┌────────────────────────────────────────────────────────┐
 │                 Knowledge Compiler                     │
 │                                                        │
 │  Parse → Normalize → Chunk → Extract → Link → Index  │
 └──────────────────────────┬─────────────────────────────┘
                            ↓
 ┌────────────────────────────────────────────────────────┐
 │                  Knowledge Layer                       │
 │                                                        │
 │  Documents / Claims / Entities / Relations / Events  │
 └──────────────────────────┬─────────────────────────────┘
                            ↓
 ┌────────────────────────────────────────────────────────┐
 │                   Retrieval Layer                      │
 │                                                        │
 │  FTS / Vector / Graph / Hybrid Search                 │
 └──────────────────────────┬─────────────────────────────┘
                            ↓
 ┌────────────────────────────────────────────────────────┐
 │                     Context Layer                      │
 │                                                        │
 │  Projects / People / Decisions / Preferences / Ideas  │
 └──────────────────────────┬─────────────────────────────┘
                            ↓
                    Human / AI Agent
```

---

# 4. 数据分层

不要让所有东西都混在一个目录。

推荐：

```text
knowledge/
├── inbox/
├── raw/
├── documents/
├── entities/
├── claims/
├── events/
├── projects/
├── decisions/
├── daily/
└── index.db
```

## inbox

所有刚进入系统、尚未处理的数据。

例如：

```text
inbox/
├── interesting-article.md
├── meeting-2026-10-02.wav
├── screenshot.png
└── paper.pdf
```

Inbox 应该是**低摩擦入口**。

---

## raw

保存原始数据。

原则：

> **Raw 永远不要被 AI 覆盖。**

因为 AI 的理解可能出错。

---

## documents

经过解析、清洗之后的标准化文档。

例如：

```text
documents/
├── article-001.md
├── meeting-2026-10-02.md
└── paper-001.md
```

---

## entities

长期存在的人、组织、项目、产品、技术等。

例如：

```text
entities/
├── people/
├── companies/
├── projects/
├── technologies/
└── products/
```

---

## claims

这是个人知识库最重要的一层。

不要只保存：

> “某篇文章说了什么。”

而应该抽取：

> “我知道什么？”

例如：

```yaml
claim:
  text: "Agent 的 Context 质量是影响任务成功率的重要因素"
  type: hypothesis
  source:
    - blog-389
  created_at: 2026-10-02
```

Claim 可以有：

```text
fact
opinion
hypothesis
decision
observation
prediction
```

这样以后 Agent 才能区分：

> 这是事实，还是我的观点？

---

# 5. Knowledge Compiler

Knowledge Compiler 是整个系统的核心。

它类似软件编译器：

```text
Source Code
   ↓
Parser
   ↓
AST
   ↓
IR
   ↓
Machine Code
```

知识编译：

```text
PDF / Web / Chat / Image
   ↓
Parser
   ↓
Document AST
   ↓
Knowledge IR
   ↓
Markdown / SQLite / Vector
```

---

# 6. Compiler Pipeline

推荐：

```text
Input
 ↓
Parse
 ↓
Normalize
 ↓
Deduplicate
 ↓
Chunk
 ↓
Semantic Extraction
 ↓
Entity Resolution
 ↓
Relation Extraction
 ↓
Index
 ↓
Persist
```

---

## 6.1 Parse

尽可能使用确定性工具。

例如：

```text
PDF       → PyMuPDF
DOCX      → python-docx
HTML      → BeautifulSoup
Markdown  → Markdown parser
Audio     → Whisper
Image     → OCR / VLM
```

这一层尽量**不要调用 LLM**。

---

# 7. CPU 和 LLM 的职责

最佳实践不是：

```text
CPU vs LLM
```

而是：

```text
CPU = deterministic compiler
LLM = semantic compiler
```

## CPU 做：

```text
文件扫描
解析
文本清洗
Chunk
Hash
去重
Metadata
索引
数据库
```

## Local LLM 做：

```text
分类
摘要
事实提取
观点提取
Entity extraction
Relation extraction
语义归纳
冲突识别
```

原则：

> **能用确定性程序解决的问题，不要浪费 LLM。**

---

# 8. Knowledge IR

LLM 不应该直接生成最终 Wiki。

应该先生成一个中间表示：

```json
{
  "document": "...",
  "summary": "...",
  "topics": [],
  "entities": [],
  "claims": [],
  "relations": [],
  "events": []
}
```

这就是：

> **Knowledge IR（Intermediate Representation）**

好处是：

* 模型可以替换
* 输出可以验证
* 可以重新编译
* 可以增加新的字段
* 可以做版本管理
* 不需要重写原始数据

---

# 9. Claim First

传统 RAG：

```text
Document
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB
 ↓
Answer
```

个人知识库应该进一步：

```text
Document
 ↓
Chunk
 ↓
Claim
 ↓
Entity
 ↓
Relation
 ↓
Context
 ↓
Answer
```

因为真正有价值的是：

> **从“我读过什么”变成“我知道什么”。**

---

# 10. Entity Resolution

同一个东西可能有很多名字：

```text
Qwen
通义千问
千问
Qwen3
```

需要统一成：

```text
Entity:
  id: qwen
  aliases:
    - Qwen
    - 通义千问
    - 千问
```

否则知识库会越来越碎。

---

# 11. 不要过早建立复杂 Knowledge Graph

第一阶段：

```text
SQLite
+
Markdown
+
FTS5
```

就够了。

Schema 可以简单到：

```text
documents
chunks
entities
claims
relations
embeddings
```

等真正出现复杂关系查询需求，再引入 Graph DB。

---

# 12. Retrieval

不要只做 Vector Search。

推荐：

```text
                 Query
                   │
        ┌──────────┼──────────┐
        ↓          ↓          ↓
      FTS       Vector      Entity
        │          │          │
        └──────────┼──────────┘
                   ↓
              Rank / Merge
                   ↓
                 Context
```

三种搜索各有所长：

### FTS

适合：

```text
精确关键词
人名
项目名
代码
产品名
```

### Vector

适合：

```text
语义相似
概念搜索
模糊问题
```

### Entity

适合：

```text
围绕某个人
某个项目
某家公司
某个主题
```

最终使用 Hybrid Search。

---

# 13. Knowledge 和 Memory 分开

这是非常重要的设计。

## Knowledge

相对稳定：

```text
概念
事实
文章
技术
历史资料
```

## Memory

关于个人的动态 Context：

```text
当前项目
最近决策
偏好
进行中的任务
最近讨论
长期目标
```

不要把：

```text
“我今天想尝试 X”
```

永久写成：

```text
User prefers X.
```

Memory 应该有：

```text
created_at
updated_at
confidence
source
expires_at
```

---

# 14. Source First

任何知识都应该能追溯来源。

例如：

```yaml
claim:
  text: "..."
  sources:
    - document_id: abc
      location: page:12
```

Agent 回答：

> 你之前认为 Harness 的复杂度可能成为 Agent 工程效率瓶颈。

应该能够继续追溯：

```text
Claim
 ↓
Source
 ↓
Original Document
 ↓
Original paragraph
```

原则：

> **AI 可以总结，但不能让来源消失。**

---

# 15. Confidence

AI 提取出来的知识不要默认都是真的。

建议：

```text
confidence:
  0.95 → 明确事实
  0.80 → 高可信推断
  0.60 → 可能正确
  0.30 → speculation
```

同时记录：

```text
source
model
timestamp
```

例如：

```yaml
claim:
  text: "..."
  confidence: 0.78
  model: qwen3-vl-4b
  source: article-123
  extracted_at: 2026-10-02
```

---

# 16. Incremental Compilation

不要每次重新处理整个知识库。

应该：

```text
new data
   ↓
hash
   ↓
already compiled?
   ├── yes → skip
   └── no
       ↓
      compile
```

进一步：

```text
新 Claim
   ↓
找相似 Claim
   ↓
   ├── duplicate
   ├── update
   ├── contradiction
   └── new
```

这会让知识库随着时间增长仍然保持可用。

---

# 17. Knowledge Conflict

个人知识库一定会出现矛盾。

例如：

```text
2025：
“AI Coding 最大问题是模型能力不足”

2026：
“AI Coding 最大问题是 Context / Harness”
```

不要覆盖。

应该：

```text
Claim A
   │
   └── superseded_by → Claim B
```

保留：

```text
历史观点
当前观点
观点变化时间
变化原因
```

这样知识库实际上还记录了：

> **你的认知是如何变化的。**

这是普通 Wiki 没有的价值。

---

# 18. AI Agent 如何使用

不要让 Agent 每次：

```text
搜索整个知识库
 ↓
把 50 篇文章塞进 Context
```

而应该：

```text
Task
 ↓
Identify relevant entities
 ↓
Retrieve relevant claims
 ↓
Retrieve supporting sources
 ↓
Build Context
 ↓
Execute
```

例如：

```text
任务：
分析 Agent Harness 的产品机会

        ↓

Entities:
Agent
Harness
Coding Agent
DingTalk

        ↓

Claims:
20 条

        ↓

Relevant Sources:
8 篇

        ↓

Context:
5K tokens

        ↓

Agent
```

核心原则：

> **Knowledge Base 不是 Context，Knowledge Base 是 Context 的生产器。**

---

# 19. VENTUNO Q 的推荐部署

VENTUNO Q 非常适合承担：

> **Always-on Local Knowledge Node**

推荐：

```text
VENTUNO Q

Ubuntu
│
├── knowledge-watcher
├── knowledge-compiler
├── SQLite
├── FTS5
├── embedding service
├── Qwen3-VL-4B
└── Web API
```

输入：

```text
~/knowledge/inbox/
```

自动：

```text
watch
 ↓
parse
 ↓
compile
 ↓
index
```

例如：

```bash
knowledge add article.pdf
```

或者：

```bash
cp article.pdf ~/knowledge/inbox/
```

然后后台自动处理。

---

# 20. 本地模型的职责

VENTUNO Q 上的 4B 模型不应该承担：

```text
复杂长文推理
最终决策
高质量长篇写作
```

更适合：

```text
OCR
分类
摘要
Entity extraction
Claim extraction
Relation extraction
Dedup 判断
简单语义判断
```

复杂任务：

```text
VENTUNO Q
    ↓
Knowledge Compiler
    ↓
复杂任务
    ↓
Cloud LLM
```

形成：

```text
Local First
Cloud When Needed
```

---

# 21. 最小可行版本

不要一开始做完整 AI Wiki。

V0.1 只实现：

```text
                    ┌─────────────┐
                    │    Inbox    │
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
                    │    Parse    │
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
                    │    Chunk    │
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
                    │ Qwen 4B     │
                    │ extraction  │
                    └──────┬──────┘
                           ↓
                  ┌─────────────────┐
                  │ SQLite + FTS5   │
                  └────────┬────────┘
                           ↓
                     Search / Ask
```

只需要：

```text
Python
SQLite
FTS5
PyMuPDF
BeautifulSoup
Qwen3-VL-4B
```

---

# 22. V0.2

加入：

```text
Embedding
Hybrid Search
Entity Resolution
Claim dedup
Source citation
```

---

# 23. V0.3

加入：

```text
Knowledge Graph
Contradiction detection
Knowledge evolution
Automatic backlinks
Daily digest
Weekly synthesis
```

---

# 24. V1.0

最终目标：

```text
                  Personal Knowledge Agent

                           User
                            │
                            ▼
                       Intent
                            │
                            ▼
                    Context Builder
                            │
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
           Claims        Entities       Memory
              │             │             │
              └─────────────┼─────────────┘
                            ↓
                       Task Context
                            ↓
                          Agent
                            ↓
                         Result
                            ↓
                     New Knowledge
                            │
                            └──────────→ Knowledge Compiler
```

形成真正的：

> **Knowledge → Context → Action → Feedback → Knowledge**

闭环。

---

# 25. 最终原则

### Principle 1

**Inbox first，分类 later。**

降低知识进入系统的成本。

### Principle 2

**Raw immutable。**

原始资料永远保留。

### Principle 3

**CPU first，LLM second。**

确定性任务不要浪费模型。

### Principle 4

**Claim over Chunk。**

最终管理的是知识，不只是文本块。

### Principle 5

**Source always attached。**

任何 AI 知识都应该能够追溯。

### Principle 6

**Knowledge ≠ Memory。**

事实、观点和个人状态应该分开。

### Principle 7

**Incremental compilation。**

只处理变化的数据。

### Principle 8

**Hybrid retrieval。**

FTS + Vector + Entity，而不是只有 Vector DB。

### Principle 9

**Don't overbuild the graph.**

先 SQLite，真实需求出现后再 Graph DB。

### Principle 10

**Knowledge Base produces Context.**

知识库的终点不是“搜索”，而是：

> **为人和 Agent 持续生产高质量 Context。**

---

# 26. 最终形态

传统个人 Wiki：

```text
我 → 写笔记 → Wiki
```

AI 时代个人知识库：

```text
                         Internet
                            │
                         Work
                            │
                         Life
                            │
                            ▼
                     ┌─────────────┐
                     │   Inbox     │
                     └──────┬──────┘
                            ↓
                  Local Knowledge Compiler
                            │
          ┌─────────────────┼─────────────────┐
          ↓                 ↓                 ↓
       Documents          Claims           Entities
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ↓
                     Personal Knowledge
                            │
                            ↓
                     Context Builder
                            │
                    ┌───────┴───────┐
                    ↓               ↓
                  Human           Agent
                    │               │
                    └───────┬───────┘
                            ↓
                         Action
                            ↓
                         Feedback
                            ↓
                     New Knowledge
                            │
                            └────────→ Compiler
```

**真正值得建设的不是一个“个人 Wiki”，而是一台 Personal Knowledge Compiler。**

它持续把：

> **非结构化世界 → 结构化知识 → Agent Context → 行动 → 新知识**

编译成一个越来越懂你的个人 Context 系统。

如果你要在 **VENTUNO Q** 上实际落地，我建议就按这份文档的 **V0.1** 开始，别一上来做“全功能第二大脑”。先把 `inbox → compile → SQLite/FTS5 → ask` 这个 Loop 跑通。
