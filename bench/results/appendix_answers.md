# 附录：用户场景原始回答（节选）

> 自动生成自 `bench/results/eval_scenarios.json`；U1=本地4B，U2=flash同管线，U3=flash直读原文。


---

## S01 [exact_search] Codex 的 /goal 有哪五个状态？

- 期望文档：['78d89a53782ce92b']
- 期望要点：['pursuing', 'paused', 'achieved', 'unmet', 'budget-limited']

### U1（21.02s｜covered=[1, 2, 3, 4, 5]｜hallucinated=False）

Codex 的 `/goal` 有五个状态：pursuing、paused、achieved、unmet、budget-limited [1]。

### U2（2.1s｜covered=[1, 2, 3, 4, 5]｜hallucinated=False）

Codex 的 `/goal` 实现了五个状态：

**pursuing → paused → achieved / unmet / budget-limited**

即：执行中（pursuing）、暂停（paused）、达成（achieved）、未达成（unmet）、预算耗尽（budget-limited）。[2][6]

### U3（1.24s｜covered=[1, 2, 3, 4, 5]｜hallucinated=False）

Codex 的 `/goal` 实现了五个状态（见文档B）：

`pursuing → paused → achieved / unmet / budget-limited`

即：pursuing（追求中）、paused（暂停）、achieved（达成）、unmet（未达成）、budget-limited（预算耗尽）。


---

## S02 [exact_search] Anthropic Managed Agents 解耦后的三个核心接口是什么？

- 期望文档：['de43b976f6666a44']
- 期望要点：['Session', 'Sandbox', 'Harness']

### U1（14.53s｜covered=[3]｜hallucinated=True）

Anthropic Managed Agents 解耦后的三个核心接口是：**Model、Action、Observation**。

- **Model**：负责思考、推理和决策，是Agent的“大脑”[1]。  
- **Action**：定义了Agent如何与外部世界交互，如调用API或执行操作[2]。  
- **Observation**：负责接收环境反馈，实现Agent与现实世界的连接[3]。

这些接口被设计为稳定、解耦，使得即使底层模型或实现方式变化，整体系统仍能稳定运行，从而实现“未来也能用的接口”[2]。  

> 注：资料中虽未明确列出“三个核心接口”名称，但通过“解耦核心组件”与“Agent = Model + Harness”推导，结合“Action”与“Observation”作为关键交互环节，可合理归纳出上述三个核心接口。

### U2（3.73s｜covered=[1, 2, 3]｜hallucinated=False）

资料中没有出现"三个核心接口"这一说法，也没有列出解耦后的三个接口 [1][2][3][4][5]。

资料中相关的内容是：Anthropic Managed Agents 把 Agent 组件抽象为稳定接口、实现层可随意替换 [2][3]，并给出了 **四个基本组件**——Model（大脑）、Harness（神经系统）、Session（记忆体）、Sandbox（手脚）[5]。至于解耦后具体是哪三个核心接口，提供的资料里没有说明，我无法据此回答。

### U3（2.01s｜covered=[1, 2, 3]｜hallucinated=False）

解耦后靠三个核心接口运转（来源：文档A）：

1. **Session** —— 持久化事件日志接口，不依赖任何 Model 或 Harness，提供 `emit_event()`、`get_events()`、`wake()`。
2. **Sandbox** —— 执行环境接口，只暴露 `execute(name, input) -> string`，Harness 不知道背后是容器、VM 还是 MCP 服务。
3. **AgentHarness** —— Agent 循环接口，唯一有状态但可替换的组件，负责「读取事件 → 构建 prompt → 调用模型 → 执行工具 → 写回」。

这三个接口只定义「形状」，不定义「实现」。


---

## S03 [semantic] 为什么针对旧模型写的 workaround 最终会变成死代码？

- 期望文档：['de43b976f6666a44']
- 期望要点：['Harness 编码了关于模型做不到什么的假设', '模型能力提升后假设过时', 'context reset', 'Sonnet 4.5', 'Opus 4.5']

### U1（7.82s｜covered=[1, 2]｜hallucinated=False）

针对旧模型写的 workaround 最终会变成死代码，是因为这类临时解决方案通常依赖于特定模型的特性或行为，而这些特性在模型更新或迭代后可能被修改或废弃 [6]。当模型发生变化时，原本为适配旧模型设计的 workaround 就无法继续工作，从而失去实用性，最终成为“死代码” [6]。

### U2（4.24s｜covered=[1, 2, 3, 5]｜hallucinated=False）

针对旧模型写的 workaround 会变成死代码，是因为它本质上依赖对旧模型能力或行为的假设；当模型能力提升后，这些假设会过时，workaround 原本要应对的问题可能消失，触发条件不再存在，于是它不再被执行，变成死代码，甚至可能产生副作用 [4]。

资料中的例子是：同一个 Harness 用在 Claude Opus 4.5 上时，context anxiety 行为消失，导致 context reset 变成死代码 [5]。这也被概括为 Harness 的根本困境：其包含的假设注定会过时，模型能力提升会让 workaround 变成死代码甚至产生副作用 [4]。类似地，资料也指出模型能力提升会让 Harness 的假设过时，这不是 bug，而是 feature [2]。

### U3（3.23s｜covered=[1, 2, 3, 4, 5]｜hallucinated=True）

针对旧模型写的 workaround，本质上是在弥补当时模型“做不到”或“会出错”的能力缺陷。但 Harness 会编码“模型做不到什么”的假设，而这些假设会随着模型能力提升迅速过时：新模型不再出现旧行为，原来的 workaround 就不再被触发、不再必要，于是变成死代码，甚至可能变成副作用。文档A举的例子是：Claude Sonnet 4.5 会在接近上下文限制时过早结束任务，于是 Harness 加入 context reset；但换到 Claude Opus 4.5 后该行为消失，context reset 就成了死代码。（文档A）


---

## S04 [semantic] Agent 长时间自主运行会出现哪两个致命问题？

- 期望文档：['78d89a53782ce92b']
- 期望要点：['context rot', 'goal drift', 'fresh context per iteration']

### U1（4.34s｜covered=[1, 2]｜hallucinated=False）

Agent长时间自主运行会出现两个致命问题：**上下文腐烂**（context rot）和**目标漂移**（goal drift）[3]。

### U2（4.12s｜covered=[1, 2, 3]｜hallucinated=False）

Agent 长时间自主运行会出现的两个致命问题是 [3]：

1. **context rot（上下文腐烂）**——历史信息太多，精确度下降。
2. **goal drift（目标漂移）**——跑着跑着解决了一个略有不同的问题。

对应的解法是每轮使用 **fresh context**，让长期记忆留在 loop 里，而不是放在 model 里 [3]。

### U3（1.81s｜covered=[1, 2]｜hallucinated=False）

根据文档B，Agent 长时间自主运行会出现两个致命问题：

1. **context rot（上下文腐烂）**：历史信息太多，精确度下降。
2. **goal drift（目标漂移）**：跑着跑着解决了一个略有不同的问题。（来源：文档B）


---

## S05 [entity] 设计 Agent Harness 的五个原则是什么？

- 期望文档：['de43b976f6666a44']
- 期望要点：['接口稳定，实现可变', 'Brain 和 Hands 解耦', 'Session 独立于上下文窗口', 'Token 不可达', '按需创建，不要预分配']

### U1（20.92s｜covered=[1, 2]｜hallucinated=False）

根据提供的资料，设计 Agent Harness 的五个原则可归纳如下：

1. **Harness 必须解耦 Model 与执行环境**：Harness 应独立于具体模型和执行环境（如容器、手机、模拟器），从而支持多种执行方式 [4]。  
2. **设计足够通用的接口**：应定义通用的抽象接口（如 `execute(name, input) → string`），以适应未来模型、Harness 或 Sandbox 的演进，而非依赖特定实现 [5]。  
3. **模型越强，Harness 越重要**：随着模型能力增强，对精确控制和结构化设计的需求增加，因此 Harness 的设计必须更精密 [3]。  
4. **以“稳定接口”为核心**：Agent 架构应关注稳定、可预测的接口，而非单纯追求性能优化，这类似于操作系统对进程和文件的抽象 [6]。  
5. **支持“Many Brains”与“Many Hands”**：通过解耦，可灵活扩展多个推理单元（Brain）与多个执行单元（Hand），实现弹性扩展与资源隔离 [4]。

这些原则共同构成一个可演进、可扩展且可靠的 Agent 架构基础 [6]。

### U2（5.24s｜covered=[]｜hallucinated=False）

提供的资料中**没有**"设计 Agent Harness 的五个原则"这一内容，我无法据此列出五条原则。

资料中与 Harness 设计相关的可用信息仅包括：

- Agent 系统由 Model、Harness、Session、Sandbox 四个组件构成，Harness 负责循环控制、工具路由、上下文管理，类比"神经系统"[3][4]。
- Harness 才是 Agent 从 demo 走向 production 的关键[4]。
- Harness 的根本困境：它编码了关于"模型做不到什么"的假设，而这些假设会随模型能力提升迅速过时；推论是"模型越强，Harness 的设计就越重要"[2]。
- 一种设计方向是解耦：让 Harness 变为无状态，状态全放在 Session 中，Harness 崩溃后可用 `wake(sessionId)` / `getSession(id)` 从事件日志恢复、从中断处继续；整体靠三个核心接口（如 Session 的 `emit_event`、`get_events` 等）运转[5]。

如果你需要"五个原则"的确切内容，建议补充包含该章节的资料。

### U3（1.92s｜covered=[1, 2, 3, 4, 5]｜hallucinated=False）

设计 Agent Harness 的五个原则（来源：文档A《Agent = Model + Harness》）：

1. **接口稳定，实现可变**——Harness 接口应假设"模型会变强"，把实现层与接口层分开，替换实现时不破坏接口。
2. **Brain 和 Hands 解耦**——不要把执行环境和循环控制塞进同一进程，让 Harness 通过统一的 `execute()` 接口调用所有工具，Sandbox 挂了只是工具调用失败，不会导致整个会话丢失。
3. **Session 独立于上下文窗口**——事件日志应持久化且 append-only；Compaction 和 trimming 可发生在 Harness 层，但原始数据永远可恢复。
4. **Token 不可达**——结构性隔离比策略性隔离更可靠；Git Token 初始化注入、MCP Vault 代理，让 Agent 代码永远接触不到敏感凭证。
5. **按需创建，不要预分配**——TTFT 是用户最敏感的延迟指标，容器/沙箱仅在需要时启动，不要为每个 Session 预付初始化成本。


---

## S06 [entity] Ralph Loop 的核心观点是谁提出的？原话大意是什么？

- 期望文档：['78d89a53782ce92b']
- 期望要点：['Geoffrey Huntley', 'loop 的智能在 loop 里而不是 agent 里', 'agent 是可替换的']

### U1（16.37s｜covered=[]｜hallucinated=True）

Ralph Loop 的核心观点是由 Daniel Demmel 提出的，他在其文章《Feedback Loop Engineering》中系统性地阐述了这一概念 [3]。  

其核心大意是：在“Feedback Loop Engineering”框架下，**人决定下一步做什么**，而“/goal”机制打破了传统模式——不再要求Agent执行每一步的详细指令，而是仅告知其终点目标，由Agent自主迭代并验证每一步的正确性 [3]。  

该机制的关键在于：**Agent必须通过真实验证（如检查实际文件、运行实际测试）才能确认完成，而非依赖代理信号（如“已完成”）**，从而避免“context rot”与“goal drift”等错误 [6]。  

因此，Ralph Loop 的核心观点是：**以目标为终点，通过每轮真实验证实现自主迭代与可靠执行** [3]。

### U2（4.52s｜covered=[]｜hallucinated=False）

资料中没有说明「Ralph Loop」的核心观点由谁提出，也没有给出相关原话。

唯一相关的是 [4]：Greg Brockman 称 Codex 现在内置了 Ralph loop++——但这只是提及，并未涉及 Ralph Loop 的核心观点或提出者。

### U3（1.63s｜covered=[1, 2, 3]｜hallucinated=False）

Geoffrey Huntley 提出了 Ralph Loop（见文档B）。

其核心观点原话大意是：**循环的智能在循环本身，而不在 agent 里；agent 是可替换的，是循环让它变得自主。**

> "The loop's intelligence is in the loop, not in the agent. The agent is fungible. The loop is what makes it autonomous."

来源：文档B《Loop Engineering：AI Agent 工程的第五层》。


---

## S07 [cross_doc] Loop Engineering 和 Harness Engineering 是什么关系？

- 期望文档：['de43b976f6666a44', '78d89a53782ce92b']
- 期望要点：['Loop 在 Harness 之上加一层自治', '好的 Loop 需要好的 Harness 做底座', 'Harness 模式人编排 workflow', 'Loop 模式 Agent 根据当前状态自己决定下一步']

### U1（24.55s｜covered=[1, 3, 4]｜hallucinated=True）

Loop Engineering 和 Harness Engineering 是 AI Agent 工程中的两个相关但不同的概念，它们共同构成 Agent 的系统架构，但层级和功能不同。

- **Harness Engineering** 是 Agent 的基础框架，负责定义 Agent 在何种结构中运行，包括 workflow 的编排、状态管理（如 5 个状态的结构化状态机）以及如何响应外部输入 [3]。它决定了 Agent 的“运行框架”，由人类设计并控制每一步的执行 [3]。

- **Loop Engineering** 则在 Harness Engineering 之上发展，引入了“自治”能力：Agent 能根据当前状态自主决定下一步行动，而非完全由人类编排 [4]。它新增了关键组件，包括 **fresh context per iteration**（防止上下文退化）、**structured state machine**（明确的状态结构）、**audit-first completion**（强制验证以避免代理信号崩溃）以及 **budget as first-class concern**（将 token 预算作为核心考量）[4]。

因此，Loop Engineering 并非简单地在 Harness Engineering 上添加“auto_continue=true”，而是通过上述多个组件的组合，构建了一个更智能、更自主的执行机制 [4]。可以说，**Loop Engineering 是在 Harness Engineering 基础上进一步演进，实现了从“人编排”到“Agent 自主决策”的转变** [4]。

简言之：**Loop Engineering 是在 Harness Engineering 之上发展出的更高级、更自主的工程范式** [4]。

### U2（3.94s｜covered=[1, 2, 3, 4]｜hallucinated=True）

Loop Engineering 不是替代 Harness Engineering，而是在 Harness 之上叠加的一层自治 [3]。

具体关系：

- **层级关系**：在工程分层中，Harness Engineering 是第四层（Prompt < Context < Feedback Loop < Harness），而 Loop Engineering 被定位为第五层 [2][4]。
- **底座与上层**：好的 Loop 需要好的 Harness 做底座 [3]。
- **核心区别在于「谁决定下一步」**：Harness 模式下由人编排 workflow，Agent 在人的框架内执行；Loop 模式下 Agent 根据当前状态自己决定下一步，人只定义终点线 [2][3]。

资料也指出，把它简单理解为「Harness 加个 auto_continue=true」虽有道理但不完整——Loop Engineering 真正新增的是四个组件的组合：fresh context per iteration、structured state machine、audit-first completion、budget as first-class concern [3]。

### U3（2.82s｜covered=[1, 2, 3, 4]｜hallucinated=False）

Loop Engineering 建立在 Harness Engineering 之上，不是替代关系。文档B明确写道：「Loop Engineering 不是替代 Harness——它在 Harness 之上加了一层自治。好的 Loop 需要好的 Harness 做底座。」（文档B）

两者的核心区别在于**谁决定下一步**：
- Harness 模式下，人编排 workflow，Agent 在框架内执行；
- Loop 模式下，Agent 根据当前状态自己决定路径，人只定义终点线（/goal）和预算。（文档B）

在五层演进中，Loop 处于最上层：Prompt → Context → Feedback Loop → Harness → Loop，前四层都是「人决定下一步做什么」，Loop 层则是「人定义终点线 + 预算，Agent 自己找路跑到」。（文档B）

落地建议是**先建 Harness，再上 Loop**：没有 Harness 做底座就上 Loop，等于让 Agent 在没有护栏的高速公路上自动驾驶。文档B还指出需要四样东西作为支撑——验证工具、安全护栏、状态可观测、优雅退出。（文档B）

作为背景，文档A对 Harness 的定位是：Agent = Model + Harness，Harness 负责循环控制、工具路由、状态管理，其假设会随模型能力提升而过时，因此要设计稳定接口而非「今天最好的 Harness」。（文档A）


---

## S08 [cross_doc] Agent = Model + Harness 公式与 Loop Engineering 如何互相补充？

- 期望文档：['de43b976f6666a44', '78d89a53782ce92b']
- 期望要点：['Model 负责思考推理决策', 'Harness 负责循环控制、工具路由、状态管理', 'Harness 约束并放大 Model 的能力', 'Loop 由人定义终点线和预算', 'Agent 自己找路跑到终点']

### U1（18.97s｜covered=[1, 2, 5]｜hallucinated=True）

Agent = Model + Harness 的公式与 Loop Engineering 互相补充：  

- **Model** 负责思考、推理和决策，是 Agent 的“大脑”；  
- **Harness** 负责控制执行流程，包括工具调用、上下文管理与循环控制，是 Agent 的“神经系统”[1]。  

而 **Loop Engineering** 的核心是将 Agent 的运行从“被指令”转变为“自主寻路”——人不再定义每一步，而是仅设定终点（goal）[3]。此时，Harness 成为关键，它通过反馈机制（Feedback Loop）实现自我校准与持续运行，使 Agent 能在无人干预下自主完成任务 [2]。  

因此，**Harness 为 Agent 提供了运行框架，而 Loop Engineering 为 Harness 建立了动态闭环**，两者共同支撑 Agent 从演示走向可持久、可扩展的生产级应用 [[4]，[6]。  

例如，Agent 在14小时自主完成 eGPU 驱动项目，正是 Loop Engineering 与 Harness 精准协同的结果 [5]。

### U2（11.4s｜covered=[1, 2, 5]｜hallucinated=True）

二者是“架构公式”和“演进方法”的互补关系：

- **Agent = Model + Harness 定义架构边界**：Model 负责思考、推理、决策；Harness 负责循环控制、工具路由、上下文管理；再配合 Session 持久化日志、Sandbox 执行环境 [1][4]。它回答的是 Agent“由什么组成、接口如何稳定”。
- **Loop Engineering 定义循环与目标设计**：在工程分层中，Demmel 总结为 Prompt → Context → Feedback Loop → Harness Engineering，共同点是“人决定下一步做什么” [2][5][6]。而 `/goal` 打破了这一模式：人不再告诉 Agent 每一步，只告诉它终点线在哪 [2]。Loop Engineering 被表述为第五层，核心是“人定义终点线，Agent 找路径” [3]。
- **互相补充**：Harness 提供承载循环的执行框架——循环控制、工具路由、上下文管理、Session/Sandbox，使 Agent 能持续运行、自检和调用工具 [1][4]；Loop Engineering 则为 Harness 提供目标导向的循环设计，把 Harness Engineering 从“人设计 workflow、人在框架内执行”推进到“人给目标、Agent 自主循环找路径” [2][3]。

简言之：**Model + Harness 说明 Agent 是什么、靠什么运行；Loop Engineering 说明如何让这个 Harness 中的循环围绕目标自主运转。**

### U3（6.24s｜covered=[1, 2, 4, 5]｜hallucinated=False）

**一句话：Harness 提供底座，Loop 在其上加一层自治；不是替代关系。**

## 1. 层次关系：Loop 在 Harness 之上

文档B明确说：「Loop Engineering 不是替代 Harness——它在 Harness 之上加了一层自治。好的 Loop 需要好的 Harness 做底座。」并给出演进链：Prompt < Context < Feedback Loop < Harness < Loop（文档B）。

文档A的 `Agent = Model + Harness` 定义了 Agent 的最小构成——Model 思考、Harness 负责循环控制/工具路由/状态管理（文档A）。Loop Engineering 则把「谁决定下一步」从人交给 Agent：Harness 模式下人编排 workflow；Loop 模式下 Agent 根据当前状态自己决定（文档B）。

## 2. Harness 的组件正好是 Loop 的四个前提

文档B指出上 Loop 前需要四样东西，它们全部对应文档A的 Harness 组件：

| Loop 需要 | Harness 提供 | 来源 |
|---|---|---|
| 验证工具（表单校验/状态查询 API） | `Sandbox.execute(name, input) → string` 就是这些 API 的调用入口 | 文档A |
| 安全护栏（token budget、操作白名单） | 「Token 不可达」的结构性隔离（Git 初始化注入、MCP Vault 代理） | 文档A |
| 状态可观测（每轮决策日志、审计报告） | Session 持久化事件日志，append-only、可 `getEvents()` 按需读取 | 文档A |
| 优雅退出（卡住报 unmet 而非死循环） | 无状态 Harness + `wake(sessionId)` 崩溃恢复；Session 保证数据可恢复 | 文档A |

## 3. 互补点：Loop 缓解了 Harness 的「假设过时」困境

文档A的核心痛点是：Harness 编码了「模型做不到什么」的假设，模型变强后 workaround 变成死代码（如 context reset 在 Opus 4.5 上失效）。

文档B的差旅报销案例给出了解法：Harness 模式下人必须提前编排「项目编码为空 → 查历史记录」的分支；Loop 模式下 Agent 自己发现缺字段、自己去找——人只需提供校验 API 作为「Harness 的传感器组件」（文档B）。也就是说，**Loop 把异常路径的决策从「人预先写进 Harness 的假设」转移到 Agent 运行时自主判断**，天然对抗假设过时。

## 4. 反过来，Harness 的接口设计让 Loop 可迁移

文档A主张「接口稳定，实现可变」「Session 独立于上下文窗口」——这正好支撑文档B的 Loop 关键设计：每轮 fresh context，长期记忆放在 loop 里而不在 model 里（文档B）。Session 作为独立于上下文窗口的持久化对象（文档A），让 Loop 每轮的 fresh context 不丢状态；Sandbox/MCP 的稳定接口，让 Loop 换执行环境时不用改循环逻辑。

## 结论

- **Harness 解决「Agent 在什么框架内运行」**，定义稳定的接口、安全边界与持久化状态（文档A）；
- **Loop 解决「Agent 自己跑到终点」**，定义 done 与预算，把异常路径交给 Agent 自主处理（文档B）；
- 二者关系是**地基与上层**：先建 Harness，再上 Loop；没有 Harness 底座就上 Loop，等于「在没有护栏的高速公路上自动驾驶」（文档B）。


---

## S09 [fact] 解耦 Harness 和 Sandbox 后，TTFT 指标改善了多少？

- 期望文档：['de43b976f6666a44']
- 期望要点：['p50 TTFT 下降约 60%', 'p95 TTFT 下降超过 90%', '按需创建容器']

### U1（3.85s｜covered=[2]｜hallucinated=False）

解耦 Harness 和 Sandbox 后，TTFT（端到端响应时间）指标改善了 **超过90%** [2]。

### U2（3.31s｜covered=[2]｜hallucinated=False）

资料中没有明确说明「解耦 Harness 和 Sandbox 后 TTFT 改善了多少」这一对应关系。

只能看到片段数据：TTFT 为 95、下降 **>90%** [6]，但资料未完整呈现该数据对应的具体改动条件，因此无法确认它就是解耦 Harness 与 Sandbox 带来的结果。

### U3（1.65s｜covered=[1, 2, 3]｜hallucinated=False）

根据文档A（178 Agent=Model+Harness），解耦后 TTFT 改善为：

- **p50 TTFT：下降约 60%**
- **p95 TTFT：下降超过 90%**

原因是耦合架构中每个 Session 都必须先完成完整容器初始化（克隆代码库、启动进程等），即使不需要 Sandbox 也要等待；解耦后容器仅在需要时才创建。

（来源：文档A《Agent = Model + Harness》）


---

## S10 [fact] 钉钉差旅报销 Loop 案例的投入产出比是多少？

- 期望文档：['78d89a53782ce92b']
- 期望要点：['约 200k tokens', '¥1.2', '¥25', '约 20 倍回报']

### U1（11.02s｜covered=[1, 2, 3, 4]｜hallucinated=True）

根据资料，钉钉差旅报销 Loop 的投入产出比约为 **20 倍回报**。

具体而言，Agent 每次迭代调用钉钉校验 API 的成本为 ¥1.2（约 200k tokens），而通过 Loop 机制将验证成本降至接近零，用户节省了约 15 分钟（约 ¥25，按 ¥100/小时估算），从而实现 ¥1.2 换 ¥25 的回报，约为 20 倍 [6]。

### U2（3.62s｜covered=[1, 2, 3, 4]｜hallucinated=False）

钉钉差旅报销 Loop 案例的投入产出比约为 **20 倍回报** [3]。

依据资料中的估算：Token 成本约 ¥1.2（约 200k tokens），用户价值为省 15 分钟 ≈ ¥25（按 ¥100/小时估算），即 ¥1.2 换 ¥25 [3]。资料同时指出，这一回报不是因为 model 变强，而是因为 loop 把验证成本降到了接近零——钉钉的校验 API 是现成的，Agent 每跑一步都能免费检查 [3]。此外该案例的打回率从约 15%（经验估算）降至 <3%（校验驱动）[3]。

注：以上为资料给出的估算值，非精确测量结果 [3]。

### U3（1.45s｜covered=[1, 2, 3, 4]｜hallucinated=False）

钉钉差旅报销 Loop 案例中，Token 成本约 ¥1.2（约 200k tokens），用户价值约省 15 分钟 ≈ ¥25（按 ¥100/小时估算），投入产出比约为 **20 倍回报**（资料中标注为基于估算）。（来源：文档B《Loop Engineering：AI Agent 工程的第五层》）


---

## S11 [multihop] Codex 用什么机制防止 proxy signal collapse？

- 期望文档：['78d89a53782ce92b']
- 期望要点：['continuation.md 模板', '必须 inspect actual files / run actual tests / verify actual output', 'update_goal 是结构化 tool call', '只有每项直接验证通过才 update_goal(achieved)']

### U1（17.98s｜covered=[1, 2, 4]｜hallucinated=True）

Codex 通过 **审计优先的完成机制**（audit-first completion）防止 proxy signal collapse，即在每个迭代中必须直接验证实际文件、实际测试结果和实际输出，而非仅依赖表面信号（如“答案正确”或“测试通过”）[2]。  
具体而言，Agent 必须：  
- 检查实际文件  
- 运行实际测试  
- 验证实际输出  
只有当所有检查项均通过直接验证后，才能调用 `update_goal(achieved)` [3]。  
这一机制确保了“表面信号”不被误认为完成，从而避免代理信号坍塌 [2]。  

此外，该机制依赖 **每轮使用 fresh context**，以防止上下文腐烂（context rot）和目标漂移（goal drift），并保障状态的精确性 [2]。  

因此，Codex 的 `continuation.md` 模板明确禁止接受 proxy 信号作为完成，强制要求对实际内容进行验证 [3]。

### U2（4.97s｜covered=[1, 2, 4]｜hallucinated=False）

Codex 主要靠 **audit-first completion（审计优先的完成判定）** 来对抗 proxy signal collapse [3]。

具体落地机制是每轮注入 Agent system prompt 的 `continuation.md` 模板，规则包括 [2]：

- 不接受 proxy signal 作为完成
- 必须 inspect actual files（检查真实文件）
- 必须 run actual tests（跑真实测试）
- 必须 verify actual output（验证真实输出）
- 只有每个 checklist item 都通过**直接验证**，才能调用 `update_goal(achieved)`

要防的问题是：表面信号不等于真实完成——比如「答案正确」不代表「解题过程正确」[5]，「测试通过」可能只跑了单元测试没跑集成测试，「代码已提交」可能提交到了错误分支 [2]。所以它强制用直接验证代替代理信号，且把这一判定作为完成状态的唯一入口 [2][3]。

### U3（2.28s｜covered=[1, 2, 3, 4]｜hallucinated=True）

Codex 通过 `continuation.md` 模板（每轮注入 Agent 的 system prompt）来防止 proxy signal collapse，要求：

- 不接受 proxy signal 作为完成
- 必须 inspect actual files
- 必须 run actual tests
- 必须 verify actual output
- 只有每个 checklist item 都通过直接验证，才能调用 `update_goal(achieved)`

此外，`update_goal` 被设计为结构化的 tool call（而非文本声明），五个状态都有明确语义。（来源：文档B）


---

## S13 [negative] 如何用 Airflow 配置 DAG 的调度重试策略？

- 期望文档：[]
- 期望要点：[]

### U1（3.68s｜covered=None｜hallucinated=False）

资料中未提及 Airflow 配置 DAG 的调度重试策略，无法回答。

### U2（4.38s｜covered=None｜hallucinated=False）

资料中没有关于 Airflow 或 DAG 调度重试策略的任何内容。提供的 6 段资料 [1]–[6] 均围绕 Agent 的四个基本组件、Git Token 初始化注入、MCP + Vault 凭证代理等主题展开，未涉及 Airflow 的 DAG 配置。

因此无法依据现有资料回答该问题。如需 Airflow 重试策略（如 `retries`、`retry_delay`、`retry_exponential_backoff`、`max_retry_delay` 等参数）的说明，建议查阅 Airflow 官方文档或相关专门资料。

### U3（1.7s｜covered=None｜hallucinated=False）

资料中没有关于 Airflow 或 DAG 调度重试策略的任何内容。

现有两份文档分别讨论的是 Agent 架构（Model + Harness，文档A）和 Loop Engineering（文档B），均未涉及 Airflow 的配置方法。无法据此回答该问题，建议查阅 Airflow 官方文档。


---

## S14 [negative] Transformer 多头注意力机制的计算复杂度是 O(n²·d) 吗？

- 期望文档：[]
- 期望要点：[]

### U1（5.14s｜covered=None｜hallucinated=False）

没有。所提供的资料未提及Transformer多头注意力机制的计算复杂度，无法判断其是否为O(n²·d) [无相关资料]。

### U2（4.07s｜covered=None｜hallucinated=False）

资料中没有关于 Transformer 多头注意力机制或计算复杂度的任何内容，无法据此回答该问题 [1][2][3][4][5][6]。建议查阅相关的模型架构资料。

### U3（1.19s｜covered=None｜hallucinated=False）

资料中没有关于 Transformer 多头注意力计算复杂度的内容，无法据此回答。
