# bench — knowledge-compiler 质量与性能实测

用同一套 kc 代码与同一份语料，对比 **本地 `qwen3-4b`（NPU）** 与 **`deepseek-v4-flash`（云端网关）** 的知识编译质量、性能与下游用户场景表现。

- 方案与拍板记录：[`PLAN.md`](./PLAN.md)
- **实测报告（结论 / 表格 / 复现）**：[`results/REPORT.md`](./results/REPORT.md)
- 原始回答附录：[`results/appendix_answers.md`](./results/appendix_answers.md)

## 三组

| 组 | 说明 |
| --- | --- |
| C1 | kc 原样 + `qwen3-4b`（本机 NPU） |
| C2 | 同一 kc 管线 + `deepseek-v4-flash`（网关，推理模型最小适配） |
| C3 | flash 整篇一次抽取（同 IR schema，无分批）→ 金标准/天花板 |

用户场景：U1=本地库+4B，U2=flash库+flash，U3=flash 直读两篇原文。判分 `deepseek-v4-pro`。

## 目录

```
bench/
├── PLAN.md                    方案（已确认/已执行）
├── README.md                  本文件
├── cases/
│   ├── docs.md                语料与 sha256
│   ├── eval_scenarios.json    14 条用户场景测试用例（评测集）
│   └── gold_ir_<docid>.json   C3 金标准 IR
├── scripts/
│   ├── common.py              端点、DOCS、Recording/Flash LLM、judge
│   ├── run_flash_long.py      C3
│   ├── run_compile.py         C1/C2（逐阶段计时 + 全部 LLM 调用记录）
│   ├── eval_compile.py        编译质量（vs 金标准）
│   ├── eval_scenarios.py      用户场景评测
│   └── kb_audit.py            可复用体检：任意 KC_HOME 打分 + 加权综合评分
├── work/                     隔离 KC_HOME 与日志（gitignore，不入库）
└── results/
    ├── c3_flash_long.json
    ├── compile_kc.json / compile_flash.json
    ├── eval_compile.json / eval_scenarios.json
    ├── appendix_answers.md
    └── REPORT.md
```

## 运行

依赖：`qwen-server`（`127.0.0.1:8080`）在线；网关 `192.168.3.1:4000` 可达（`sk-1234`）。全部 stdlib。

```bash
cd ~/Projects/knowledge-compiler
python3 bench/scripts/run_flash_long.py      # C3
python3 bench/scripts/run_compile.py kc      # C1（NPU，约 20 分钟）
python3 bench/scripts/run_compile.py flash   # C2（网关）
python3 bench/scripts/eval_compile.py        # 编译质量（judge）
python3 bench/scripts/eval_scenarios.py      # 用户场景
```

## 知识库体检 / 综合评分（可复用）

```bash
# 任意 KC_HOME 的 8 维健康评分（只读，不写库）
python3 bench/scripts/kb_audit.py --home ~/knowledge
python3 bench/scripts/kb_audit.py --home bench/work/kb-kc \
        --label 4B --golden bench/cases/golden_queries.json --json /tmp/audit.json

# 基于 bench 结果的「质量+性能+落地」加权综合评分（权重可调）
python3 bench/scripts/kb_audit.py score
python3 bench/scripts/kb_audit.py score --w-quality 43 --w-perf 15 --w-practical 42

# 两个 KC_HOME 并排对比（如重编译/补跑判重前 vs 后）
python3 bench/scripts/kb_audit.py compare --a ~/knowledge-backup --b ~/knowledge \
        --label-a 修复前 --label-b 修复后 --golden bench/cases/golden_queries.json
```

- `kb_audit.py` 的检索维度需要 golden 集（`{"query","expected_doc_ids","k","mode"}`）；缺省则标 n/a，不计入综合分。
- `bench/cases/golden_queries.json` 由 14 条场景中的检索题导出，可用于 bench KB。
- 默认权重（质量60/性能25/落地15）下 flash 综合略高；把落地权重提到 ~45（离线/隐私/$0）后本地 4B 反超（脚本会提示）。

端点/模型可用环境变量覆盖：`KC_LOCAL_BASE`、`KC_GATEWAY_BASE`、`KC_GATEWAY_KEY`。

## 关键结论（详见 REPORT.md）

- 编译质量：flash 略优（实体 F1 +0.10~0.15，论断召回 +0.03~0.05）；两者论断召回 0.41–0.50，**主要受管线分批/截断限制**。
- 性能：全流程 4B 21.7 min vs flash 8.5 min；4B 本地 $0、离线。
- 场景：问答要点覆盖 4B 0.57 vs flash 0.68 vs 长上下文 0.95；**瓶颈是检索/上下文组装**；负样本 3 组均正确拒答。
- hybrid 检索（FTS+LIKE+向量+实体）文档召回 1.00，显著优于 FTS 的 0.91（跨文档题 0.5）。
