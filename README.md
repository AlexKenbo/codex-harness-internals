# codex-harness-internals

**Languages:** English · [Русский](./README.ru.md)

Reverse-engineered specification of the OpenAI Codex CLI harness — the mechanics of the agent loop, tools registry, compaction, and context management. Extracted from the source repository and organized as a navigable knowledge graph for engineers building AI agents or writing instructions for them.

> Companion to the OpenAI blog series *«Unrolling the Codex agent loop»* / *«Harness engineering»* / *«Unlocking the Codex harness»*.

---

## 5 places where your instructions get silently truncated

Most instruction authors don't realize that the harness applies hard numeric cuts inside its own prompt-assembly logic. Truncation happens quietly — no warning, no error. Here are the five places where it actually occurs.

**1. 5 000 bytes — the limit on the rendered approved command prefixes text.** When you define a shell-command approval policy through a list of allowed prefixes (`git commit`, `npm install`, etc.), the harness renders them into the developer instructions as a text block. As soon as this text exceeds 5 000 bytes, it gets truncated at the last valid UTF-8 character and a marker `...[Some commands were truncated]` is appended. The model will see an incomplete list — and you won't know about it. **What to do:** group prefixes (`git *` instead of five separate `git commit`, `git push`, …), or move critical rules into `base_instructions` as plain text.

**2. 100 entries — the limit on the number of items in the prefix list.** Even if the byte count is below 5 000, the harness still takes at most 100 prefixes after sorting by `(length, combined length, alphabetical)`. The rest are dropped without warning. **What to do:** if you have more than 100 rules, it's no longer a prefix list — it's a policy, and it should be expressed declaratively in `base_instructions`, not as an enumeration.

**3. 20 000 tokens — the user-message budget after compaction.** When the harness runs auto-compaction, it keeps only the most recent user messages — exactly as many as fit into 20 000 tokens in total. Everything older dissolves into a single summary paragraph. If you were "unfolding" rules sequentially across a dozen user messages, the start of that series will disappear after compaction. **What to do:** keep the rule scaffold in `base_instructions` (they're rebuilt every turn), and understand that step-by-step elaboration only lives until the next compaction.

**4. 90% of the context window — the auto-compaction threshold.** The harness triggers compaction on its own when total token usage exceeds 9/10 of the model's context window size. After compaction, all developer messages are discarded entirely — the `should_keep_compacted_history_item` filter classifies them as "stale/duplicated instructions". This means: the rules the harness injected via developer messages for a single turn (approval policy, sandbox instructions, screen environment) will no longer exist in history after the first compaction. **What to do:** don't rely on a developer-instruction being "remembered"; if a rule matters, it belongs in `base_instructions`.

**5. 1 MiB — hard limit on user input.** Any user input longer than 1 048 576 characters is rejected by the harness before it reaches the model. That's a lot for text queries, but easy to hit if you programmatically splice large chunks (log files, diffs, dumps). **What to do:** pre-truncate large inserts on your side — don't count on the harness "figuring something out".

---

## 5 parameters that define harness behavior

For a harness engineer, it's not "81 event types" that matter, but the values that define the contract with your client.

**1. Submission channel — capacity 512.** The harness receives operations from the client through a channel with a fixed size of 512. When the channel fills, the client blocks (backpressure). If you're building your own orchestrator, an equivalent parameter must be chosen deliberately: too small causes UX stalls, too large masks slow processing. Codex picked 512 as a tradeoff between responsiveness and protection from heavy-turn "flooding".

**2. Agent job concurrency — default 16, max 64.** When the harness runs CSV-spawn (parallel sub-agents from a table), the default is 16 parallel workers and the hard max is 64. This cap isn't a technical constraint — it's about cost: every active sub-agent holds its own turn with its own token consumption. If your harness has no equivalent cap, a client can unintentionally launch hundreds of parallel agents and trigger billing shock.

**3. Lock retry — 10 attempts at 100 ms intervals.** Session history is written to the rollout file under a lock; on conflict the harness retries the lock up to 10 times with 100 ms sleeps. This is a design choice for supporting multiple concurrent processes in one session (e.g. CLI + VS Code extension). If you're writing your own persistence layer, note that a similar scheme without retries falls apart on the first concurrent access.

**4. Max scan files on resume — 10 000.** When the harness restores a session from the rollout archive, it scans at most 10 000 files. This caps the depth of history that can be "rewound" through resume. For harness engineers, the same limit is about startup resources rather than functionality; an uncapped scan leads to minutes of latency on client startup.

**5. Wait timeout — from 10 seconds to 1 hour.** For blocking tool calls (wait operations), the harness normalizes timeout into the 10s–3600s range. Below 10s is rejected (protection against busy loops), above one hour is also rejected (protection against stuck sessions). If your tool registry accepts arbitrary timeouts from the model, you risk either DoS (the model asks to wait forever) or "jitter" (the model calling wait every second).

---

## Who this is for

### Agent instruction and prompt authors (primary audience)

You write system prompts, AGENTS.md, rule sets, and skill bundles — and want to know **how the harness actually combines all that with history and passes it to the model**. The spec answers:

- which instructions survive compaction and which don't;
- where your rules physically end up in the final prompt;
- which thresholds silently cut your text before the model sees it;
- how skill mentions activate injection without bloating context;
- why exactly your rules get "forgotten" after N turns.

### Harness system engineers

You're building your own agent loop — turn loop, streaming, tool dispatch, session persistence — and want a reference implementation. The spec provides:

- the full turn machinery: Item lifecycle, TurnItem → legacy EventMsg mapping, TTFT/TTFM metrics;
- the two-path compaction (inline summarization vs the `/responses/compact` endpoint);
- the retry policy with prefix cache on `ContextWindowExceeded`;
- fork / resume / rollback semantics for threads.

---

## What the spec contains

18 SRC modules × 3 lenses:

| Lens | What | Total |
|---|---|---|
| `rules.json` | Invariants, thresholds, formulas, GWT examples (Given / When / Then) | **1 028 rules** |
| `structural.json` | Entities, fields, relations, data flows | **588 structures** |
| `use-case.json` | Scenarios: steps, Cockburn extensions, edge cases, lifecycle | **325 use-cases** |
| **Total** | | **≈ 1 941 compositions** |

**Coverage range:** D1-protocol (types, wire formats) → D2-state (sessions, threads, rollout) → D3-agent (core: spawn, turn loop, compaction) → D4-context (turn-context, initial context, env) → D5-tools (registry, dispatch, sub-agents).

**Out of scope:** sandbox, security, MCP integration, hooks/plugins internals, auth, analytics, network policy, model providers, CLI / TUI / IDE ports.

---

## What Codex decided differently

These are not "terms from the code" but decisions the Codex team made that set Codex apart from typical agent-industry practice. For each: what was done, what is usually done, and what it gives the instruction author or harness engineer. Selection criterion: (1) it's a decision with a visible alternative; (2) the alternative is typical in the industry (LangChain / AutoGen / Assistants API do it differently); (3) the difference changes audience behavior, not just an internal detail.

### 1. Instructions have five layers with different lifetimes

**What Codex did.** Split instructions into five semantic layers, each with its own lifecycle: `base_instructions` (rebuilt every turn), `initial_context` (re-injected after compaction), `skill injections` (activated by @-mentions), developer messages (runtime policy), user messages (task-specific content).

**What others typically do.** In LangChain / AutoGen / Assistants API there's a flat `system prompt` + `messages[]`, and a single system prompt for the entire thread lifetime. Instructions are either hardcoded once and forever, or "smeared" across user messages without structure.

**What it gives the audience.** The instruction author gets a clear answer to "where do I put a rule so it always works". A rule in `base_instructions` survives compaction and doesn't depend on history; a rule in a developer message is volatile and disappears at the first compaction; a rule in a skill file activates only when needed. This enables deliberate ruleset design instead of hoping "the model will remember".

### 2. Skills activate via @-mention — they are lazy instructions

**What Codex did.** Modular instruction bundles (skills) don't enter the prompt by default. They are pulled in only when the user explicitly mentions a skill via `@skill-name` (`collect_explicit_skill_mentions`) or when the harness has seen that skill in previous turns (`implicit_invocation_seen_skills`).

**What others typically do.** In a typical agent, all tool descriptions and all rules are loaded into the prompt up front. That works for 5–10 tools, but breaks at 50 — the model starts to lose focus, and the prompt swells into tens of thousands of tokens of constant overhead.

**What it gives the audience.** You can maintain an arbitrarily large rule catalog broken down by topic (`@testing-rules`, `@security-review`, `@refactor-style`), and each turn only sees the ones actually needed. The base prompt stays small — just meta-rules about "which skill to call when". This is a fundamentally different instruction-scaling model.

### 3. Context updates by diff, not full re-injection

**What Codex did.** When the environment hasn't changed between turns, the harness doesn't re-send it. For this it stores `reference_context_item` — a snapshot of the last sent context. On a new turn, `build_environment_update_item` compares the current state with the reference and emits a user message containing only the changed fields, wrapped in the `<environment_context>` marker.

**What others typically do.** The classic approach is to glue the full system context to every request. The model sees the same CWD, git branch, and filesystem dozens of times in a row.

**What it gives the audience.** Token savings on static rules — and, more importantly, the model doesn't get "lost" in repetition. When the same env message is resent every turn, the model starts giving it extra weight; diff updates turn the context into "noise that only arrives when something actually happens". An instruction author who places their environment info through the standard `<environment_context>` fragment gets this saving automatically.

### 4. Compaction exists, yet `/undo` still works

**What Codex did.** Before compaction, the harness collects ghost snapshots — slices of history — and appends them to the end of the new compacted history. The `should_keep_compacted_history_item` filter specifically preserves them even when everything else is dropped.

**What others typically do.** Compaction in most systems is irreversible: old history is replaced with a summary and there's no way back. A user who started a long session loses the ability to "undo" once compaction has happened.

**What it gives the audience.** For the harness engineer — a compaction design pattern where one invariant (reasonable prompt size) doesn't destroy another (ability to rollback). It's a non-trivial decision requiring extra bookkeeping (snapshots are flagged, the filter whitelists them), but the result is safety in long sessions, where the user isn't afraid to "overdo it" and lose progress.

### 5. User instructions and scaffolding are **different things**

**What Codex did.** The harness distinguishes six types of "contextual fragments", each with its own XML marker: `<agents_md>`, `<environment_context>`, `<skill>`, `<user_shell_command>`, `<turn_aborted>`, `<subagent_notification>`. These pieces of text formally don't count as user input — they are not treated as a `user_turn_boundary` during rollback, they are handled separately during trim passes, and some of them (`AGENTS_MD`, `SKILL`) are excluded from memory generation.

**What others typically do.** All user-role text is treated uniformly: a real user command, boilerplate instructions, and env dumps all look the same. As a result, rollback "to the last user message" may land in the wrong place because the "last user message" turns out to be a system env fragment.

**What it gives the audience.** For the instruction author — you cannot wrap your rules in an arbitrary XML tag and expect the harness to recognize them. If you wrap them in `<my_rules>`, they remain ordinary user-text and will be trimmed first. The correct path: either put them in `base_instructions`, or use the standard `<skill>` via skill injection. The same contract is useful to harness engineers: if you're building your own agent, the distinction between "user input vs service inserts" should be a first-class concept, not a commenting convention.

### 6. The reviewer agent has its own policy and does not inherit from the caller

**What Codex did.** When Guardian — the specialized reviewer sub-agent for shell-command approval — is spawned, it ignores the exec-policy inherited from the parent and uses built-in safety checks (`ExecPolicyManager::default`). This is an explicit case in the code: the rule `guardian-reviewer-bypass-exec-policy`.

**What others typically do.** Sub-agents inherit policy from the caller. That's intuitive — if the main agent is configured to trust `git *`, the sub-agent trusts it too. But for a reviewer, this is dangerous: the caller could "self-approve" its own commands through a reviewer with caller-specific rules.

**What it gives the audience.** For the harness engineer — the pattern "a security-critical sub-agent must be architecturally isolated". It's a rare but principled distinction: the reviewer cannot be bribed by the caller's context. If you're building an agent system with self-review, this specific part cannot be done "the simple way", or the whole idea of the reviewer collapses. Codex encoded this as an invariant directly.

---

## How to use — query examples

The two examples below demonstrate working with the spec on live queries. The reader arrives with an arbitrary question and receives a detailed, substantive answer assembled from the SRC modules. No prior orientation in the spec files is required.

> **Original user request:**
>
> *"I want two examples made from the spec — literally show how you built them: the query and what came out. Example 1 query: explain the most complex and important mechanic of the Codex agent. Example 2 query: help me solve the attention-budget decay problem when an instruction contains many rules. Answer these queries using this spec."*
>
> **Clarifications:**
>
> *"Example 2 should be for an instruction author. Answers must be built from the spec data. Also include the queries that produced the answers — otherwise the example is weak, it has to show how to use the spec. Rewrite the examples to be educational: fewer links into the spec, more of what's inside, so a reader can grasp the point without clicking through."*

---

### Example 1. Explaining a complex mechanic

**Short answer:** the most complex thing in Codex is auto-compaction of conversation history. It matters because it keeps the turn loop alive when context window fills up; it's complex because it combines five unrelated tasks: detecting proximity to the limit, choosing an implementation per provider, safely retrying on overflow, preserving history invariants, and supporting `/undo`.

#### When it fires

The harness computes the threshold as **90% of the model's context window**, with the ability to tighten it via config (the smaller value wins):

```
auto_compact_token_limit = min(config_limit, context_window × 9/10)
```

If both are unset, auto-compaction is disabled. It fires in three modes:

- **pre-turn** — before processing a new user message, if the previous total usage is already past the threshold;
- **mid-turn** — inside an active turn, when the model is about to hit the window;
- **manual** — the user command `/compact`.

The difference matters: mid-turn leaves an "anchor" on the current turn context so the next turn goes by diff. Manual and pre-turn don't set an anchor — the next turn performs a full re-injection.

#### Two implementations: inline vs remote

The harness first asks the provider: "do you support remote compaction?". If yes — the whole history is sent to the server endpoint `/responses/compact`, which returns an already compacted `Vec<ResponseItem>`. If no — the harness does inline summarization: it injects its own `SUMMARIZATION_PROMPT`, runs a regular streaming turn, and gets back a summary text.

The decision is made by the `provider.supports_remote_compaction()` flag and fixed at the start — no mid-stream switching.

#### The retry loop on window overflow (inline)

The trickiest part. Inline compaction itself performs a streaming request and may receive `ContextWindowExceeded` from the model — because the prompt with history plus summarization instructions doesn't fit either. The harness reacts like this:

- **if history has more than one item** → `history.remove_first_item()`, `truncated_count++`, `retries = 0`, `continue`. We sacrifice the oldest item to preserve the provider's prefix cache and the most recent messages (where the fresh user instructions usually live);
- **if only one item or fewer remains** → `set_total_tokens_full`, Error event, exit. Even minimal context doesn't fit — nothing further can save us.

On other errors (network, rate-limit) — retry with exponential backoff up to `provider.stream_max_retries()`. `Interrupted` / `TurnAborted` — immediate exit without retry. On pre-trim, the UI emits:

> *"Trimmed N older thread item(s) before compacting..."*

#### What actually stays in history after compaction

The harness builds `build_compacted_history_with_limit`:

1. Takes all user messages except summary messages (they're identified by the prefix — the text of `SUMMARIZATION_PROMPT` + `\n`).
2. Iterates in reverse and collects until the total exceeds `COMPACT_USER_MESSAGE_MAX_TOKENS = 20 000`.
3. Assembles the final list: `initial_context` + collected recent user messages + `summary_user_message` as the last.

If `summary_text` is empty — it's substituted with `"(no summary available)"`.

#### Where initial context physically ends up

The Codex model is trained to see summary in the last position of history. The harness preserves this invariant:

```
insertion_index =
  last_real_user_index          // before the last real user message
  ?? last_user_or_summary_index // if no real user found — before the summary
  ?? last_compaction_index      // if neither — before the last Compaction
                                // otherwise — append at the end
```

GWT example:

- **Given:** `compacted_history = [compaction_item, summary_user_message]`, `initial_context = [env_msg, dev_msg]`
- **When:** insertion
- **Then:** `last_real_user_index = None` (summary isn't real), `last_user_or_summary_index = 1`, insertion = 1 → `[compaction_item, env_msg, dev_msg, summary_user_message]`

Summary stays as the tail — the contract is preserved.

#### Remote pre-trim: what can be cut, what can't

Before sending to remote compaction, the harness estimates `estimate_token_count_with_base_instructions`. If the forecast exceeds the window — it trims the tail of history, but only while the last item is `codex-generated`:

- `FunctionCallOutput` / `ToolSearchOutput` / `CustomToolCallOutput` — can be cut
- developer Message — can be cut
- user / assistant Message — **break**, we don't cross the boundary

Invariant: when a tool-output is removed, the paired tool-call is also removed via `normalize::remove_corresponding_for`, otherwise the model would receive an "orphan" call with no response.

#### Ghost snapshots: preserving `/undo` after compaction

Before compaction, the harness collects `GhostSnapshot` items — snapshots for rollback. After the new `compacted_history` is assembled, they are appended to the end and flagged by the `should_keep_compacted_history_item` filter as retainable. Otherwise, `/undo` after compaction would be impossible.

#### What survives the `should_keep_compacted_history_item` filter (remote)

The remote endpoint can return items of any kind — the harness filters:

**Dropped:** developer messages (stale/duplicated instructions), user-role messages that are not UserMessage/HookPrompt (session-prefix wrappers), Reasoning, FunctionCall/Output, ToolSearchCall/Output, CustomToolCall/Output, LocalShellCall, WebSearchCall, ImageGenerationCall, GhostSnapshot, Other.

**Preserved:** assistant messages, Compaction items, user-role warnings, and summary messages.

Practical takeaway: anything the instruction author stashed in "one-off" developer injections will be discarded after compaction.

#### Telemetry and feature gating

All compaction events are emitted with a hard-coded `strategy = CompactionStrategy::Memento` — a constant on the analytics side, regardless of the real implementation. Emission is gated by `Feature::GeneralAnalytics`: the flag is snapshotted in `CompactionAnalyticsAttempt::begin` and is not rechecked in `track()`. Disabling analytics mid-turn will not cancel an already-started event.

The finalizer translates `Result` into `CompactionStatus`:

- `Ok` → `Completed`
- `Err(Interrupted)` / `Err(TurnAborted)` → `Interrupted`
- other `Err` → `Failed`

#### A side UX effect that matters to instruction authors

On completion of inline compaction, the harness emits a warning toast:

> *"Heads up: Long threads and multiple compactions can cause the model to be less accurate. Start a new thread when possible to keep threads small and targeted."*

This is a degradation signal: every new round of compaction discards an additional layer of context, because developer instructions don't survive the filter and summary is already a "retelling of a retelling". Hence the rule: system instructions must live in `base_instructions` (rebuilt every turn), not in runtime developer injections.

#### Why this is "the most complex" mechanic

A single execution path crosses six independent invariants:

1. Token arithmetic (`estimate_tokens_with_base_instructions`)
2. Provider detection (remote vs inline)
3. Network retry with backoff and prefix cache
4. History invariants (call/output pair, user/assistant boundary, summary last)
5. Rollback (ghost snapshots)
6. Feature flags and analytics state machine

An error in any of them leads to either OOM on context, or silent degradation of model quality.

---

### Example 2. Solving attention decay in rule-heavy instructions

**Short answer:** the harness already addresses the "many rules vs limited window" problem, but it does not forgive instruction authors who place rules in "volatile" zones. If rules land in the wrong place, the model silently gets a truncated version. Below are six practical rules, each with the mechanism and concrete numbers.

#### Rule 1. Stay below 90% of the context window

The harness auto-triggers compaction when `used_tokens > context_window × 9/10`. After compaction:

- only user messages within the last **20 000 tokens total** are kept (constant `COMPACT_USER_MESSAGE_MAX_TOKENS`);
- everything older dissolves into the summary;
- developer messages (including those carrying your rules) are **discarded in full** as "stale/duplicated instructions".

**Practice:** if your window is 200k tokens, the safe zone is up to 180k. If you "unfold" rules step by step across a dozen user messages, the start of the series will disappear after the first compaction.

#### Rule 2. Skeleton rules belong only in `base_instructions`

The harness resolves `base_instructions` by priority:

```
base_instructions =
    config.base_instructions                         // explicit override
    ?? conversation_history.get_base_instructions()  // from rollout session_meta
    ?? model_info.get_model_instructions(personality) // model default
```

Key property: **`base_instructions` are rebuilt every turn**. They don't live in history — they arrive as system instructions in a separate request field. Therefore they survive compaction and don't depend on which slice of history is still in context.

**Practice:**

- invariants of behavior, response style, hard rules → `base_instructions`;
- runtime policy (sandbox, approvals) → developer messages (the harness will place them);
- task-specific details → user messages (the last 20k will survive compaction).

#### Rule 3. The approved command prefixes list has hard limits

If you configure a shell-command approval policy via a prefix list, the harness renders it into developer instructions with two truncations:

- **byte limit:** `MAX_ALLOW_PREFIX_TEXT_BYTES = 5 000`. On overflow — the text is cut at the last valid UTF-8 char and the marker `...[Some commands were truncated]` is appended.
- **item limit:** `MAX_RENDERED_PREFIXES = 100`. The first 100 after sorting by `(len, combined_str_len, alphabetical)` are taken. The rest simply don't reach the prompt.

If you listed 200 permitted commands, the model will only see 100. Which 100 depends on lexicographic order. The harness does not warn.

**Workarounds:**

- group prefixes (`git *` instead of `git commit`, `git push`, `git pull` separately);
- move critical prohibitions into `base_instructions` as text, not into exec policy.

#### Rule 4. Use skill mentions instead of a bloated system prompt

The harness supports lazy injection of skill instructions. The sequence inside a turn:

1. `collect_explicit_skill_mentions(user_input)` — find `@`-mentions of skills in the user text;
2. `resolve_skill_dependencies_for_turn` — check env variables required by those skills;
3. `build_skill_injections` — assemble instructions **only for mentioned** skills;
4. `record_conversation_items` — add them to the turn history.

You can have dozens of skills with detailed rules, but only those the user explicitly invoked reach the prompt. This is the direct way to keep a large ruleset out of permanent context.

**Practice:** break the rule set into modules by topic (`@testing-rules`, `@security-review`, `@refactoring-style`). The base prompt holds only meta-rules about "which skill to call when". The active turn pulls details on demand.

#### Rule 5. Respect the contextual fragment markers

The harness distinguishes ordinary user text from contextual fragments — six kinds with strict wrapper tags:

- `<agents_md>...</agents_md>` (`AGENTS_MD_FRAGMENT`)
- `<environment_context>...</environment_context>`
- `<skill>...</skill>` (`SKILL_FRAGMENT`)
- `<user_shell_command>...</user_shell_command>`
- `<turn_aborted>...</turn_aborted>`
- `<subagent_notification>...</subagent_notification>`

During history normalization and pre-compaction trim, the harness treats these markers specially: it recognizes them as "meaningful" and handles their trimming differently. Environment and subagent-notification fragments are preserved in memory inputs.

**What happens if you wrap rules in your own XML tag like `<my_rules>...`?** The harness will treat it as ordinary user-text and trim it first — with no regard for what's inside. Want your rules to survive? Either `base_instructions`, or the standard `SKILL_FRAGMENT` via skill injection.

#### Rule 6. initial context is injected at a specific location

After mid-turn compaction, the harness inserts `initial_context` following a priority chain:

1. before the last real user message (not the summary);
2. if there is none — before the last user-like item (including summary);
3. if that's missing too — before the last Compaction item;
4. otherwise — append at the end.

The resulting order the model sees is `[..., initial_context, last_real_user, summary]` or `[..., initial_context, summary]`. The summary is always last — it's a contract with the trained model.

**Practice:** don't try to place something "after" the last user message — the harness will reshuffle it anyway. Put contextual rules inside initial_context blocks (env, agents.md), not as a trailing user reminder at the end of history — after compaction they won't be there.

#### Template for placing rules correctly

```
┌─────────────────────────────────────────────────────────────┐
│ base_instructions       ← invariants: style, safety,        │
│                           behavior boundaries               │
│                           (rebuilt every turn)              │
├─────────────────────────────────────────────────────────────┤
│ initial_context         ← env, AGENTS.md, persistent skill  │
│                           metadata                          │
│                           (re-injected after compaction)    │
├─────────────────────────────────────────────────────────────┤
│ skill injections        ← modular detailed rulesets,        │
│                           activated by @-mention            │
│                           (not kept in permanent context)   │
├─────────────────────────────────────────────────────────────┤
│ developer messages      ← runtime policy (approval/sandbox) │
│                           (volatile, 5000-byte /            │
│                           100-prefix cap, silently cut,     │
│                           discarded on compaction)          │
├─────────────────────────────────────────────────────────────┤
│ user messages           ← task-specific content             │
│                           (last 20 000 tokens               │
│                           survive compaction)               │
└─────────────────────────────────────────────────────────────┘
```

#### How to verify the rule "reached" the model

- **survived compaction** → it lives in `base_instructions`, initial_context, or within the last 20k user-tokens;
- **not silently truncated** → developer-prefix-list ≤ 5000 bytes and ≤ 100 items;
- **appears in the right context** → wrapped in the standard `SKILL_FRAGMENT` and activated by mention.

If even one is violated, the model is reading a cropped version of your instructions — and in most cases nobody ever notices.

---

## Structure and navigation

```
knowledge/
├── SRC-0001/ ... SRC-0018/        # 18 modules, each with:
│   ├── rules.json                 #   invariants and formulas
│   ├── structural.json            #   entities and relations
│   └── use-case.json              #   scenarios
├── scaffold_index.json            # flat index of composition IDs per SRC
├── graph.json                     # edges: triggers / depends_on / contains
└── README.md
```

Compositions are linked by references like `→triggers SRC-0011/uc.sub/auto-compact-inline` — you can navigate them through `scaffold_index.json` and `graph.json`.

For spec query methodology (grep + jq recipes): see `HOW-TO-QUERY.md`.

---

## Keywords

`openai codex harness` · `codex cli internals` · `codex agent loop` · `harness engineering` · `agent conversation turn lifecycle` · `tool registry` · `thread turn item` · `context compaction` · `auto-compact` · `responses compact endpoint` · `codex app server` · `prompt engineering reference` · `AI agent architecture spec` · `codex reverse engineering` · `base_instructions priority` · `initial context injection` · `SUMMARIZATION_PROMPT` · `CompactionStrategy::Memento` · `skill injection` · `prompt cache key` · `context window management`

Reference specification for the Codex harness (OpenAI Codex CLI) extracted as a knowledge graph: rules, use-cases, structural entities across 18 SRC modules covering protocol, session state, agent core, turn context, and tools registry. Companion to OpenAI's «Unrolling the Codex agent loop» series. Use cases: writing agent instructions, building your own agent harness, reverse-engineering compaction strategies, understanding turn lifecycle.

---

## Liked this work?

If the spec was useful to you — **star the repository**. It helps other agent builders and instruction authors find it through GitHub search.

Feedback and proposals for expanding coverage (new SRC modules, formula refinements, missing edge cases) are welcome in Issues.
