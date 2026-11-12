# codex-harness-internals

**语言：** [English](./README.md) · [Русский](./README.ru.md) · 简体中文

这是一份通过逆向分析整理的 OpenAI Codex CLI 智能体运行框架（harness）规范，涵盖智能体循环、工具注册表、上下文压缩和上下文管理的机制。内容提取自源代码仓库，并组织成可导航的知识图谱，面向构建 AI 智能体或为其编写指令的工程师。

> **译者说明：** 本文是社区整理的非官方逆向工程资料，不是 OpenAI 官方规范，也不保证适用于当前版本。数值和行为描述反映本仓库保存的分析快照；实际行为请以所用 Codex 版本的源码与配置为准。提示词中的文字规则不能替代执行策略、沙箱或审批机制，也不应为了减少提示词长度而扩大实际权限。下文对原文的安全建议和统计数据所作修正均明确标注。

> 可配合 OpenAI 博客系列 *《Unrolling the Codex agent loop》* / *《Harness engineering》* / *《Unlocking the Codex harness》* 阅读。


## 中文阅读入口与翻译范围

- [18 个模块中文导读](./docs/zh-CN/README.md)：逐模块介绍职责、关键结构、规则与流程。
- [中文查询指南](./HOW-TO-QUERY.zh-CN.md)：可直接运行的 `jq` 查询和图导航示例。
- 本文提供英文 README 的完整中文版本，并明确标注必要的译者修正。模块导读不是逐条译本；原始 JSON 的俄文/英文叙述尚未逐字段翻译，ID、字段和机器可读数据保持不变。

---

## 指令会被静默截断的 5 个位置

大多数指令作者并不知道，harness 在自身的提示词组装逻辑中设置了严格的数值限制。截断悄然发生，没有警告，也没有报错。以下是实际发生截断的五个位置。

**1. 5 000 字节：已批准命令前缀渲染文本的上限。** 当你通过允许的前缀列表（如 `git commit`、`npm install` 等）定义 shell 命令审批策略时，harness 会将它们渲染为文本块，放入开发者指令。一旦文本超过 5 000 字节，就会在最后一个合法的 UTF-8 字符处截断，并在末尾追加标记 `...[Some commands were truncated]`。模型看到的列表将不完整，而你不会收到通知。**应对方法（译者修正）：** 保持实际执行策略的最小权限，检查前缀列表的渲染结果；仅在不扩大授权范围时合并冗余项。可在 `base_instructions` 中补充关键规则的文字说明，但不能用它替代实际执行策略。原文建议用 `git *` 合并命令前缀，这可能扩大权限，不宜作为通用做法。

**2. 100 项：前缀列表的条目数上限。** 即使字节数低于 5 000，harness 仍会先按 `(length, combined length, alphabetical)`（长度、组合长度、字母顺序）排序，再最多取 100 个前缀。其余条目不会展示。**译者修正：** 按 `SRC-0001/rule/prefix-list-rendering-limits`，超过条目或字节限制都会追加截断标记；这不等于单独向用户发出告警。**应对方法（译者修正）：** 如果规则超过 100 条，应审查策略设计与提示词渲染方式。可以在 `base_instructions` 中提供简洁的声明式说明，但实际授权仍须由执行策略和审批机制强制实施，不能仅以文字说明替代。

**3. 20 000 token：上下文压缩后的用户消息预算。** harness 执行自动上下文压缩时，只保留最近的用户消息，总量以能装入 20 000 token 为限。更早的内容都会融入一段摘要。如果你在十几条用户消息中逐步“展开”规则，压缩后这组消息的开头就会消失。**应对方法：** 将规则骨架放在 `base_instructions` 中（每轮都会重建），并明确逐步展开的细节只能保留到下一次上下文压缩。

**4. 上下文窗口的 90%：自动上下文压缩阈值。** 当总 token 用量达到或超过所配置的自动压缩阈值（默认由模型上下文窗口大小的 9/10 推导） 时，harness 会自行触发上下文压缩。压缩后，所有开发者消息都会被完整丢弃，因为 `should_keep_compacted_history_item` 过滤器将其归类为“过时或重复的指令”。这意味着：harness 通过开发者消息为某一轮注入的规则（审批策略、沙箱指令、屏幕环境），在第一次上下文压缩后就不再存在于历史记录中。**应对方法：** 不要依赖开发者指令被“记住”；重要规则应放入 `base_instructions`。

**5. 1 048 576 个字符：用户输入的硬性上限。** （译者说明：原文标题写作 1 MiB，但仓库中的常量为 `MAX_USER_INPUT_TEXT_CHARS`，因此这里按字符数表述，不能将多字节中文的字符数等同于 UTF-8 字节数。）任何超过 1 048 576 个字符的用户输入，都会在到达模型前被 harness 拒绝。对于文本提问，这个额度很大；但如果你用程序拼接大块内容（日志文件、diff、数据转储），就很容易达到上限。**应对方法：** 在自己的程序中预先截断大段插入内容，不要指望 harness “自行想办法”。

---

## 决定 harness 行为的 5 个参数

对 harness 工程师来说，重要的不是“81 种事件类型”，而是决定其与客户端之间契约的参数值。

**1. 提交通道：容量为 512。** harness 通过一个固定容量为 512 的通道接收客户端操作。通道填满后，客户端会阻塞，产生背压。如果你正在构建自己的编排器，应有意识地选择对应参数：太小会造成交互卡顿，太大则会掩盖处理缓慢的问题。Codex 选择 512，是在响应速度与防止高负载轮次“洪水”之间取得平衡。

**2. 智能体任务并发数：默认 16，最多 64。** 当 harness 执行 CSV-spawn（根据表格并行创建子智能体）时，默认并行运行 16 个 worker，硬性上限为 64。这个上限不是技术约束，而是成本约束：每个活跃子智能体都有自己的轮次和 token 消耗。如果你的 harness 没有类似上限，客户端可能无意间启动数百个并行智能体，造成意外的高额账单。

**3. 锁重试：最多 10 次，间隔 100 ms。** 会话历史在持锁状态下写入 rollout 文件；发生冲突时，harness 会以 100 ms 的休眠间隔重试获取锁，最多 10 次。这是为支持同一会话中的多个并发进程（例如 CLI 与 VS Code 扩展）而做出的设计选择。如果你正在编写自己的持久化层，需要注意：类似方案如果没有重试，第一次并发访问就会失败。

**4. 恢复会话时的最大扫描文件数：10 000。** harness 从 rollout 归档恢复会话时，最多扫描 10 000 个文件。这限定了通过恢复操作可以“回溯”的历史深度。对 harness 工程师而言，这项限制关注的是启动时的资源消耗，而不是功能本身；不设上限的扫描会让客户端启动延迟达到数分钟。

**5. 等待超时：10 秒到 1 小时。** 对于阻塞式工具调用（wait 操作），harness 将超时规范在 10s–3600s 范围内。**译者修正：** `SRC-0017/rule/wait-timeout-clamp` 表明，不大于 0 的值才被拒绝；正数会钳制到 10 秒至 1 小时，未设置时默认 30 秒。如果工具注册表接受模型提供的任意超时时间，就可能引发 DoS（模型要求永远等待），或“抖动”（模型每秒调用一次 wait）。

---

## 适合谁阅读

### 智能体指令与提示词作者（主要读者）

你编写系统提示词、AGENTS.md、规则集和技能包，希望了解 **harness 究竟如何将这些内容与历史记录组合，并传给模型**。本规范回答以下问题：

- 哪些指令能在上下文压缩后保留，哪些不能；
- 你的规则最终位于提示词中的什么位置；
- 哪些阈值会在模型看到文本前静默截断它；
- 如何通过提及技能来激活指令注入，而不让上下文膨胀；
- 为什么你的规则会在 N 轮后被“遗忘”。

### Harness 系统工程师

你正在构建自己的智能体循环，包括轮次循环、流式处理、工具分发和会话持久化，希望有一个参考实现。本规范提供：

- 完整的轮次运行机制：Item 生命周期、TurnItem → 旧版 EventMsg 映射、TTFT/TTFM 指标；
- 两条上下文压缩路径（内联摘要与 `/responses/compact` 端点）；
- 遇到 `ContextWindowExceeded` 时利用前缀缓存的重试策略；
- 线程的 fork / resume / rollback（分叉、恢复、回滚）语义。

---

## 规范包含什么

18 个 SRC 模块 × 3 个观察视角：

| 视角 | 内容 | 总量 |
|---|---|---|
| `rules.json` | 不变量、阈值、公式、GWT 示例（Given / When / Then） | **990 条规则** |
| `structural.json` | 实体、字段、关系、数据流 | **588 个结构** |
| `use-case.json` | 场景：步骤、Cockburn 扩展、边界情况、生命周期 | **325 个用例** |
| **总计** | | **1 903 个组合条目** |

> **译者说明（统计修正）：** 按本仓库当前快照的 JSON 组合条目计数，实际为 990 条规则、588 个结构和 325 个用例，共 1 903 个条目。英文原文的 1 028 条规则及约 1 941 个条目为旧统计。

**覆盖范围：** D1-protocol（类型、传输格式）→ D2-state（会话、线程、rollout）→ D3-agent（核心：spawn、轮次循环、上下文压缩）→ D4-context（轮次上下文、初始上下文、环境）→ D5-tools（注册表、分发、子智能体）。

**原文声明不在范围内：** 沙箱、安全、MCP 集成、hooks/plugins 内部实现、身份认证、分析统计、网络策略、模型提供方、CLI / TUI / IDE 适配端。

> 译者说明：这表示原文的覆盖目标，而非这些主题完全不存在。当前 JSON 仍包含沙箱、权限、MCP、网络审批和遥测的部分接口与行为，参见模块导读。

---

## Codex 做出了哪些不同的设计选择

这里不是罗列“代码中的术语”，而是介绍 Codex 团队做出的、区别于智能体行业常见实践的设计决策。每项都会说明：Codex 如何实现、通常的做法是什么，以及它为指令作者或 harness 工程师带来什么。筛选标准是：（1）这是一个存在明确替代方案的决策；（2）替代方案是业内常见做法（LangChain / AutoGen / Assistants API 的做法不同）；（3）这种差异会影响读者的实际做法，而不仅是内部细节。

### 1. 指令分为五层，各有不同的存续时间

**Codex 的做法。** 将指令拆成五个语义层，每层都有自己的生命周期：`base_instructions`（每轮重建）、`initial_context`（上下文压缩后重新注入）、`skill injections`（通过 @ 提及激活）、开发者消息（运行时策略）、用户消息（特定任务的内容）。

**常见做法。** LangChain / AutoGen / Assistants API 使用扁平的 `system prompt` + `messages[]`，整个线程生命周期共用一个系统提示词。指令要么一次性硬编码后不再变化，要么没有结构地“摊开”在用户消息中。

**给读者的价值。** 指令作者能够明确回答“规则应该放在哪里才能始终生效”。`base_instructions` 中的规则能在上下文压缩后保留，不依赖历史记录；开发者消息中的规则是易失的，会在第一次上下文压缩时消失；技能文件中的规则仅在需要时激活。这让你可以有意识地设计规则集，而不是寄希望于“模型会记住”。

### 2. 技能通过 @ 提及激活，是按需加载的指令

**Codex 的做法。** 模块化指令包（skills）默认不会进入提示词。只有当用户通过 `@skill-name` 显式提及技能（`collect_explicit_skill_mentions`），或 harness 在之前的轮次见过该技能（`implicit_invocation_seen_skills`）时，才会引入它。

**常见做法。** 典型智能体会预先将所有工具描述和所有规则加载进提示词。对于 5–10 个工具，这种方式可行；但达到 50 个工具时就会出问题：模型开始失去重点，提示词膨胀为数万 token 的固定开销。

**给读者的价值。** 你可以维护按主题划分、规模任意大的规则目录（`@testing-rules`、`@security-review`、`@refactor-style`），而每轮只看到真正需要的规则。基础提示词保持精简，只保留“何时调用哪个技能”的元规则。这是一种根本不同的指令扩展模式。

### 3. 上下文通过差异更新，而不是完整重新注入

**Codex 的做法。** 如果轮次之间环境没有变化，harness 就不会重新发送环境信息。为此，它保存了 `reference_context_item`，即上次发送的上下文快照。新一轮开始时，`build_environment_update_item` 将当前状态与参考快照比较，只将发生变化的字段包装在 `<environment_context>` 标记中，作为用户消息发出。

**常见做法。** 传统方式是在每次请求上附加完整的系统上下文。模型会连续几十次看到相同的 CWD、git 分支和文件系统信息。

**给读者的价值。** 节省静态规则所占的 token；更重要的是，模型不会在重复信息中“迷失”。如果每轮都重发相同的环境消息，模型会开始赋予它更高权重；差异更新则让上下文成为“只有真正发生变化时才出现的噪声”。通过标准 `<environment_context>` 片段放置环境信息的指令作者，可以自动获得这种节省。

### 4. 有上下文压缩，`/undo` 仍然有效

**Codex 的做法。** 压缩前，harness 会收集 ghost snapshots（幽灵快照），也就是历史记录的切片，并将它们追加到新压缩历史的末尾。**译者修正：** 快照通过独立追加恢复，不是由远程结果过滤器保留；该过滤器会丢弃远端返回的 GhostSnapshot。

**常见做法。** 大多数系统的上下文压缩不可逆：旧历史被摘要替换，无法回退。开启长会话的用户一旦遇到上下文压缩，就会失去“撤销”能力。

**给读者的价值。** 对 harness 工程师而言，这是一种上下文压缩设计模式：一个不变量（合理的提示词大小）不会破坏另一个不变量（回滚能力）。这项决策并不简单，需要额外的状态记录（为快照设置标记，并在过滤器中将其列入保留名单），但它为长会话提供了安全保障，用户不必害怕“做过头”而丢失进度。

### 5. 用户指令与框架辅助信息是**不同的东西**

**Codex 的做法。** harness 区分六种“上下文片段”，每种都有自己的 XML 标记：`<agents_md>`、`<environment_context>`、`<skill>`、`<user_shell_command>`、`<turn_aborted>`、`<subagent_notification>`。这些文本在形式上不算用户输入：回滚时不会被视为 `user_turn_boundary`，裁剪过程中会单独处理，其中一些（`AGENTS_MD`、`SKILL`）还会被排除在记忆生成之外。

**常见做法。** 所有 user 角色的文本都被同等对待：真实的用户命令、模板化指令和环境转储看起来都一样。因此，回滚到“上一条用户消息”可能会落到错误位置，因为那条“用户消息”其实是系统环境片段。

**给读者的价值。** 对指令作者而言，不能随意用一个 XML 标签包裹规则，就期待 harness 识别它们。如果使用 `<my_rules>` 包裹，它们仍是普通用户文本，会被优先裁剪。正确路径是将规则放入 `base_instructions`，或通过技能注入使用标准 `<skill>`。同样的契约也适用于 harness 工程师：如果你正在构建自己的智能体，“用户输入”与“服务侧插入内容”的区别应当成为一等概念，而不是一种注释约定。

### 6. 审查智能体有自己的策略，不继承调用方策略

**Codex 的做法。** Guardian 是专门审批 shell 命令的审查子智能体。它被创建时，会忽略从父级继承的执行策略，转而使用内置安全检查（`ExecPolicyManager::default`）。代码中明确规定了这一特殊情况，对应规则为 `guardian-reviewer-bypass-exec-policy`。

**常见做法。** 子智能体继承调用方策略。这很直观：如果主智能体被配置为信任 `git *`，子智能体也会信任。但对于审查者，这很危险：调用方可能借助采用其自定义规则的审查者，对自己的命令进行“自我批准”。

**给读者的价值。** 对 harness 工程师而言，这体现了“安全关键的子智能体必须在架构上隔离”的模式。这种区分不常见，却有明确原则：审查者不能被调用方的上下文“收买”。如果你正在构建带有自我审查能力的智能体系统，这一部分不能用“简单方式”实现，否则审查者的整个设计就会失去意义。Codex 直接将其编码为不变量。

---

## 如何使用：查询示例

下面两个示例展示了如何利用本规范回答实际问题。读者可以带着任意问题开始，再从各个 SRC 模块中组织出详尽、实质性的答案，无须提前熟悉规范文件。

> **用户的原始请求：**
>
> *“我想要两个基于这份规范的示例，请直接展示你如何构建它们：提出了什么问题，得到了什么结果。示例 1 的问题：解释 Codex 智能体最复杂、最重要的机制。示例 2 的问题：当一份指令包含大量规则时，帮我解决注意力预算衰减的问题。请使用这份规范回答。”*
>
> **补充说明：**
>
> *“示例 2 应面向指令作者。答案必须依据规范数据构建。还要附上产生这些答案的问题，否则示例就不够有说服力，必须展示如何使用规范。请把示例改写得更有教学性：少放些指向规范的链接，多展示其中的内容，让读者不必逐个点开链接也能理解要点。”*

---

### 示例 1：解释一个复杂机制

**简短回答：** Codex 最复杂的机制是对话历史的自动上下文压缩。它很重要，因为当上下文窗口填满时，它能让轮次循环继续运行；它很复杂，因为它结合了五项彼此独立的任务：检测是否接近上限、根据提供方选择实现、溢出时安全重试、维护历史记录的不变量，以及支持 `/undo`。

#### 何时触发

harness 将阈值设为**模型上下文窗口的 90%**，也允许通过配置进一步收紧（取较小值）：

```
auto_compact_token_limit = min(config_limit, context_window × 9/10)
```

如果两者都未设置，则禁用自动上下文压缩。压缩有三种触发模式：

- **轮次前（pre-turn）**：处理新的用户消息之前，如果此前的总用量已达到阈值；
- **轮次中（mid-turn）**：在当前轮次执行期间，模型即将达到窗口上限时；
- **手动（manual）**：用户执行 `/compact` 命令。

这种区别很重要：轮次中压缩会在当前轮次上下文上留下一个“锚点”，让下一轮按差异更新。手动压缩和轮次前压缩不设置锚点，下一轮会完整地重新注入上下文。

#### 两种实现：内联与远程

harness 首先询问提供方：“是否支持远程上下文压缩？”如果支持，就将整个历史记录发送到服务端端点 `/responses/compact`，由它返回已经压缩好的 `Vec<ResponseItem>`。如果不支持，harness 就执行内联摘要：注入自己的 `SUMMARIZATION_PROMPT`，运行一个普通的流式轮次，并取得摘要文本。

这个选择由 `provider.supports_remote_compaction()` 标志决定，并在开始时固定下来，不会在流式处理途中切换。

#### 窗口溢出时的重试循环（内联）

这是最棘手的部分。内联上下文压缩本身也要发起流式请求，并且可能收到模型返回的 `ContextWindowExceeded`，因为历史记录加上摘要指令组成的提示同样可能放不进窗口。harness 的处理方式如下：

- **历史记录包含多个条目时** → `history.remove_first_item()`、`truncated_count++`、`retries = 0`、`continue`。牺牲最旧的条目，以保留提供方的前缀缓存以及最近的消息（最新的用户指令通常就在其中）；
- **只剩一个或更少条目时** → `set_total_tokens_full`、发出 Error 事件、退出。连最小上下文都放不下，已经没有进一步的补救空间。

遇到其他错误（网络、速率限制）时，采用指数退避重试，最多重试 `provider.stream_max_retries()` 次。遇到 `Interrupted` / `TurnAborted` 时立即退出，不重试。预裁剪时，界面会显示：

> *“Trimmed N older thread item(s) before compacting...”*（在压缩前裁剪了 N 个较早的线程条目……）

#### 压缩之后，历史记录中究竟留下什么

harness 通过 `build_compacted_history_with_limit` 构建压缩后的历史：

1. 获取除摘要消息之外的所有用户消息（摘要消息通过前缀识别，即 `SUMMARY_PREFIX` 加上 `\n`；译者按用例 `SRC-0011/uc.sub/auto-compact-inline` 修正）。
2. 逆序遍历并收集消息，在 `COMPACT_USER_MESSAGE_MAX_TOKENS = 20 000` 的预算内保留最近内容；预算不足时，将最旧的保留消息截断到剩余预算并停止（译者按规则 `SRC-0011/rule/compact-user-message-token-budget` 修正）。
3. 组装最终列表：`initial_context` + 收集到的近期用户消息 + 位于最后的 `summary_user_message`。

如果 `summary_text` 为空，则用 `"(no summary available)"` 替代。

#### 初始上下文具体插在什么位置

Codex 模型在训练时，摘要位于历史记录的最后。harness 会保持这个不变量：

```
insertion_index =
  last_real_user_index          // 放在最后一条真实用户消息之前
  ?? last_user_or_summary_index // 若没有真实用户消息，则放在摘要之前
  ?? last_compaction_index      // 若两者都没有，则放在最后一个 Compaction 之前
                                // 否则追加到末尾
```

GWT 示例：

- **Given（给定）：** `compacted_history = [compaction_item, summary_user_message]`，`initial_context = [env_msg, dev_msg]`
- **When（当）：** 执行插入
- **Then（则）：** `last_real_user_index = None`（摘要不是真实用户消息），`last_user_or_summary_index = 1`，插入位置 = 1 → `[compaction_item, env_msg, dev_msg, summary_user_message]`

摘要仍位于尾部，契约得以保持。

#### 远程预裁剪：什么可以删，什么不能删

发送到远程压缩之前，harness 会通过 `estimate_token_count_with_base_instructions` 估算 token 数。如果预计会超过窗口，就裁剪历史记录的尾部，但仅在最后一个条目属于 `codex-generated` 时继续：

- `FunctionCallOutput` / `ToolSearchOutput` / `CustomToolCallOutput`：可以裁剪
-开发者 Message：可以裁剪
- 用户 / 助手 Message：**break**，不跨越这一边界

不变量：移除工具输出时，还会通过 `normalize::remove_corresponding_for` 移除与之配对的工具调用，否则模型会收到一个没有响应的“孤立”调用。

#### 幽灵快照：压缩后仍保留 `/undo`

压缩之前，harness 会收集 `GhostSnapshot` 条目，即用于回滚的快照。新的 `compacted_history` 组装完成后，这些快照会被追加到末尾，（译者修正：这是独立追加步骤，而非远程结果过滤器的保留项）。否则，压缩之后就无法执行 `/undo`。

> 译者说明：这里指本地保存并另行追加的快照；下一节过滤的是远程端点返回的条目，两者不是同一步操作。

#### 哪些内容能通过 `should_keep_compacted_history_item` 过滤器（远程）

远程端点可能返回任意类型的条目，harness 会进行过滤：

**丢弃：**开发者消息（过时或重复的指令）、不是 UserMessage/HookPrompt 的用户角色消息（会话前缀包装内容）、Reasoning、FunctionCall/Output、ToolSearchCall/Output、CustomToolCall/Output、LocalShellCall、WebSearchCall、ImageGenerationCall、GhostSnapshot、Other。

**保留：** 助手消息、Compaction 条目、用户角色的警告，以及摘要消息。

实际启示：指令作者藏在“一次性”开发者注入中的任何内容，都会在上下文压缩后被丢弃。

#### 遥测与功能开关

所有上下文压缩事件都使用硬编码的 `strategy = CompactionStrategy::Memento` 发出。无论真实实现是什么，分析侧看到的都是这个常量。事件发送受 `Feature::GeneralAnalytics` 控制：在 `CompactionAnalyticsAttempt::begin` 中对该标志取快照，`track()` 中不会再次检查。因此，在轮次中途关闭分析，不会取消已经开始的事件。

收尾逻辑会将 `Result` 转换为 `CompactionStatus`：

- `Ok` → `Completed`
- `Err(Interrupted)` / `Err(TurnAborted)` → `Interrupted`
- 其他 `Err` → `Failed`

#### 指令作者需要关注的一项用户体验副作用

内联上下文压缩完成时，harness 会弹出警告：

> *“Heads up: Long threads and multiple compactions can cause the model to be less accurate. Start a new thread when possible to keep threads small and targeted.”*（提醒：过长的线程和多次压缩可能降低模型的准确性。条件允许时请开启新线程，让线程保持简短且聚焦。）

这是一个退化信号：每一轮新的上下文压缩都会再丢失一层上下文，因为开发者指令无法通过过滤，而摘要已经是“对转述的再次转述”。因此，系统指令应放在每轮重建的 `base_instructions` 中，而不是放在运行时的开发者注入中。

#### 为什么它是“最复杂”的机制

单条执行路径会跨越六项彼此独立的不变量：

1. token 计算（`estimate_tokens_with_base_instructions`）
2. 提供方检测（远程或内联）
3. 带退避和前缀缓存的网络重试
4. 历史记录不变量（调用与输出配对、用户与助手的边界、摘要位于最后）
5. 回滚（幽灵快照）
6. 功能开关与分析状态机

其中任何一处出错，都可能导致上下文内存耗尽（OOM），或模型质量悄然下降。

---

### 示例 2：解决规则密集型指令中的注意力衰减

**简短回答：** harness 已经在应对“规则很多、窗口有限”的问题，但它不会替把规则放在“易失”区域的指令作者兜底。如果规则放错位置，模型就会在没有提示的情况下收到截断版本。下面是六条实用规则，每条都附有机制说明和具体数值。

#### 规则 1：保持在上下文窗口的 90% 以下

当用量达到有效阈值（默认由 `context_window × 9/10` 推导）时，harness 会检查自动压缩。**译者修正：** 轮中触发条件是 `used_tokens >= limit` 且 `needs_follow_up`，配置还能收紧阈值，参见 `SRC-0009/rule/auto-compact-trigger-midturn`。压缩后：

- 只保留最近**合计 20 000 tokens** 以内的用户消息（常量 `COMPACT_USER_MESSAGE_MAX_TOKENS`）；
- 更早的内容都会融入摘要；
-开发者消息（包括承载你所写规则的消息）会被视为“过时或重复的指令”，**整条丢弃**。

**实践建议：** 如果窗口为 200k tokens，默认阈值就是 180k；应在达到该阈值前处理上下文，且实际配置可以更低。如果你在十几条用户消息中逐步“展开”规则，这一系列消息的开头会在第一次上下文压缩后消失。

#### 规则 2：骨干规则只应放在 `base_instructions` 中

harness 按以下优先级解析 `base_instructions`：

```
base_instructions =
    config.base_instructions                         // 显式覆盖
    ?? conversation_history.get_base_instructions()  // 来自 rollout 的 session_meta
    ?? model_info.get_model_instructions(personality) // 模型默认值
```

关键性质：**`base_instructions` 每个轮次都会重新构建**。它们不存放在历史记录中，而是通过独立的请求字段作为系统指令传入。因此，它们能在上下文压缩后继续存在，不依赖于上下文中还剩下哪一段历史。

**实践建议：**

- 行为不变量、回复风格、硬性规则 → `base_instructions`；
- 运行时策略（沙箱、审批）→开发者消息（由 harness 放置）；
- 任务特定细节 → 用户消息（最近 20k tokens 会在压缩后保留）。

#### 规则 3：已批准命令的前缀列表有硬性限制

如果你通过前缀列表配置 shell 命令审批策略，harness 会将其渲染为开发者指令，并执行两种截断：

- **字节限制：** `MAX_ALLOW_PREFIX_TEXT_BYTES = 5 000`。超出时，文本会在最后一个有效 UTF-8 字符处截断，并追加标记 `...[Some commands were truncated]`。
- **条目限制：** `MAX_RENDERED_PREFIXES = 100`。按 `(len, combined_str_len, alphabetical)` 排序后，仅取前 100 项。其余条目根本不会进入提示词。

如果你列出 200 条允许执行的命令，模型只会看到 100 条。具体是哪 100 条取决于上述完整排序规则。**译者修正：** 超过任一上限都会追加截断标记，不应解读为完全没有截断提示。

**应对办法：**

- **译者修正：** 仅在不扩大权限时去除冗余前缀，不要为缩短列表而授予 `git *` 等更宽权限；
- 可在 `base_instructions` 中补充关键禁令的文字说明，但实际执行策略（exec policy）仍须保留，文字不能替代权限控制。

#### 规则 4：用 skill 提及替代臃肿的系统提示词

harness 支持按需注入 skill 指令。单个轮次内的顺序如下：

1. `collect_explicit_skill_mentions(user_input)`：在用户文本中查找对 skill 的 `@` 提及；
2. `resolve_skill_dependencies_for_turn`：检查这些 skill 所需的环境变量；
3. `build_skill_injections`：**仅为被提及的** skill 组装指令；
4. `record_conversation_items`：将它们加入当前轮次的历史记录。

你可以维护几十个包含详细规则的 skill，但只有用户显式调用的 skill 才会进入提示词。这是避免大型规则集长期占据上下文的直接方法。

**实践建议：** 按主题将规则集拆分为模块（`@testing-rules`、`@security-review`、`@refactoring-style`）。基础提示词只保留“何时调用哪个 skill”的元规则。当前轮次按需加载细节。

#### 规则 5：遵循上下文片段标记

harness 区分普通用户文本与上下文片段，后者有六种严格的包装标签：

- `<agents_md>...</agents_md>`（`AGENTS_MD_FRAGMENT`）
- `<environment_context>...</environment_context>`
- `<skill>...</skill>`（`SKILL_FRAGMENT`）
- `<user_shell_command>...</user_shell_command>`
- `<turn_aborted>...</turn_aborted>`
- `<subagent_notification>...</subagent_notification>`

在历史规范化和压缩前裁剪期间，harness 会特殊处理这些标记：它将其识别为“有语义的”内容，并采用不同的裁剪方式。环境片段和子智能体通知片段会保留在记忆生成的输入中。

**如果用自己定义的 XML 标签，例如 `<my_rules>...`，包装规则会怎样？** harness 会将它视为普通用户文本并优先裁剪，不关心其中内容。想让规则保留下来？要么使用 `base_instructions`，要么通过 skill 注入使用标准的 `SKILL_FRAGMENT`。

#### 规则 6：初始上下文会注入到特定位置

在轮次中途进行上下文压缩后，harness 按以下优先级插入 `initial_context`：

1. 放在最后一条真正的用户消息（不是摘要）之前；
2. 如果没有，就放在最后一个用户类型的条目（包括摘要）之前；
3. 如果连这样的条目也没有，就放在最后一个 Compaction 条目之前；
4. 否则，追加到末尾。

模型最终看到的顺序是 `[..., initial_context, last_real_user, summary]` 或 `[..., initial_context, summary]`。摘要始终在最后，这是与经过训练的模型之间的约定。

**实践建议：** 不要试图把内容放在最后一条用户消息“之后”，因为 harness 仍会重新调整顺序。把上下文规则放进 initial_context 块（env、agents.md）中，而不是作为用户提醒追加到历史末尾；上下文压缩之后，后者就不在原位了。

#### 正确放置规则的模板

```text
base_instructions  ← 不变量：风格、安全、行为边界
                     （每个轮次重新构建）

initial_context    ← env、AGENTS.md、持久化的 skill 元数据
                     （上下文压缩后重新注入）

skill injections   ← 模块化的详细规则集，由 @ 提及激活
                     （不常驻上下文）

developer messages ← 运行时策略（审批 / 沙箱）
                     （易失；其中前缀展示块有 5 000 字节 / 100 项上限；
                       静默截断；上下文压缩时丢弃）

user messages      ← 任务特定内容
                     （最近 20 000 tokens 在上下文压缩后保留）
```

#### 如何验证规则是否“到达”模型

- **经压缩后仍然存在** → 它位于 `base_instructions`、initial_context 或最近 20k 用户 tokens 之内；
- **没有被静默截断** →开发者指令中的前缀列表不超过 5 000 字节，且不超过 100 项；
- **出现在正确的上下文中** → 用标准 `SKILL_FRAGMENT` 包装，并通过提及激活。

只要违反其中一条，模型读到的就是你的指令的裁剪版本，而大多数情况下没有人会察觉。

---

## 结构与导航

```text
./
├── SRC-0001/ ... SRC-0018/        # 18 个模块，每个模块包含：
│   ├── rules.json                 #   不变量与公式
│   ├── structural.json            #   实体与关系
│   └── use-case.json              #   场景
├── scaffold_index.json            # 各 SRC 中组合条目 ID 的扁平索引
├── graph.json                     # 边：triggers / depends_on / contains
└── README.md
```

组合条目通过 `→triggers SRC-0011/uc.sub/auto-compact-inline` 这样的引用相互连接。你可以借助 `scaffold_index.json` 和 `graph.json` 在它们之间导航。

规范查询方法（grep + jq 示例）参见[中文查询指南](./HOW-TO-QUERY.zh-CN.md)。

---

## 关键词

`openai codex harness` · `codex cli internals` · `codex agent loop` · `harness engineering` · `agent conversation turn lifecycle` · `tool registry` · `thread turn item` · `context compaction` · `auto-compact` · `responses compact endpoint` · `codex app server` · `prompt engineering reference` · `AI agent architecture spec` · `codex reverse engineering` · `base_instructions priority` · `initial context injection` · `SUMMARIZATION_PROMPT` · `CompactionStrategy::Memento` · `skill injection` · `prompt cache key` · `context window management`

以知识图谱形式提取的 Codex harness（OpenAI Codex CLI）参考规范：18 个 SRC 模块中的规则、用例与结构实体，覆盖协议、会话状态、智能体核心、轮次上下文和工具注册表。它是 OpenAI《Unrolling the Codex agent loop》系列文章的配套资料。适用场景包括编写智能体指令、构建自己的智能体 harness、逆向分析上下文压缩策略，以及理解轮次生命周期。

---

## 喜欢这份工作吗？

如果这份规范对你有帮助，欢迎**给仓库点个 Star**。这能帮助其他智能体开发者和指令作者通过 GitHub 搜索找到它。

欢迎在 Issues 中反馈意见，或提出扩大覆盖范围的建议，例如增加 SRC 模块、细化公式、补充遗漏的边界情况。
