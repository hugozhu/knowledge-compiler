# 实测方案：knowledge-compiler 质量与性能评测（deepseek-v4-flash vs 本地 qwen3-4b）

> 状态：**已完成**（2026-10-04）。结果见 `results/REPORT.md`、`results/appendix_answers.md`；运行说明见 `README.md`。
> 已拍板：语料 = `178` + `263`；基线 = C2 + C3 两层；judge = `deepseek-v4-pro` + 事实匹配；编译 = 纯编译 + 完整流程都跑。
> （judge 原定 `qwen3-8-max`，实测 >8 条即 HTTP 500、单次 100s+，无法批量判分，改为 `deepseek-v4-pro`。）
> 目标：给 knowledge-compiler 写一篇有实测数据支撑的 blog——回答「在本机 NPU 上跑 4B 模型做知识编译，和用前沿模型（deepseek-v4-flash）比，质量差多少、性能差多少、是否够用」。

---

## 0. 一句话方案

用同一份语料（2 篇 2026 blog），跑三组知识编译：

| 组 | 名称 | 编译器 / 模型 | 说明 |
| --- | --- | --- | --- |
| **C1** | kc 本地（现状） | knowledge-compiler 原样 + `qwen3-4b`（NPU，`127.0.0.1:8080`） | 被测对象 |
| **C2** | kc 管线 + flash | **同一套 kc 代码**，仅把 `KC_LLM_MODEL` 指向 `deepseek-v4-flash`（网关 `192.168.3.1:4000`） | 控制变量：只换模型，回答"本地 4B 到底差在哪" |
| **C3** | flash 长上下文（Oracle） | 独立脚本，**整篇一次**抽取（不分块），同 IR schema | 质量天花板 + 评测集金标准 |

再用这 3 份知识库跑一组**用户场景评测**（检索 / 问答 / context / 实体 / 负样本），量化对比。所有产物放在 `knowledge-compiler/bench/`。

**为什么这样设计**：C1 vs C2 只改模型、不改代码，是最干净的对照；C3 用 flash 的百万上下文做整篇抽取，既当质量金标准，又暴露 kc「2000 字分批」带来的信息损失。

---

## 1. 测试素材

从 `blog2/content/post/2026/` 选 **2 篇 2026 年近期、主题相邻**的中文 blog，保证既有单篇问答，也能测跨文档综合：

| 文档 | 文件 | 体量 | 选它的理由 |
| --- | --- | --- | --- |
| Doc A | `178-agent-model-plus-harness.md` | 25.5 KB | 「Agent = Model + Harness」，实体/断言密集 |
| Doc B | `263-loop-engineering.md` | 12.2 KB | 「Loop Engineering」，**正文显式引用了 Doc A**，天然形成跨文档关系 |

- 两篇同属「Agent 工程分层」主题，共享实体（Agent / Harness / Context / Loop / Prompt / Codex…），适合实体归并、跨文档 context、RAG 综合题。
- 备选（若你想换）：`209-ai-native-harness-engineering-knowledge-compound-interest.md`、`172-evolvable-agent-skills-best-practices.md`。
- 语料以**只读引用**方式使用（`bench/cases/docs.md` 记录绝对路径 + `sha256`），不改写 blog2；编译时 `kc add` 会复制进隔离的 `KC_HOME/raw`。

---

## 2. 目录与产物（全部在 knowledge-compiler 下）

```
knowledge-compiler/bench/
├── PLAN.md                 # 本方案
├── README.md               # 复现步骤 + 结论摘要（收尾写）
├── cases/
│   ├── docs.md             # 选定文档、路径、sha256、体量
│   ├── gold_ir_<docid>.json# flash 整篇抽取的金标准 IR（机器可读）
│   ├── eval_scenarios.yaml # 用户场景测试用例（评测集主体）
│   └── gold_answers.json   # 每题的参考答案 / 期望事实
├── scripts/
│   ├── run_compile.py      # 对指定模型跑 kc 编译，逐批计时，产出 metrics json
│   ├── run_flash_long.py   # C3：flash 整篇抽取 → gold IR
│   ├── make_eval.py        # 依据语料+gold 生成/校验场景用例（flash 辅助 + 人工确认）
│   ├── eval_compile.py     # C1/C2 的 IR 对比 C3 金标准：claim/entity/relation 召回
│   ├── eval_scenarios.py   # 跑检索/ask/context，按用例打分
│   └── report.py           # 汇总 → results/REPORT.md
├── work/                   # 运行数据（**gitignore**，不进仓库）
│   ├── kb-kc/  kb-flash/  kb-flash-long/   # 三份隔离 KC_HOME
│   └── logs/
└── results/
    ├── compile_metrics.json
    ├── scenario_metrics.json
    ├── flash_cost.json
    └── REPORT.md           # 对比报告 = blog 素材（中文）
```

- 全部使用**独立 `KC_HOME`**（`bench/work/kb-*`），绝不污染现有 `~/knowledge`。
- `bench/work/` 与 `bench/results/*.json` 建议加进 `.gitignore`（报告与 gold 可提交，中间库不提交）。

---

## 3. 编译评测（compile）

### 3.1 运行

```bash
# C1 本地 4B（NPU）
KC_HOME=bench/work/kb-kc KC_LLM_MODEL=qwen3-4b \
  KC_LLM_BASE_URL=http://127.0.0.1:8080/v1 \
  ./kc compile --no-dedup --no-backlinks      # 只测编译；另跑一次含全流程

# C2 同管线换 flash
KC_HOME=bench/work/kb-flash KC_LLM_MODEL=deepseek-v4-flash \
  KC_LLM_BASE_URL=http://192.168.3.1:4000/v1 KC_LLM_API_KEY=sk-1234 \
  ./kc compile --no-dedup --no-backlinks

# C3 flash 整篇（脚本，一次调用/篇，无分批）
python3 bench/scripts/run_flash_long.py
```

`run_compile.py` 会：逐文档、逐 batch 记录**开始/结束时间戳、输入输出 token、是否 JSON 合法、是否触发 compact 重试/降级**（从 `compile_log` + 包裹 LLM 调用获得），写入 `results/compile_metrics.json`。

### 3.2 指标

**性能**
- 每篇 / 每 batch 墙钟时间；端到端总时间；有效 tokens/s
- 输出 token 数、是否截断、`[extract-failed]` 次数、JSON 一次通过率
- flash 侧按网关返回 usage 估算 token 成本（`results/flash_cost.json`；本地 NPU 记为 `$0`）

**质量（对照 C3 金标准）**
- **claims**：数量；recall = 命中金标准 claim / 金标准总数；precision（AI 判重 + 归一化匹配）
- **entities**：按归一化名（含别名）算 precision / recall / F1
- **relations**：(subject,predicate,object) 元组 recall
- **summary**：覆盖关键点的比例（judge 打分）
- 匹配判定：确定性归一化匹配为主 + `deepseek-v4-pro`（**不同于被测/基线模型**，降低自评偏差）做语义等价判定；关键项人工抽查。

---

## 4. 用户场景评测（use case / 评测集）

### 4.1 测试用例（`cases/eval_scenarios.yaml`）

~12–15 条，覆盖 kc 的全部能力面。每条含：

```yaml
- id: S01
  category: exact_search        # exact|semantic|entity|cross_doc|fact|multihop|context|negative
  query: "Codex 的 /goal 有哪五个状态？"
  expected_doc_ids: ["<docid-B>"]
  expected_facts: ["pursuing", "paused", "achieved", "unmet", "budget-limited"]
  gold_answer: "..."
  note: "FTS 精确命中测试"
```

场景清单（初稿）：
1. 精确关键词检索（专有名词，如 `Ralph Loop`）
2. 语义检索（无字面关键词，如「Agent 自主运行如何避免目标漂移」）
3. 实体检索（围绕 `Harness Engineering` 的文档/论断）
4. 单篇问答（fact）
5. 跨文档综合（Loop Engineering 与 Harness Engineering 的关系）
6. 多跳（`/goal` 由哪几层工程演进而来 → 需要两篇）
7. Context Builder（给定任务生成上下文包，检查预算内是否含关键论断/实体）
8. 实体归并 / 关系（graph、dedup 干跑）
9. 负样本（语料里没有的主题，如「Airflow 任务调度」→ 必须承认无资料，测幻觉）
10. Memory 注入（可选：加一条记忆，验证 `ask`/`context` 是否区分知识/记忆）

### 4.2 三组对照

| 组 | 用什么回答 | 目的 |
| --- | --- | --- |
| **U1** | C1 知识库 + kc 本地 `ask`/`search`/`context` | 最终交付形态（全本地） |
| **U2** | C2 知识库 + 同命令（flash 提关键词/生成） | 分离"知识质量"与"检索/生成模型"的影响 |
| **U3** | 把两篇原文喂给 flash 直接答（长上下文 RAG 天花板） | 前沿模型上限 |

### 4.3 指标

- **检索**：`expected_doc_ids` 的 recall@k（k=6/10）、MRR；`fts` vs `hybrid` 两种模式对照
- **问答**：`expected_facts` 覆盖率（归一化匹配）+ 忠实度（judge 0–5，`deepseek-v4-pro`）+ 是否带正确来源引用
- **context**：pack 是否含关键论断/实体、是否在 `--max-chars` 预算内、chars 统计
- **负样本**：拒答率（不幻觉）pass/fail
- **性能**：每次 `search`/`ask`/`context` 墙钟时间（kc.ask 本地约 1–2 min，context 约 30–60s）

---

## 5. 报告与 blog

`results/REPORT.md`（中文）结构：
1. 环境与方法（硬件、模型、语料、可复现命令）
2. 编译质量对比表（C1/C2/C3：claims/entity F1/relation recall/JSON 合法率）
3. 编译性能对比（时间、tokens/s、成本）
4. 用户场景对比表（U1/U2/U3：检索 recall、问答覆盖率、幻觉率、时延）
5. 结论：本地 4B「够用在哪些场景 / 差在哪些场景」、成本与隐私权衡、后续优化方向
6. 局限与复现说明

报告即 blog 素材，交付前给你过。

---

## 6. 可复现与诚信约定

- 文档按 `sha256` 固定；模型名/版本原样记录；生成温度固定（与 kc 一致的 0.2）。
- 所有评测脚本纯 Python stdlib（沿用项目零依赖约束）。
- LLM-as-judge 的偏置写进"局限"；关键结论辅以确定性 `expected_facts` 匹配 + 人工抽查。
- 时延做 1 次主测 + 必要时复跑 2 次标注波动；NPU 吞吐噪声如实记录。
- 不修改 `knowledge-compiler` 核心代码（除非发现 bug，另开 issue/PR）。

---

## 7. 时间预算（预估）

| 阶段 | 预估 |
| --- | --- |
| 选语料 + 写 C3 gold 抽取 | 10 min |
| C1 本地编译（~7–10 batch × ~1 min） | ~10–15 min |
| C2 flash 编译 | ~3–5 min |
| 评测集生成 + 人工确认 | 15 min |
| 场景评测（U1/U2/U3，含本地 ask） | ~30–45 min |
| 汇总报告 | 20 min |

---

## 8. 需要你拍板的点（已给推荐）

1. **语料**：Doc A `178-agent-model-plus-harness` + Doc B `263-loop-engineering`（推荐，天然跨文档引用）；或换 `209` / `172`。
2. **基线定义**：采用 **C2 同管线换模型 + C3 flash 整篇 Oracle** 两层（推荐）；还是只做 C3。
3. **判分模型**：用 `deepseek-v4-pro` 做 judge + 确定性事实匹配（推荐，避免 flash 自评）；或只做确定性匹配 + 人工。
4. **目录**：新建 `bench/`（推荐）；或放到别处。
5. **报告语言**：中文（推荐）。
6. **成本**：只记 latency/tokens，还是也估 flash 的 $ 成本（推荐都记）。
7. **编译命令**：是否额外跑一次**含 dedup/backlinks/embed 的完整流程**（更真实，但更慢）。

确认后我按此执行；执行中如发现计划偏差，会回来更新本文件状态。
