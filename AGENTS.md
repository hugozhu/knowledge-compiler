# AGENTS.md — knowledge-compiler

Personal Knowledge Compiler（V0.1）：把非结构化输入持续编译成可检索、可追溯、可喂给 Agent 的个人 Context。运行于 VENTUNO Q（Ubuntu 24.04, aarch64）。

设计文档与实施计划在 `docs/`，动手前先读：

- `docs/personal-knowledge-base-best-practices.md` — 总体提案（唯一设计权威）
- `docs/v0.1-plan.md` — 本阶段范围、Schema、验收标准

## 项目现状

- **V0.1 → V1.0 四个版本全部实现并端到端验收**（2026-10-03，见 Issues #1–#4 与 `docs/` 下四份计划的状态表）。
- 数据目录默认 `~/knowledge`（与仓库分离，勿提交）；代码在 `src/kc/`，启动器 `./kc`。
- Schema 用 `PRAGMA user_version` 版本化迁移（当前 v4：memories），旧库自动升级。
- `entities/<type>/` 与 `daily/` 是生成器产物（确定性视图），归 `kc backlinks` / `kc digest` 所有，勿手改。
- Web API：`./kc serve`（默认 127.0.0.1:8300，`KC_API_KEY` 可选 Bearer 鉴权）。
- **OpenCode MCP 接入**：全局配置已加 `kc` 服务器（`kc mcp` stdio 适配器，代理到 serve 的 REST API）。本会话的 `tools.kc.*` 即来自它；serve 挂了工具会返回带启动指引的错误而非崩溃。
- NPU 吞吐：抽取每批（≤2000 字符）约 1 分钟；判重/巡检每 4 条约 30–60s——大批量用 `./kc watch` 后台跑，或 `--no-dedup` / `--no-backlinks` 跳过对应阶段。

## 硬约束（来自本机实测，勿凭经验假设）

1. **零第三方依赖**：系统 Python 3.12.3 无 pip。只用 stdlib（urllib、sqlite3、subprocess…）。`requirements.txt` 里只允许「可选增强」。
2. **LLM 端点**：`http://127.0.0.1:8080/v1`（Bearer `sk-local`）。文本模型 `qwen3-4b`，视觉模型 `qwen3-vl-4b`。
3. **模型限制**：不支持 tool calling；上下文约 4–8k；约 16 tok/s。所有提示词保持小；抽取 max_tokens ≤ 768；`ask` 上下文 ≤ ~3.5k 字符。
4. **PDF**：用 `pdftotext`/`pdftoppm`（已装）。无 tesseract、无 PyMuPDF/bs4——别 import，除非先装。
5. **数据目录** `~/knowledge`（`KC_HOME` 覆盖）里的 `raw/` **永不可写/删**；重编译只动 `documents/` 与 DB。
6. 检索用 SQLite FTS5 `trigram`；两字中文短查询必须 LIKE 兜底。

## 代码约定

- 布局：`src/kc/` 包 + 根目录 `kc` bash 启动器（设 `PYTHONPATH=src`）。
- 一切可追溯：`doc_id = sha256(原始文件)[:16]`；claim 必带 `document_id` 来源。
- 幂等：`init`/`compile` 重复执行安全；增量以 `compile_log.sha256` 为准；重编译先级联删再插。
- LLM 输出一律走鲁棒 JSON 解析 + 失败重试一次 + 降级（仅摘要/`--no-llm` 路径），任何模型故障不得阻塞确定性编译。
- CLI 子命令与行为以 `docs/v0.1-plan.md` 第 5 节为准；新增命令先改计划再写码。
- **新功能必须附测试**（`tests/`，用 `tests/support.py` 的 FakeLLM/KCTestCase，禁真实网络与 NPU 依赖）；任何改动后 `./kc test` 须全绿再提交。

## 常用命令

```bash
./kc init && ./kc add docs/personal-knowledge-base-best-practices.md && ./kc compile
./kc search "Claim"            # FTS 路径
./kc search "知识库"            # LIKE 兜底路径
./kc ask "这份文档的核心观点是什么？"
./kc test                      # 自动化测试套件（离线 FakeLLM，改代码后必跑）
nohup ./kc serve --port 8300 & # Web API（OpenCode 的 kc MCP 工具依赖它）
curl -s http://127.0.0.1:8300/health   # 确认知识库服务在线
```

## 完成定义

改动以 `docs/v0.1-plan.md` 第 6 节验收清单为准，逐条通过才算完成；每完成一步在计划第 7 节表格标注状态。禁止把 V0.2+ 功能（embedding、entity resolution、claim 去重、graph）混进本阶段 PR。

## 环境备忘

- 模型服务由 `~/Projects/qwen-server/ctl.sh` 管理（start/stop/status/logs）；它挂了先重启它，不要绕过。
- 大文件下载走 SOCKS5 `192.168.3.1:11081`；`huggingface.co` 不通（用 hf-mirror / ModelScope）。
- **不要升级 Ubuntu 26.04**（官方确认与本板不兼容）。
