# 测试语料（只读引用，原始文件不改写）

| 标记 | 文件 | doc_id (sha256[:16]) | 字符数 | sha256 |
| --- | --- | --- | --- | --- |
| Doc A | `/home/arduino/Projects/blog2/content/post/2026/178-agent-model-plus-harness.md` | `de43b976f6666a44` | 16769 | `de43b976f6666a44011457eced39e9afb243c3a81edce968a76fdb6cd118b9b3` |
| Doc B | `/home/arduino/Projects/blog2/content/post/2026/263-loop-engineering.md` | `78d89a53782ce92b` | 7075 | `78d89a53782ce92b6f3b0e25ff160c9c38badf328a2c18317532740008f1a82c` |

- 主题：AI Agent 工程分层（Agent = Model + Harness / Loop Engineering）。
- Doc B 正文显式引用了 Doc A，天然形成跨文档关系，供跨文档问答与 context 综合使用。
- 选材日期：2026-10-04；两篇均位于 `blog2/content/post/2026/`。
