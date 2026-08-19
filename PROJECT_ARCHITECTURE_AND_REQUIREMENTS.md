# Eeveetuber: Project Architecture, Requirements, and Delivery Plan

**Status:** Working architecture baseline  
**Prepared:** 2026-08-19  
**Scope reviewed:** Open-LLM-VTuber 1.2.1 at commit `3afa410`, BearCode at commit `335b293`, Letta Code 0.30.25 at commit `ee230f3`, plus the linked Claude Code, LangChain/LangGraph, and RAG references  
**Implementation:** Phase 0 backbone and the deterministic fake Phase 1 vertical tracer are implemented; see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Production model, ASR, TTS, Live2D, frontend, and operator adapters remain next.  
**Intended use:** This is the editable source of truth for product scope, architecture decisions, requirements, and implementation phases. Update it as decisions are made; record consequential changes as ADRs rather than silently replacing their rationale.

---

## 1. Executive recommendation

Build Eeveetuber as a **new modular monolith**, not as a fork of any reviewed project.

- Selectively port the mature media/provider integrations from Open-LLM-VTuber behind new interfaces.
- Use Letta Code as the strongest reference for persistent identity, tiered context, versioned memory, conversation compaction, and background reflection—but adapt those ideas to SQLite-backed, deadline-bound VTuber operation.
- Use BearCode more narrowly for its small loop, typed tools, isolated work, and replay/candidate/promotion ideas; do not copy its coupled runtime or live skill mutation.
- Use **typed domain events** between components.
- Keep the **real-time interaction/media plane** separate from the **durable cognition/control plane**.
- Make the normal conversation path a fast lane: cached local identity and memory, one foreground model stream, incremental TTS, and deterministic capability filtering. It must not wait for an auxiliary AI safety judge, skill scan, reflection agent, or memory-writing model.
- Keep activity, reasoning effort, autonomy, broadcast status, and privacy as independent state axes. “Gaming” must not automatically mean “low reasoning,” and “working” must not automatically grant tools.
- Give the model semantic avatar actions such as affect, gaze, and gesture. A deterministic performance director—not the LLM—owns raw Live2D parameters, timing, blending, priority, and cancellation.
- Own Eeveetuber's public contracts. LangGraph may implement durable workflows behind an internal port; LangChain/model SDK types must not leak into avatar, media, memory, or plugin APIs.
- Start local-first with Python, FastAPI/WebSocket, SQLite, and in-process events. Do not begin with microservices, Kafka, autonomous skill mutation, a subagent swarm, or general desktop control.

The core product promise is:

> Eeveetuber is a responsive on-screen character that can converse and perform in real time, remember with consent, safely use capabilities, and continue entertaining while durable background work runs.

---

## 2. What was reviewed

### 2.1 Local projects

| Project | What it actually is | Best use for Eeveetuber | Main reason not to fork |
|---|---|---|---|
| [Open-LLM-VTuber](../Open-LLM/Open-LLM-VTuber/) | A FastAPI/WebSocket VTuber application with audio input, ASR, model streaming, TTS, simple Live2D expression tags, histories, character configs, and optional MCP tools | Port or wrap selected ASR, TTS, model-provider, Live2D metadata, and client-protocol knowledge | Thin agent harness, primitive avatar control, shared mutable session components, weak persistence/policy/security, and tightly coupled turn pipeline |
| [BearCode](../Agent/BearCode/) | A single-process terminal coding-agent harness with provider loops, built-in tools, permissions, sessions, compaction, memory, skills, MCP, and subagents | Reimplement selected loop, tool, isolation, and offline evaluation patterns | One large agent object, duplicated provider loops, coding-specific UI/tools, no real-time media, and serious correctness/security defects |
| [Letta Code](../Agent/letta-code/) | A production-scale TypeScript/Bun stateful coding-agent harness with a git-backed context repository (MemFS), conversations, compaction, background reflection, skills, subagents, tools, permissions, channels, and local/cloud backends | Primary reference for memory/context architecture, identity continuity, progressive disclosure, isolated reflection, and engineering/test discipline | Coding-oriented and non-realtime; its local search and storage are not suitable as Eeveetuber's hot path, and its agent-writable/auto-merged memory needs stronger source and promotion rules for public VTuber input |

The Open-LLM-VTuber and BearCode working trees already contained local modifications and were left untouched. Letta Code was cloned read-only for reference at the pinned commit above.

### 2.2 External references

| Reference | Use it for | Important caveat |
|---|---|---|
| [All-in-RAG](https://github.com/datawhalechina/all-in-rag) | Parent/child chunks, hybrid retrieval, metadata filters, routing, and evaluation ideas | / |
| [Claude Code reverse-engineering whitepaper](https://ccb.agent-aura.top/docs/introduction/what-is-claude-code) | A useful map of possible harness concepts | Its [methodology note](https://ccb.agent-aura.top/docs/introduction/why-this-whitepaper) says it is an unofficial reverse-engineering account, not a contract for Claude Code behavior. Cross-check important claims with official documentation. |
| [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) | Small educational examples of loops, hooks, permissions, memory, MCP, subagents, and durable workflows | It teaches patterns rather than providing a production runtime. The root s01–s17 track is canonical; its docs folder is a legacy track. |
| [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) | Compact examples of compaction, skills, subagents, MCP, budgets, and session resume | The project explicitly does not claim parity with real Claude Code. |
| [Official Claude Code hooks](https://code.claude.com/docs/en/hooks-guide), [memory](https://code.claude.com/docs/en/memory), and [permissions](https://code.claude.com/docs/en/permissions) | Stable lifecycle and safety concepts | Coding-agent policies must be adapted to owner/viewer authority and broadcast privacy. |
| [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) and [persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Durable state machines, checkpoints, interruption/resume, and background workflows | It is not an audio, animation, or low-latency event runtime. |
| [LangChain middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview), [memory concepts](https://docs.langchain.com/oss/python/concepts/memory), and [retrieval](https://docs.langchain.com/oss/python/langchain/retrieval) | Model/tool adapters and reference patterns | Keep framework types behind Eeveetuber-owned interfaces. |
| [Letta memory/dreaming](https://docs.letta.com/configuration/memory), [MemFS](https://docs.letta.com/concepts/memfs), and [conversations](https://docs.letta.com/concepts/conversations) | Tiered context, persistent agent identity, git-backed revisions, background consolidation, compaction, and recall | Official behavior spans local and cloud backends. The inspected local backend has simpler storage/search than some high-level product descriptions imply. |

---

## 3. Current Open-LLM-VTuber system

### 3.1 Runtime flow

The normal single-turn path is:

~~~text
Browser microphone/text/image
  -> WebSocketHandler
  -> ConversationHandler
  -> optional server-side VAD
  -> optional ASR
  -> BasicMemoryAgent
       -> provider-specific model stream
       -> optional MCP tool loop
       -> sentence splitting
       -> [emotion] tag extraction
       -> display/TTS filtering
  -> parallel sentence-level TTS
  -> WebSocket audio + volume envelope
  -> browser playback + Live2D expression
  -> JSON conversation history
~~~

Startup is [run_server.py](../Open-LLM/Open-LLM-VTuber/run_server.py), which builds the FastAPI/WebSocket server in [server.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/server.py). [websocket_handler.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/websocket_handler.py) routes client messages and owns per-client contexts. [service_context.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/service_context.py) constructs configured ASR, VAD, model, TTS, translation, Live2D, and MCP components.

### 3.2 Current functionality and where it lives

| Capability | Current implementation | Location |
|---|---|---|
| Web application/API | FastAPI routes, static frontend, WebSockets; frontend source is an external submodule and only built assets are checked out at this revision | [server.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/server.py), [routes.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/routes.py), [websocket_handler.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/websocket_handler.py) |
| Session conversation | Client contexts and asynchronous conversation tasks | [websocket_handler.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/websocket_handler.py), [conversations/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/conversations/) |
| Voice activity detection | Stateful server-side Silero VAD; the built frontend also contains VAD assets | [vad/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/vad/) |
| Speech recognition | Azure, faster-whisper, FunASR, Groq Whisper, OpenAI Whisper, sherpa-onnx, and whisper.cpp adapters | [asr/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/asr/) |
| Model providers | OpenAI-compatible, Anthropic/Claude, Ollama, llama.cpp, and templated APIs | [agent/stateless_llm/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/agent/stateless_llm/) |
| Agent behavior | In-memory transcript, vision messages, provider-specific tool loops, sentence transforms | [basic_memory_agent.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/agent/agents/basic_memory_agent.py), [transformers.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/agent/transformers.py) |
| MCP tools | Stdio MCP server registry, discovery, adaptation, execution, and tool-status messages | [mcpp/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/mcpp/) |
| Speech synthesis | Azure, Bark, Coqui, CosyVoice, Edge, Fish, GPT-SoVITS, Melo, MiniMax, OpenAI, pyttsx3, sherpa, SiliconFlow, Spark, XTTS, and related adapters | [tts/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/tts/) |
| Translation | DeepLX and Tencent translation before speech synthesis | [translate/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/translate/) |
| Avatar | Live2D model metadata plus prompt-emitted expression tags such as [joy] | [live2d_model.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/live2d_model.py), [model_dict.json](../Open-LLM/Open-LLM-VTuber/model_dict.json) |
| History | One JSON array file per character/history, with create/list/read/delete operations | [chat_history_manager.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/chat_history_manager.py) |
| Characters/config | Pydantic/YAML config and alternative character configs, selected via factories | [config_manager/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/config_manager/) |
| Images/perception | Client-provided camera/screenshot/screen images as data URIs to a vision-capable model | Conversation input and BasicMemoryAgent |
| Proactive speech | The client can send an ai-speak signal, which injects a generic prompt and skips normal memory/history | WebSocket/conversation handlers |
| Group conversation | Client/AI grouping and group turn handling | WebSocket and conversation handlers |
| Bilibili live chat | A separate script receives danmaku and forwards text to a hard-coded local proxy WebSocket | [scripts/run_bilibili_live.py](../Open-LLM/Open-LLM-VTuber/scripts/run_bilibili_live.py), [live/bilibili_live.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/live/bilibili_live.py) |

### 3.3 Current “thinking” behavior

There are two unrelated mechanisms:

1. The output transformer recognizes textual think tags. Their contents can be displayed differently and removed from TTS. This is presentation filtering, not an adaptive reasoning controller.
2. The local working tree adds an Ollama/OpenAI-compatible `reasoning_effort` value from static configuration, currently set to `none`. It is chosen from configuration rather than inferred from conversation, work, game, latency, or risk.

Neither mechanism provides automatic activity-aware reasoning, a background job lane, reasoning budgets, or provider-capability negotiation.

### 3.4 Strengths worth retaining

- A working end-to-end multimodal VTuber path.
- Broad ASR and TTS adapter coverage.
- Streaming sentence segmentation and ordered parallel TTS generation.
- A simple character configuration experience.
- An official MCP SDK integration rather than a completely custom protocol.
- Client/server knowledge for Live2D presentation and audio playback.
- A useful quick-start baseline for local deployments.

These are valuable adapters and UX references. They are not yet an extensible platform core.

### 3.5 Important gaps and defects

#### Architecture and isolation

- New client contexts reuse several default context objects by reference, including the stateful VAD and agent engine. Mutable transcript/VAD state can leak or race across sessions.
- A new turn overwrites the current task reference without consistently serializing or cancelling the previous turn.
- Disconnect cleanup removes the context before retrieving and closing it, so resources such as MCP sessions can leak.
- Factories and configuration unions require central edits for every new provider; plugins are not independent packages.
- Most integrations are installed together, creating a large dependency and startup surface.

#### Agent harness

- BasicMemoryAgent keeps an unbounded in-process message list; history is not a safe long-term memory system.
- OpenAI- and Claude-style tool loops are provider-specific and lack a common event contract.
- There are no per-run turn, time, token, or cost budgets; no durable checkpoint; no approval workflow; and no capability policy.
- MCP tools run when exposed. There is no owner/viewer authorization distinction, risk metadata, sandbox, or exact-arguments approval.
- MCP tools are keyed by bare tool name, which can collide across servers.
- The configured `mem0_agent` points to a zero-byte implementation file and is unusable.

#### Real-time interaction

- TTS completion can be emitted twice because the same task list is gathered in both the main path and finalization.
- Turn finalization can wait indefinitely for browser playback completion.
- There is no unified turn-taking state machine for listening, processing, speaking, barge-in, cancellation, and recovery.
- Backpressure, bounded queues, sequence guarantees, and per-resource arbitration are not explicit.
- Proactive speech is a client trigger, not an attention policy with interruption costs and cooldowns.

#### Avatar and performance

- The LLM emits literal expression tags; the backend maps them to an expression index.
- There are no semantic gesture/gaze/posture actions, timelines, viseme/phoneme scheduling, blending, priorities, leases, cooldowns, or cancellation.
- There is no capability negotiation for different Live2D models.
- Idle, blink, listening, speaking, and failure behavior are not coordinated as a deterministic performance state.

#### Data, security, and operations

- Conversation JSON is fully read and rewritten, without transactions, schema migrations, locking, or robust corruption recovery.
- There is no semantic/episodic/procedural memory separation, provenance, consent, confidence, TTL, privacy scope, export, or deletion workflow.
- FastAPI has no application authentication; permissive CORS and unauthenticated WebSockets are unsafe if exposed beyond trusted localhost.
- Model, chat, retrieved documents, tool output, and live-chat text are not consistently assigned trust levels.
- Logging is mostly unstructured. There are no traces, metrics, latency histograms, audit correlation, or replay tooling.
- There are no source tests in the reviewed tree.

### 3.6 Verified migration hazards

These details matter when deciding whether an existing component can be wrapped or must be ported:

| Severity | Verified behavior in the reviewed checkout | Migration decision |
|---|---|---|
| P0 | Per-client contexts share the mutable BasicMemoryAgent and stateful Silero VAD by reference. Loading one history can clear shared agent memory. | Never reuse ServiceContext cloning; construct/scoped state per session |
| P0 | A session builds its own MCP objects, but the shared agent remains bound to the default executor. | Rebuild dependency ownership; do not patch around the reference graph |
| P0 | Disconnect removes the context before looking it up for close, so cleanup is skipped. | Session supervisor owns idempotent close |
| P0 | A new turn overwrites the tracked task without first rejecting/cancelling the old task. | Serialized session mailbox plus cancellation generation |
| P0 | Microphone chunks are accumulated through repeated unbounded numpy append/copy. | Bounded ring/chunk buffers with size/time limits |
| P0 | Synthesis-complete is sent twice; the completion waiter can be registered after the frontend acknowledgement and has no default timeout. | New request/ack protocol with correlation, registration-before-send, and deadline |
| P0 | TTS synthesis tasks can outlive interruption; the payload sender is not fully joined before completion. | Structured task group owns synthesis, delivery, cancel, and one terminal event |
| P0 | API/WebSocket access has no authentication/authorization/rate limit, and CORS is wildcarded. | Localhost-only default; authenticated remote mode and origin/size limits |
| P0 | Enabled MCP tools execute without an approval/policy gateway. | All execution paths pass the same host-owned CapabilityPolicy |
| P1 | Mem0 is advertised, but its implementation file is zero bytes; a tool-error branch calls a nonexistent disable method. | Do not carry advertised backends without contract/E2E tests |
| P1 | Several ASR/VAD validators compare the wrong values; pyttsx3 exists in a factory but not selectable config. | Generate/validate adapter registration from one descriptor |
| P1 | Disabling translation during a character switch can leave the previous translator active. Old MCP/agent resources are not consistently closed. | Transactional profile switch with prepare/commit/rollback/close |
| P1 | The current frontend uses only the first expression, a random Talk motion, and local amplitude lip-sync; other action fields are effectively unused. | Replace the action contract and test renderer acknowledgements |
| P1 | History reload maps every non-human role to assistant; images/tool exchanges are not faithfully durable. | New normalized message/event schema and migration/import tool |
| P1 | Group conversation can run indefinitely without budgets and contains owner/group-ID, prompt-scope, ordering, and first-responder defects. | Defer multi-character behavior until single-session invariants are proven |
| P2 | Some async paths perform blocking HTTP work; factories are hard-coded conditionals; protocol strings have no shared schema. | Isolate blocking adapters, use registries, and generate/validate contracts |

---

## 4. Harness reference systems

### 4.1 BearCode runtime flow

~~~text
Terminal input
  -> Agent.chat()
  -> lazy MCP startup + skill retrieval + memory prefetch
  -> provider-specific model stream
  -> tool intents
  -> rule-based permission check
  -> built-in/MCP/subagent execution
  -> tool observations
  -> repeat model loop
  -> context compaction/session folding
  -> session save + background skill evaluation/evolution
~~~

The CLI starts in [agents/main.py](../Agent/BearCode/agents/main.py). Most runtime responsibilities live in the 1,827-line [agents/agent.py](../Agent/BearCode/agents/agent.py). Tools and permission rules live in [agents/tools.py](../Agent/BearCode/agents/tools.py); memory, sessions, skills, MCP, and subagents are separate files but are orchestrated directly by Agent.

### 4.2 BearCode functionality and where it lives

| Capability | Current implementation | Location |
|---|---|---|
| Model/tool loop | Separate Anthropic and OpenAI streaming loops, tool-call collection/execution, usage/cost reporting | [agent.py](../Agent/BearCode/agents/agent.py) |
| Built-in capabilities | Read, write, edit, list, grep, and shell tools | [tools.py](../Agent/BearCode/agents/tools.py) |
| Permissions | Rule-based allow/ask/deny-like decisions and plan/default/bypass profiles | [tools.py](../Agent/BearCode/agents/tools.py), [agent.py](../Agent/BearCode/agents/agent.py) |
| Optimistic editing | A file must be read before modification; modification time is checked before write | [tools.py](../Agent/BearCode/agents/tools.py) |
| Context control | Tool-result truncation, out-of-band result files, token estimates, transcript reduction, structured folding | [agent.py](../Agent/BearCode/agents/agent.py), [session_memory.py](../Agent/BearCode/agents/session_memory.py) |
| Long-term memory | Markdown records selected by a side model, bounded index, staleness warnings, asynchronous prefetch | [memory.py](../Agent/BearCode/agents/memory.py) |
| Sessions | Provider-native transcript JSON and folded-memory JSONL | [session.py](../Agent/BearCode/agents/session.py), [session_memory.py](../Agent/BearCode/agents/session_memory.py) |
| Skills | SKILL.md discovery, lexical retrieval, inline/fork use, usage tracking, provenance, automatic evolution | [skills.py](../Agent/BearCode/agents/skills.py), [skill_evolution.py](../Agent/BearCode/agents/skill_evolution.py) |
| Offline-style evaluation | Replay pools, candidate mutation, judging, status gates, champion artifacts | [online_skill_eval.py](../Agent/BearCode/agents/online_skill_eval.py) |
| MCP | Hand-built stdio JSON-RPC client, project/user configuration, namespaced tool names | [mcp_client.py](../Agent/BearCode/agents/mcp_client.py) |
| Subagents | Explore, plan, general, and custom Markdown-defined worker roles | [subagent.py](../Agent/BearCode/agents/subagent.py) |
| Interface | Blocking Rich terminal UI | [ui.py](../Agent/BearCode/agents/ui.py), [main.py](../Agent/BearCode/agents/main.py) |

### 4.3 BearCode ideas to reimplement

- A small model → tool intent → policy → execution → observation loop.
- Typed tool schemas and one host-owned policy gateway.
- Read-before-write optimistic concurrency checks.
- Provider streams normalized into internal text/tool/usage events.
- Explicit plan versus act behavior.
- Large result externalization and progressive compaction.
- Structured session folding into episodic, working, and tool state.
- Bounded memory indexes with selective recall rather than loading all durable memory.
- Declarative capabilities/skills with provenance and isolation options.
- MCP namespacing such as `mcp__server__tool`.
- Isolated worker contexts for slow research or work.
- Frozen replay sets, candidate changes, and human-approved promotion.

### 4.4 Why the BearCode implementation should not be adopted

The following are verified code-level problems, not merely missing enterprise features:

- OpenAI multi-tool batches are built/executed inside the per-call parsing loop, which can execute earlier tools repeatedly.
- Plan approval calls an asynchronous callback without awaiting it, previews a path instead of file content, and fails to apply the intended permission mode.
- General subagents run with bypass permissions and can evade parent confirmations.
- Project MCP commands start automatically, inherit the full environment, do not enter normal permission classification, lack tool timeouts, and contain a request-registration race.
- A normal OpenAI API key without a custom base URL is misclassified as the Anthropic protocol.
- Asynchronous memory prefetch often completes too late to affect the only model request in a turn.
- File paths are not workspace-confined, and shell safety relies on regular expressions around `shell=True`.
- Built-in outputs are truncated before “full” persistence.
- Latest-session resume can select another project's session and restores incomplete runtime state.
- “Thinking” is a startup boolean, not an adaptive policy.

Additional architectural gaps:

- The central Agent mixes provider protocols, prompts, permissions, tools, sessions, compaction, memory, skills, subagents, UI, and cost accounting.
- Output is coupled to terminal rendering rather than structured events.
- It has no VAD, ASR, TTS, image stream, avatar, OBS, chat aggregation, or performance scheduler.
- Owner and public viewer inputs have no distinct authority.
- Live skill mutation is unsafe for a public persona.
- There are no meaningful unit/integration tests, package metadata, lockfile, or CI in the reviewed source.

### 4.5 Letta Code runtime and scope

The inspected Letta Code revision is a much more substantial harness than BearCode. It has explicit package boundaries, local and API backends, provider adapters, tools, permissions, subagents, channels, hooks/mods, crons, telemetry, and extensive tests. Its central product idea is that an agent's identity and learned context persist across many independent conversations.

The relevant local-backend flow is:

~~~text
turn begins
  -> compile a committed MemFS revision into a cached system/context snapshot
  -> expose selected skills/tools and run the provider/tool loop
  -> append messages and usage to the conversation transcript
  -> compact older active context when pressure requires it; keep source history
  -> after the turn, optionally queue background reflection
  -> reflection edits an isolated memory worktree and commits a candidate revision
  -> merge/no-change/conflict result updates the primary memory revision
  -> invalidate/recompile the cached context snapshot for a future turn
~~~

This is a useful **control-plane** reference. It is not an audio, animation, game-telemetry, or low-latency speech runtime.

### 4.6 Letta Code memory and context model

Letta Code implements three practically useful disclosure levels:

| Level | Letta implementation | Eeveetuber interpretation |
|---|---|---|
| Always-hot context | Committed Markdown under `system/` is compiled into every system prompt; persona is separated from other system memory | Small, hard-budgeted character canon, owner/relationship summary, conversation contract, and current state snapshot |
| Memory map | Non-system file paths and descriptions are projected into the prompt without their full contents | A compact typed directory/index that helps select relevant cold memory without flooding context |
| Cold memory | Full external Markdown content is read only when needed | Semantic/episodic records retrieved locally under a strict deadline or explored by a background recall job |

Skills are stored as procedural external memory: descriptions can be visible while full instructions are loaded on demand. Conversation messages remain a separate history/recall layer. This separation is stronger than BearCode's looser Markdown recall and much stronger than Open-LLM-VTuber's in-process message list.

MemFS commits memory changes to an agent-owned git repository. The local prompt compiler reads a committed revision, caches it, and recompiles only when the memory revision changes. This gives coherent per-turn snapshots, diffs, attribution, rollback, and isolated branches for concurrent reflection.

### 4.7 Letta Code reflection, compaction, and recall

- **Dreaming/reflection:** after a configured number of steps or a compaction event, a background reflection subagent examines recent transcript plus the memory tree. It tries to preserve durable facts, corrections, preferences, contradictions, and at most one genuinely reusable skill change.
- **Isolation:** reflection runs in a separate git worktree/branch. Integration explicitly handles no-change, dirty parent, merge conflict, and failure outcomes instead of editing the foreground agent's context mid-turn.
- **Compaction:** older messages are summarized while recent messages and tool-call boundaries remain active. Full transcript data remains available for recall, so compaction is not treated as long-term memory.
- **Recall:** the useful interaction pattern is “find a likely message, then expand around its stable identifier.” However, the inspected local backend performs synchronous JSONL/term scanning rather than a production semantic index; MemFS itself has no vector index by default.
- **Promotion:** Letta can automatically merge reflection changes. Eeveetuber needs a stricter policy by memory class because public speech and viewer chat are untrusted sources.

### 4.8 Letta Code ideas to adopt

- Persistent agent identity shared across separate conversations, without treating one endless transcript as identity.
- Hot context + compact memory map + cold on-demand detail.
- A compiled, immutable context snapshot for each turn, keyed by a memory revision.
- Full history as the source of truth; compaction summaries are lossy navigation aids.
- Background consolidation that cannot delay the active response.
- Isolated candidate revisions, diff/merge semantics, rollback, and audit for learned context.
- Separate fresh specialist contexts for recall/reflection so their tool traces do not pollute foreground conversation.
- Hard package boundaries, dependency-cycle checks, adapter tests, and architecture test ratchets.

### 4.9 Letta Code gaps and required VTuber adaptations

- Letta is optimized for coding turns, not end-of-speech-to-audio latency. Eeveetuber must serve cached hot context immediately and abandon optional retrieval when its local deadline expires.
- The normal conversation lane must not invoke a recall/reflection subagent or scan skills before speaking. Partial ASR may prefetch local candidates; final context assembly is deterministic and bounded.
- Letta's memory frontmatter is mainly a description plus optional read-only flag. Eeveetuber additionally needs factual-source provenance, actor/trust labels, confidence, sensitivity, visibility, consent, validity, TTL, and deletion lineage.
- Letta allows the primary agent to edit its own memory without a normal approval prompt. Eeveetuber must prevent public/viewer text from directly creating durable memory and must protect owner-authored character canon.
- Loading every `system/` file can silently bloat the prompt. Eeveetuber needs hard token budgets, deterministic demotion, and metrics for every hot-context class.
- Git is valuable for owner-authored persona/skill revisions but is heavy as the primary store for frequent personal facts. Eeveetuber should use transactional SQLite revisions and indexes, with optional Markdown/git export for reviewable canon and skills.
- Memory consolidation and skill evolution must be different services. Consolidation may run only in idle/post-session windows; skill candidate generation is enabled only for explicit WORK-mode maintenance and never during casual conversation, gameplay, or a live show.
- The local JSONL scan and optional-mod search path are not sufficient for realtime recall. Eeveetuber should asynchronously index committed transcript/memory records in SQLite FTS5 and add vectors only if evaluation justifies them.
- Letta's self-rewriting identity philosophy is too permissive for a public character. Stable owner-authored canon is immutable at runtime; learned style/relationship state is a lower-authority layer; proposed canon changes require owner review.

---

## 5. Gap from the current projects to the target

| Target property | Open-LLM-VTuber today | BearCode today | Letta Code today | Eeveetuber requirement |
|---|---|---|---|---|
| Spoken responsiveness | Working but loosely coordinated | None | Interactive text harness; no realtime media path | Explicit real-time state machine, streaming audio, cancellation, deadlines, backpressure |
| Avatar expressiveness | Prompt tags to expressions | None | None | Semantic performance intents and deterministic scheduling/arbitration |
| Tool control | MCP without approval policy | Coding rules with bypasses and path/shell gaps | Mature deterministic permission/tool-loading modes, still coding-oriented | No tools in ordinary chat by default; deterministic capability filtering on the fast path; approval/isolation only for side-effecting work |
| Durable work | No resumable job runtime | Sessions/compaction, no durable workflow engine | Conversations, subagents, crons, channels; not a VTuber job/checkpoint contract | Checkpointed background jobs with idempotent steps and foreground progress events |
| Long-term memory | Transcript JSON/in-process list | Selective Markdown recall | Tiered git-backed MemFS, compaction, recall, background reflection | Hard-budget hot identity, compact index, deadline-bound local recall, typed/versioned memory, class-specific promotion |
| Automatic mode choice | Static model/config behavior | Static startup thinking | No VTuber activity/reasoning router | Deterministic sensor-driven activity router plus independent reasoning policy |
| Multi-session isolation | Shared mutable defaults | Single foreground CLI | Multiple conversations share agent memory; fresh/stateful subagents | Actor/mailbox per live session, explicit shared identity scope, scoped capability ownership |
| Extensibility | Central factories/config edits | Central schemas/dispatch/UI edits | Strong tools, skills, mods, channels, and backend packages | Versioned Eeveetuber ports, manifests, typed events, optional out-of-process plugins |
| Streaming/public privacy | Bilibili text forwarding | No broadcast model | Messaging channels but no VTuber broadcast/privacy model | Moderator, rate control, source trust, stream-safe context, protected canon, operator console |
| Operations | Logs only | Terminal/session files | Substantial tests, telemetry, architecture checks | Health, traces, metrics, audit, replay, degradation, soak/load tests |

---

## 6. Product scope and principles

### 6.1 Primary use cases

1. **Companion conversation:** low-latency voice conversation with barge-in, expressive delivery, images/screenshots, and consent-aware personal memory.
2. **Work companion:** accept an owner goal, safely use approved tools, run durable background work, and provide concise spoken progress without freezing conversation.
3. **Gaming companion:** react to aggregated game/voice/controller events, preserve low latency, avoid chatter spam, and optionally ask a background strategy worker for help.
4. **Live performance:** aggregate public chat, moderate/rank messages, coordinate avatar/OBS cues, and prevent private context from reaching the stream.
5. **Scripted performance:** execute deterministic show cues while retaining controlled conversational branches.
6. **Extensible character platform:** swap models, voices, ASR, avatar renderers, platforms, memories, and capabilities through stable interfaces.

### 6.2 Design principles

- **Real-time first:** no unbounded work on the audio/render critical path.
- **One-model fast lane:** ordinary conversation performs no auxiliary model approval, skill classification, reflection, or memory extraction before speech begins. Low-risk safety is structural: expose no side-effecting capabilities and use deterministic local checks.
- **Deterministic shell, probabilistic center:** the model proposes intent; code owns timing, authority, lifecycle, and safety.
- **One owner of each resource:** speech, microphone, avatar layers, scene control, and tool side effects have explicit arbiters.
- **State is explicit:** modes and transitions are data, not prompt folklore.
- **Authority never follows mode:** entering work/game/live mode cannot grant capabilities.
- **Memory is identity infrastructure:** a small versioned hot context is always available; durable writes are selective, attributable, visible, and reversible; slow consolidation never delays conversation.
- **Retrieval is untrusted context:** recalled memory, documents, chat, tool output, and MCP descriptions never become instructions by position alone.
- **Frameworks are replaceable:** Eeveetuber contracts outlive model/provider/orchestration libraries.
- **Degrade gracefully:** loss of a cloud model, TTS, ASR, renderer, or platform produces a known safe state.
- **Separate remembering from learning skills:** memory consolidation may propose low-risk personal facts in idle time; procedural skill evolution runs only in explicit WORK-mode maintenance and uses replay plus human promotion. Persona canon never rewrites itself live.

### 6.3 MVP non-goals

- Training a foundation model, ASR model, TTS model, or Live2D model.
- Revealing or storing private chain-of-thought. Only short user-facing status and decisions are exposed.
- General autonomous desktop or unrestricted shell control.
- Automatic installation or trust of arbitrary MCP servers.
- Live self-modifying prompts/skills/persona.
- A multi-agent swarm in the speaking path.
- A second AI model that approves, classifies skills, extracts memory, or performs reflection before each spoken reply.
- A hosted multi-tenant SaaS control plane.
- Supporting every provider currently present in Open-LLM-VTuber on day one.
- Letting an LLM generate frame-by-frame Live2D parameters.
- Microservices before scale evidence requires them.

---

## 7. Functional requirements

Priorities: **P0** is required for the first usable vertical slice, **P1** for a solid companion, **P2** for the work harness, **P3** for streaming/gaming, and **P4** for ecosystem expansion.

### 7.1 Sessions, inputs, and turn-taking

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-SES-001 | P0 | Create an isolated runtime session for every connected character/user context. No mutable VAD, transcript, agent, cancellation, tool, or output state may be shared accidentally. | Two concurrent-session test with sentinel data and zero cross-session events/memory |
| FR-SES-002 | P0 | Model every session as a supervised actor/mailbox with bounded queues, ordered event sequence numbers, cancellation, and shutdown. | Queue overflow, reconnect, cancel, and clean-shutdown integration tests |
| FR-SES-003 | P0 | Every accepted foreground turn advances a cancellation generation. Model, TTS, playback, and avatar results from older generations are discarded even if an adapter completes late. | Late-result fault test proves no stale audio/text/cue reaches the client |
| FR-IN-001 | P0 | Accept text, microphone audio, and user-selected images/screenshots through versioned API contracts. | Contract tests and one end-to-end trace for each input |
| FR-IN-002 | P1 | Support configurable trust/source metadata: owner, trusted operator, local sensor, public viewer, retrieved document, tool, and plugin. | Context/policy tests prove source labels survive transformations |
| FR-TURN-001 | P0 | Implement LISTENING, PROCESSING, SPEAKING, WAITING_APPROVAL, INTERRUPTING, IDLE, and DEGRADED interaction states with legal transition validation. | State-machine unit/property tests |
| FR-TURN-002 | P0 | Barge-in must cancel or duck current speech, stop pending speech segments, cancel superseded model/TTS work where safe, and preserve a consistent transcript. | Measured barge-in E2E test |
| FR-TURN-003 | P0 | All model, ASR, TTS, tool, and playback waits require deadlines and explicit timeout/fallback behavior. | Fault-injection suite; no indefinite waits |

### 7.2 Speech and presentation

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-ASR-001 | P0 | Provide VAD and streaming/final ASR interfaces with per-session state and normalized timestamps/confidence. | Fake adapter plus at least one real local/cloud adapter |
| FR-TTS-001 | P0 | Stream ordered speech segments with cancellation, retry/fallback, and word/phoneme/viseme timing when a provider supplies it. | Audio order/cancel tests and adapter contract suite |
| FR-TTS-002 | P0 | Text intended for subtitles and text intended for speech must be explicit fields, not inferred by stripping private reasoning tags. | UtterancePlan schema tests |
| FR-TTS-003 | P0 | Begin TTS from the first validated speakable segment without waiting for the complete model response or complete performance plan. | Measured first-segment pipeline test and cancellation test |
| FR-PERF-001 | P0 | Convert streaming model/persona output into validated UtteranceSegments containing speakable text, display text, affect, delivery, and optional semantic cues; collect them into a completed UtterancePlan for transcript/replay. | Incremental validation, malformed-segment fallback, and final-plan consistency tests |
| FR-PERF-002 | P0 | A PerformanceDirector maps semantic cues to each avatar's declared capabilities. Missing actions degrade to neutral behavior. | Capability-profile contract tests |
| FR-PERF-003 | P0 | A PresentationScheduler owns gesture/expression/gaze/posture leases, priority, blending, cooldown, rate limiting, cancellation, and neutral fallback. | Deterministic arbitration tests with competing cues |
| FR-PERF-004 | P1 | Lip/viseme motion follows the audio timeline; reactive blink/idle/listen/think behavior is deterministic and never waits for an LLM. | Recorded synchronization and fallback tests |
| FR-PERF-005 | P3 | Scene/prop/OBS cues use a separate authorized output channel and cannot be smuggled through spoken text. | Policy and injection tests |

### 7.3 Cognition and mode management

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-COG-001 | P0 | Provide a provider-neutral streaming model interface for text deltas, structured output, tool intents, usage, stop reason, and errors. | Golden contract tests against fake and two provider adapters |
| FR-COG-002 | P0 | Negotiate model capabilities: tools, structured output, vision, audio, context size, reasoning controls, and cancellation. | Capability mismatch produces a controlled fallback |
| FR-COG-003 | P0 | Enforce run budgets for wall time, model turns, tokens, cost, retries, and tool calls. | Budget exhaustion tests produce typed terminal events |
| FR-COG-004 | P0 | The REALTIME conversation profile performs one foreground model stream and never waits for an auxiliary model guard, skill selector, reflection agent, or memory-writing model. Its available capabilities are fixed by deterministic configuration/policy. | Trace assertion shows exactly one foreground model run before first audio and zero auxiliary AI dependencies |
| FR-COG-005 | P1 | Optional local recall may improve a turn only within its declared deadline. On timeout/error, continue with the cached hot-context snapshot instead of delaying speech. | Slow-index fault test remains within latency target and returns a coherent response |
| FR-MODE-001 | P1 | Track activity, broadcast, interaction, reasoning, autonomy, and privacy as independent state axes. | Transition matrix tests |
| FR-MODE-002 | P1 | Derive mode suggestions from explicit signals first and probabilistic classification only for ambiguity. | Replay evaluation with precision/recall and reason codes |
| FR-MODE-003 | P1 | Apply confidence thresholds, minimum dwell, hysteresis, cooldowns, transition hooks, manual lock with TTL, and safe fallback. | No-flapping property tests and operator override E2E |
| FR-MODE-004 | P1 | Reasoning policy may select realtime, standard, deep foreground, or background work independently of activity. | Test gaming+background-deep and work+realtime-interruption |
| FR-MODE-005 | P1 | Mode changes never change authorization unless a separate policy decision is made. | Security invariant test |

### 7.4 Agent harness, tools, and durable work

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-AGT-001 | P2 | Implement a small model → tool intent → policy → execution → observation loop with lifecycle middleware. | Deterministic fake-model loop tests |
| FR-AGT-002 | P2 | Externalize large results before compaction, preserve tool-call/result pairing, and keep the full audit transcript outside the prompt window. | Compaction golden tests |
| FR-TOOL-001 | P2 | Register built-in and MCP capabilities in one typed registry, with namespacing and lazy schema discovery. | Collision and deferred-discovery tests |
| FR-TOOL-002 | P2 | Every capability declares input/output schema, effect class, risk, permitted actors/modes, latency class, timeout, idempotence, concurrency/resource locks, retry, and optional compensation. | Registry rejects incomplete manifests |
| FR-POL-001 | P2 | Apply host-owned deny → ask → allow policy after schema/path validation and before execution. | Policy matrix and bypass-resistance tests |
| FR-POL-002 | P2 | Bind approval to actor, tool identity, normalized exact arguments/effect scope, expiry, and one execution. Edited arguments require a new decision. | Approval replay/edit/expiry tests |
| FR-POL-003 | P2 | Sandboxed/background operation fails closed when approval cannot be obtained. Public viewer input cannot approve owner capabilities. | Unattended/public-authority E2E tests |
| FR-MCP-001 | P2 | Treat MCP servers, schemas, descriptions, prompts, resources, and output as untrusted. Do not auto-launch project MCP commands without owner trust. | Malicious MCP fixture tests |
| FR-JOB-001 | P2 | Run complex work as checkpointed background jobs with progress, cancellation, pause/approval, retry, and resume after process restart. | Kill-and-resume E2E test |
| FR-JOB-002 | P2 | Isolate side effects into idempotent steps or stable idempotency keys because resumed workflow nodes may replay. | Duplicate-delivery fault test |
| FR-JOB-003 | P2 | Foreground conversation remains responsive while background work runs; it receives only typed public progress/result events. | Concurrent conversation/work latency E2E |
| FR-JOB-004 | P2 | On restart, mark in-flight executions interrupted. Never automatically replay a non-idempotent side effect; resume only from a recorded safe boundary or new approval. | Crash at every workflow boundary and verify external effect count |

### 7.5 Memory and knowledge

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-MEM-001 | P0 | Persist append-only conversation/event history and thread checkpoints separately from long-term memories. | Storage schema and recovery tests |
| FR-MEM-002 | P1 | Support distinct canon/persona revisions, user/viewer profiles, relationship state, semantic facts, episodes, and active-task records. Procedural skills use a separate repository and lifecycle. | Type/schema and repository-boundary tests |
| FR-MEM-003 | P1 | Every record includes subject/scope, provenance, timestamps, confidence, validity interval, sensitivity, visibility, TTL/retention, and revision status. | Database constraints and API tests |
| FR-MEM-004 | P1 | Durable memory admission is selective. Current instructions, untrusted chat claims, secrets, duplicates, and low-value transient details are rejected or quarantined. | Extraction/admission evaluation set |
| FR-MEM-005 | P1 | Recalled memory is delimited as untrusted background data and cannot override persona, policy, or current owner intent. | Prompt-injection tests |
| FR-MEM-006 | P1 | Provide operator/user view, correction, pin, forget, export, and delete controls with an audit trail. | UI/API privacy acceptance test |
| FR-MEM-007 | P1 | Consolidate duplicate, stale, and contradictory records only in background idle/post-session windows; never block an active turn or first spoken response. | Concurrent-conversation and contradiction tests; no foreground reflection span |
| FR-MEM-008 | P1 | Deleting a memory or knowledge source also removes or tombstones all derived summaries, chunks, lexical entries, vectors, and caches. | Delete-then-retrieve non-recurrence test |
| FR-MEM-009 | P0 | Compile a small immutable context snapshot for each turn from owner-authored canon, persona/relationship state, active session state, and privacy/mode recipe. A turn keeps one memory revision even if background consolidation commits concurrently. | Snapshot-generation and concurrent-update consistency tests |
| FR-MEM-010 | P1 | Implement progressive disclosure: hard-budget always-hot context, a compact memory directory/index, cold full records, and archived source history. | Token-budget tests and cold-record retrieval fixture |
| FR-MEM-011 | P1 | Enforce token budgets per hot-context class with deterministic trim/demotion rules, observability, and a valid minimal fallback snapshot. | Oversized-persona/profile test never exceeds configured budget |
| FR-MEM-012 | P1 | Memory selection for REALTIME uses only local cached/indexed data. It may prefetch from partial ASR and must return or abandon optional candidates within a strict deadline; it cannot call a remote model or scan the full store synchronously. | Retrieval latency benchmark, forced timeout, and full-store-scan prohibition test |
| FR-MEM-013 | P1 | Apply class-specific promotion: high-confidence low-risk profile/episodic facts may auto-commit; ambiguous, sensitive, contradictory, public-sourced, persona, or canon changes remain candidates for review. Policy/security rules are never learned as memory. | Promotion matrix and malicious-viewer transcript tests |
| FR-MEM-014 | P1 | Separate MemoryConsolidator from SkillLearner. Reflection can propose memory revisions from bounded transcript windows; it cannot create or mutate skills or owner-authored canon. | Component/tool-boundary tests and candidate diff review |
| FR-MEM-015 | P1 | Support transcript recall as indexed “needle then expand”: locate stable message/event IDs, then retrieve a bounded context window with source/cursor metadata. | Recall relevance and stable-expansion tests |
| FR-SKL-001 | P2 | Treat skills as versioned procedural packages separate from personal memory. In normal conversation/game/live modes, no dynamic skill scan or candidate generation occurs before speech. | Mode/tool exposure tests and trace assertion |
| FR-SKL-002 | P2 | Enable skill use or candidate generation only in explicit WORK/background contexts or owner-requested maintenance. New/changed candidates require frozen replay evaluation and owner promotion before becoming active. | Replay/candidate/champion gate and mode-boundary tests |
| FR-SKL-003 | P2 | Pin the active skill-set revision for a work job; running jobs and live performances cannot observe a mid-run skill mutation. | Concurrent-promotion consistency test |
| FR-RAG-001 | P2 | Keep knowledge document ingestion/retrieval separate from personal memory and thread checkpoints. | Separate schemas/namespaces and policy tests |
| FR-RAG-002 | P2 | Support canonical source records, parent-child chunks, metadata filtering, hybrid lexical/vector retrieval, fusion/reranking, citations, and cache/version invalidation. | Retrieval evaluation and citation provenance |
| FR-RAG-003 | P2 | Start with deterministic two-step or routed-hybrid retrieval; agentic multi-source research is an explicit background workflow. | Router tests and latency budget |

### 7.6 Streaming, gaming, proactive behavior, and operator control

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-CHAT-001 | P3 | Normalize chat from each platform into viewer/channel/role/trust/timestamp/message events. | Platform adapter contract tests |
| FR-CHAT-002 | P3 | Aggregate, deduplicate, moderate, rank, sample, and rate-limit public chat before it reaches the dialogue model. | Flood and injection load tests |
| FR-GAME-001 | P3 | Ingest read-only game/foreground-app/controller telemetry through allowlisted adapters with sampling and aggregation. | Fake-game replay |
| FR-GAME-002 | P3 | Game input/control is a separate high-risk plugin, disabled by default, with anti-cheat and game terms reviewed per integration. | Policy defaults and integration checklist |
| FR-PRO-001 | P1 | A deterministic attention policy decides whether proactive speech is useful using salience, interruption cost, speaking status, cooldown, quiet hours, privacy, and user preference. | Replay evaluation for nuisance rate |
| FR-OPS-001 | P0 | Provide an operator console with mute, stop speech, cancel job, neutral avatar, disable tools, memory privacy mode, mode lock, and kill switch. | End-to-end operator safety drill |
| FR-OPS-002 | P3 | A stream-safe privacy profile excludes private memory, screen regions, tool output, secrets, and private job details from public context/output. | Information-flow tests |
| FR-SHOW-001 | P3 | Support deterministic cue timelines for scripted speech, gestures, music/scene events, and controlled conversational branch points. | Replayable show fixture |

### 7.7 Configuration and extension

| ID | Pri | Requirement | Acceptance evidence |
|---|---:|---|---|
| FR-CFG-001 | P0 | Validate configuration before runtime with versioned schemas, secret references, capability checks, and actionable errors. | Invalid-config fixture suite |
| FR-PLUG-001 | P1 | Define versioned ports for model, ASR, TTS, avatar, platform, sensor, storage, memory, workflow, and tool adapters. | Adapter conformance tests |
| FR-PLUG-002 | P2 | Trusted built-ins may run in process; third-party plugins default to isolated processes with narrow secrets, filesystem, network, and capability grants. | Isolation/integration tests |
| FR-PLUG-003 | P2 | Plugin manifests declare API version, configuration schema, capabilities, permissions, health checks, and lifecycle hooks. | Manifest validator |

---

## 8. Non-functional requirements

Targets below are initial engineering budgets, not claims about the reviewed projects. They must be measured on a declared reference machine, network, model, ASR, TTS, and avatar profile.

| ID | Category | Initial target |
|---|---|---|
| NFR-LAT-001 | Barge-in | p95 speech stop/duck command within 150 ms of confirmed user speech detection |
| NFR-LAT-002 | Avatar | p95 internal avatar-event dispatch under 50 ms |
| NFR-LAT-003 | Overhead | p95 Eeveetuber pipeline overhead, excluding external model/ASR/TTS latency, under 100 ms per realtime event |
| NFR-LAT-004 | Speech | time from end-of-user-speech to first output audio: p50 ≤ 2 s and p95 ≤ 4 s on the reference profile |
| NFR-LAT-005 | Event loop | no blocking task over 50 ms on the realtime loop; blocking adapters use worker pools/processes |
| NFR-LAT-006 | Context | cached hot-context snapshot load p95 ≤ 10 ms; complete local realtime context assembly, including optional indexed recall, p95 ≤ 50 ms and hard timeout ≤ 75 ms |
| NFR-LAT-007 | Fast-lane independence | first audio in REALTIME has zero dependency on auxiliary AI services, reflection, skill discovery, durable memory writes, or full-corpus scans |
| NFR-REL-001 | Isolation | zero cross-session leakage in stress/property tests |
| NFR-REL-002 | Recovery | durable jobs resume after process termination without duplicate external effects |
| NFR-REL-003 | Soak | four-hour conversation/audio/avatar soak without unbounded memory/queue growth |
| NFR-REL-004 | Degradation | every external dependency has timeout, circuit breaker/failure counter, health state, and fallback |
| NFR-SEC-001 | Network | localhost by default; authenticated sessions, origin controls, and TLS are mandatory for remote access |
| NFR-SEC-002 | Secrets | secrets never enter prompts, normal logs, plugin-wide environments, or public events |
| NFR-SEC-003 | Audit | every tool decision/call, memory mutation, mode change, and operator override is attributable and correlated |
| NFR-PRI-001 | Privacy | configurable local retention; export/delete; stream-safe information-flow tests; camera/screen data off by default |
| NFR-OBS-001 | Observability | structured logs, traces, metrics, latency histograms, health endpoints, and replayable event capture |
| NFR-TST-001 | Testability | all adapters have deterministic fakes; core state/policy logic requires no cloud service |
| NFR-TST-002 | Compatibility | versioned API/event schemas and contract tests prevent silent plugin/client breakage |
| NFR-PER-001 | Backpressure | all queues bounded with documented overflow/coalescing policy and dropped-event metrics |
| NFR-PER-002 | Context bounds | each hot-context class and the total compiled context have configured token/byte ceilings; growth of history or cold memory does not increase fast-lane assembly complexity without bound |
| NFR-PORT-001 | Local first | Windows is supported as a first-class development/runtime target; platform-specific adapters are isolated |

---

## 9. Target architecture

### 9.1 Two planes, one typed event model

~~~mermaid
flowchart LR
    subgraph Inputs
      MIC[Mic / VAD]
      CHAT[Owner & public chat]
      IMG[Image / screen]
      GAME[Game / app telemetry]
      OBS[OBS / show state]
    end

    subgraph Realtime["Real-time interaction & media plane"]
      SESSION[Session actor + turn coordinator]
      CONTEXT[Cached context snapshot]
      DIALOGUE[Foreground dialogue policy]
      SPEECH[ASR / TTS / subtitles]
      PERF[Performance director + scheduler]
    end

    subgraph Control["Durable cognition & control plane"]
      ROUTER[Activity + reasoning router]
      AGENT[Provider-neutral agent loop]
      POLICY[Capability policy + approvals]
      JOBS[Checkpointed workflow runtime]
      MEMORY[Memory store + local indexes]
      CONSOLIDATE[Background memory consolidator]
      SKILLS[Work-mode skill learner]
      RAG[Knowledge retrieval]
    end

    subgraph Outputs
      AVATAR[Live2D / avatar adapter]
      AUDIO[Audio output]
      UI[Operator UI / clients]
      TOOLS[Tools / MCP / integrations]
    end

    Inputs --> SESSION
    CONTEXT --> DIALOGUE
    SESSION --> DIALOGUE
    SESSION --> ROUTER
    DIALOGUE <--> AGENT
    MEMORY -->|revisioned snapshot| CONTEXT
    AGENT -.->|bounded local recall request| MEMORY
    MEMORY -.->|bounded records| AGENT
    CONSOLIDATE -->|candidate/commit| MEMORY
    SKILLS -->|reviewed version| MEMORY
    AGENT <--> RAG
    AGENT --> POLICY --> TOOLS
    ROUTER --> JOBS
    AGENT --> JOBS
    JOBS -->|progress/result events| SESSION
    DIALOGUE --> SPEECH
    DIALOGUE --> PERF
    SPEECH --> AUDIO
    SPEECH --> PERF
    PERF --> AVATAR
    SESSION --> UI
~~~

The planes may run in one process initially, but their contracts and scheduling are separate:

- **Real-time plane:** session lifecycle, VAD/ASR stream, cached context snapshots, turn taking, foreground dialogue, incremental TTS/subtitles, speech cancellation, avatar intents, and presentation timing. It uses bounded queues and deadlines and does not call a second model before speaking.
- **Control plane:** deeper reasoning, tools, policy/approval, long-term memory processing, skill maintenance, RAG, checkpoints, and durable jobs. It may pause, retry, or resume, but it cannot become a prerequisite for the current spoken response.
- **Event boundary:** control work reports status/result events. It never directly writes Live2D parameters or audio buffers.

### 9.2 Event envelope

Every cross-component event should use a versioned envelope:

~~~python
class EventEnvelope:
    event_id: UUID
    type: str
    schema_version: int
    occurred_at: datetime       # wall clock
    monotonic_at_ms: int        # ordering/latency on this host
    session_id: UUID | None
    actor_id: str | None
    correlation_id: UUID
    causation_id: UUID | None
    sequence: int | None
    priority: int
    trust: TrustLabel
    visibility: Visibility
    payload: JsonValue
~~~

Representative events:

- `audio.chunk`, `vad.speech_started`, `vad.speech_ended`
- `transcript.partial`, `transcript.final`
- `interaction.state_changed`, `mode.suggested`, `mode.transitioned`
- `agent.text_delta`, `agent.status`, `agent.completed`, `agent.failed`
- `tool.proposed`, `approval.required`, `tool.started`, `tool.progress`, `tool.completed`
- `job.checkpointed`, `job.progress`, `job.completed`
- `utterance.ready`, `speech.started`, `speech.cancelled`, `speech.completed`
- `avatar.cue_requested`, `avatar.cue_started`, `avatar.cue_cancelled`
- `context.snapshot_published`, `memory.candidate_created`, `memory.committed`, `memory.forgotten`
- `reflection.queued`, `reflection.completed`, `skill.candidate_created`, `skill.promoted`

Events are not automatically durable. The schema declares retention class: ephemeral media, operational trace, transcript, audit, or durable domain event.

### 9.3 Proposed repository layout

~~~text
Eeveetuber/
  pyproject.toml
  uv.lock
  README.md
  apps/
    server/                 # FastAPI composition root and API
    operator_web/           # operator/client UI (separate TS workspace if chosen)
  src/eeveetuber/
    api/                    # HTTP/WS DTOs and versioning
    runtime/                # event bus, session actors, supervision, cancellation
    dialogue/               # turn coordinator, foreground policy, UtterancePlan
    modes/                  # activity/reasoning/autonomy/privacy state machines
    agent/                  # model loop, context, compaction, budgets
    capabilities/           # registry, schemas, policy, approval, execution
    workflows/              # durable jobs; optional LangGraph implementation
    media/
      vad/
      asr/
      tts/
      audio/
    avatar/                 # semantic model, director, scheduler, renderer ports
    perception/             # image/screen/game/app sensors and aggregation
    memory/                 # tiers, snapshot compiler, admission, indexed recall, consolidation, privacy
    skills/                 # procedural packages, WORK-only selection/evaluation/promotion
    knowledge/              # document ingest, indexes, retrieval, citations
    integrations/
      mcp/
      obs/
      bilibili/
    plugins/                # manifest, discovery, isolation, lifecycle
    storage/                # repositories, SQL models, blob/artifact store
    observability/          # logs, traces, metrics, audit, replay
    config/                 # versioned schemas and secret references
  profiles/
    characters/             # persona, voice, avatar capability mappings
    policies/               # owner/viewer/mode capability policy
  migrations/
  tests/
    unit/
    property/
    contract/
    integration/
    e2e/
    replay/
    evals/
    load/
  docs/
    adr/
    protocols/
    operations/
~~~

This is a dependency direction, not permission for arbitrary imports. Domain/state/policy modules should not depend on FastAPI, LangChain, a model SDK, a database ORM model, or a renderer.

### 9.4 State ownership and cancellation semantics

Every mutable state has exactly one logical owner:

| Owner | State it alone mutates |
|---|---|
| SessionActor / TurnCoordinator | Foreground turn, interaction state, input ordering, cancellation generation |
| ModeCoordinator | Activity/reasoning suggestions, evidence, dwell/cooldown, manual lock |
| AgentRuntime | One model run, budgets, context window, tool-call progression |
| ContextSnapshotManager | Compiled hot-context generations, budgets, cache publication, per-turn revision pin |
| MemoryConsolidator | Background memory candidates, class-specific admission, merge/consolidation transactions |
| SkillLearner | WORK-only procedural candidates, replay results, promotion state |
| JobSupervisor / WorkflowRuntime | Durable job status, checkpoint, retry/resume decision |
| CapabilityExecutor | Tool execution record and acquired resource locks |
| PerformanceDirector / Scheduler | Active avatar layers, cue leases, audio-timeline binding |
| Repositories | Transactional persisted records and revisions |

A barge-in or replacement turn increments the session's cancellation generation before cancellation signals are sent. Every asynchronous result carries the generation under which it started. The SessionActor rejects older-generation model deltas, TTS audio, completion acknowledgements, and avatar cues. This protects the user even when a provider ignores cancellation or finishes concurrently.

Use direct calls for ordinary same-process request/response. Use bounded queues only where serialization, buffering, priority, or cancellation isolation is required. The architecture is event-informed, not a requirement to persist or broker every token, audio frame, or animation tick.

---

## 10. Activity, reasoning, and automatic switching

### 10.1 Independent state axes

| Axis | Suggested values | What it controls |
|---|---|---|
| Activity | CONVERSATION, WORK, GAMEPLAY, PERFORMANCE, IDLE, SAFE_DEGRADED | Attention pattern, latency targets, context recipe, proactive behavior |
| Broadcast | OFFLINE, PRIVATE_RECORDING, LIVE | Audience/visibility constraints; can overlay any activity |
| Interaction | IDLE, LISTENING, PROCESSING, SPEAKING, WAITING_APPROVAL, INTERRUPTING, DEGRADED | Real-time resource ownership and legal transitions |
| Reasoning | REALTIME, STANDARD, DEEP_FOREGROUND, BACKGROUND | Model profile, budget, whether durable job is used |
| Autonomy | OBSERVE, SUGGEST, ACT_WITH_APPROVAL, POLICY_ALLOWED | Maximum action authority; policy may still deny |
| Privacy | NORMAL, PRIVATE, STREAM_SAFE | Which sources may enter context and which outputs may leave |

These axes prevent false coupling. Examples:

- GAMEPLAY + LIVE + REALTIME for moment-to-moment reactions.
- GAMEPLAY + LIVE + BACKGROUND for a strategy question while reactions continue.
- WORK + OFFLINE + REALTIME for a quick spoken clarification.
- WORK + OFFLINE + BACKGROUND + ACT_WITH_APPROVAL for a resumable research task.
- CONVERSATION + LIVE + STREAM_SAFE so private owner memories are excluded.

### 10.2 Mode profiles

| Activity | Foreground behavior | Typical capabilities | Proactive policy |
|---|---|---|---|
| CONVERSATION | Cached hot context, optional ≤75 ms local recall, one streaming dialogue model, incremental TTS, normal gestures | No side-effecting tools or dynamic skills; explicitly configured low-latency local reads only | Speak on explicit address or strong salience |
| WORK | Clarify goal, create durable job, voice concise progress | Approved file/web/project tools and version-pinned skills in background | Progress only at milestones or operator request |
| GAMEPLAY | Cached hot context, aggregate events, short reactions, strict cooldowns | Read-only telemetry; optional background strategy worker; no skill learning | React to salient events, never narrate every event |
| PERFORMANCE | Follow show clock/cues; controlled improvisation; frozen context/skill revisions | Cue/OBS actions defined by script/policy; no reflection | Script owns timing |
| IDLE | Low-resource sensing, deterministic idle animation, and budgeted background memory consolidation | Memory candidate work only when resource/quiet policy allows | Quiet-hours and nuisance thresholds |
| SAFE_DEGRADED | Canned/local acknowledgement and neutral avatar | No risky/external actions | Only explain recovery state if useful |

### 10.3 Switching algorithm

The LLM may suggest a transition, but a deterministic ModeCoordinator owns it.

Signal precedence:

1. Operator manual mode lock/command and emergency controls.
2. Privacy/safety requirements.
3. Explicit task lifecycle: accepted work job, approval wait, completion.
4. OBS/broadcast state and scripted show cues.
5. Foreground process/game plus allowlisted game telemetry.
6. Voice cadence, direct address, input source, and idle timeout.
7. A small classifier only when deterministic evidence is ambiguous.

Each suggestion contains target, confidence, evidence, reason code, timestamp, and expiry. The coordinator then applies:

- confidence threshold;
- minimum dwell time;
- separate enter/exit thresholds (hysteresis);
- transition cooldown;
- required hooks and resource cleanup;
- manual lock and TTL;
- safe fallback if evidence conflicts or a hook fails.

Example:

~~~text
OBS switches to Gameplay scene + allowlisted game process is foreground
  -> suggest GAMEPLAY at high confidence
  -> enter after 2 seconds of stable evidence
  -> load gameplay context/tool subset and reaction cooldown policy

User asks: "Research the best build and write me a note"
  -> remain in GAMEPLAY
  -> reasoning policy selects BACKGROUND
  -> start checkpointed work job after required approval
  -> foreground continues short gameplay reactions
  -> job emits milestone/result events

Game closes or operator pins CONVERSATION
  -> exit after debounce, cancel/coalesce stale telemetry
  -> keep background job unless explicitly cancelled
~~~

The first mode-router evaluation dataset should contain recorded/replayed traces with labeled expected transitions. Optimize false switches and mode flapping, not merely classifier accuracy.

---

## 11. Avatar and Live2D control

### 11.1 Replace prompt tags with semantic intent

The model should produce:

~~~python
class UtterancePlan:
    display_text: str
    speech_text: str
    locale: str
    affect: Affect              # valence/arousal + optional label
    delivery: Delivery          # pace, energy, emphasis, volume intent
    cues: list[SemanticCue]      # smile, nod, look_at_user, point_left...
    visibility: Visibility
    interruptibility: str
~~~

It must not output renderer expression indices, raw Cubism parameter names, arbitrary JavaScript, or exact frame timing.

### 11.2 Performance pipeline

~~~text
UtterancePlan
  -> PerformanceDirector
       persona style rules
       current interaction/activity state
       avatar capability profile
       deterministic reactive layers
  -> PresentationScheduler
       leases/TTL
       priorities + resource locks
       blend curves
       cooldown/rate limit
       cancellation + neutral fallback
       audio/word/viseme timeline
  -> AvatarAdapter
       Live2D Web client initially
       future VTube Studio/VRM/other renderers
~~~

Suggested priority order:

1. Operator emergency/neutral/visibility override.
2. Speech lip-sync and barge-in cancellation.
3. Scripted show cue with explicit lock.
4. Interaction state (listen/think/speak).
5. LLM-requested semantic gesture/affect.
6. Game/event reaction.
7. Idle/blink/breathing.

An action receives a lease with duration/TTL. On expiry, cancellation, disconnect, or adapter failure, the scheduler transitions cleanly to the next layer or neutral state.

### 11.3 Avatar capability profile

Each avatar profile maps stable semantics to model-specific resources:

~~~yaml
api_version: 1
avatar_id: eevee_v1
renderer: live2d_web
capabilities:
  affect.joy:
    expression: exp_smile
    max_intensity: 0.8
  gesture.nod:
    motion_group: acknowledge
    cooldown_ms: 2500
  gaze.user:
    parameters: [ParamAngleX, ParamAngleY, ParamEyeBallX, ParamEyeBallY]
  viseme:
    strategy: amplitude_or_provider_viseme
fallbacks:
  missing_cue: ignore
  disconnect: neutral
~~~

This preserves character-specific art constraints while keeping the cognition layer renderer-independent.

---

## 12. Agent harness design

### 12.1 Minimal loop

~~~text
load pinned cached context snapshot + optional deadline-bound local recall
  -> call model adapter
  -> stream validated utterance segments/status events to TTS and presentation
  -> if no tool intents: validate final/UtterancePlan and finish
  -> validate tool schemas and normalize arguments
  -> policy decision: deny / ask / allow
  -> execute approved calls with limits and isolation
  -> persist observations/artifacts
  -> compact when required
  -> repeat within budgets
~~~

The loop must stay small. These concerns attach through declared middleware/hooks:

- before run: pinned context revision, cached hot snapshot, optional local recall deadline, model selection, budgets;
- before model: compaction, prompt injection defense, redaction;
- model stream: typed delta/tool/usage normalization;
- before tool: schema, path, actor, policy, approval, lock acquisition;
- after tool: artifact externalization, redaction, audit, result limits;
- after run: append source history, enqueue memory-candidate material, metrics, replay capture; never run reflection inline;
- stop: cancellation, budget, operator kill, policy violation.

### 12.2 Owned ports

Core interfaces:

- `ModelProvider`
- `ContextAssembler`
- `AgentRuntime`
- `ToolRegistry`
- `PolicyEngine`
- `ApprovalBroker`
- `CapabilityExecutor`
- `ArtifactStore`
- `CheckpointStore`
- `WorkflowRuntime`
- `MemoryRepository`
- `ContextSnapshotCompiler`
- `MemoryConsolidator`
- `SkillRepository`
- `SkillLearner`
- `KnowledgeRetriever`

Provider-specific “reasoning effort” is a mapping inside ModelProvider. The reasoning policy requests an abstract profile and budget; the adapter translates it only if supported.

### 12.3 Capability descriptor

~~~python
class CapabilityDescriptor:
    qualified_name: str
    version: str
    input_schema: JsonSchema
    output_schema: JsonSchema
    effect: Literal["read", "local_write", "external_write", "control"]
    risk: Literal["low", "medium", "high", "critical"]
    authorized_actor_classes: set[ActorClass]
    allowed_activities: set[Activity]
    latency_class: Literal["realtime", "interactive", "background"]
    timeout_ms: int
    idempotent: bool
    concurrency_safe: bool
    resource_locks: set[str]
    retry_policy: RetryPolicy
    compensation: str | None
~~~

Tool selection is dynamic: expose only the smallest set allowed for the actor, mode, stage, model capability, and current policy. MCP is an adapter source for descriptors, never the authorization authority.

### 12.4 Foreground versus background reasoning

- **Realtime:** one fast model stream, cached hot context, optional deadline-bound local recall, incremental TTS, and no dynamic skill discovery. No auxiliary model, reflection, memory write, full-store scan, or side-effecting tool is on the first-audio dependency chain.
- **Standard:** normal conversation with a limited tool round budget.
- **Deep foreground:** only when silence is acceptable; emits user-facing status, never private chain-of-thought.
- **Background:** durable job graph with checkpoints, progress, approvals, and result artifacts. Foreground dialogue remains available.

Use one foreground agent first. Add isolated workers only for a measured need such as research, document construction, or game strategy. A child worker receives a scoped context and no greater authority than its parent.

### 12.5 LangGraph decision

Use LangGraph optionally **behind WorkflowRuntime** for:

- resumable work/research jobs;
- approval interruptions;
- multi-step RAG with validation;
- memory extraction/consolidation;
- WORK-mode skill candidate evaluation and promotion workflows;
- fault recovery and progress/checkpoint streams.

Do not use it for:

- audio buffers or VAD;
- TTS playback sequencing;
- Live2D frame/parameter updates;
- high-rate game telemetry;
- the session mailbox itself.

LangGraph's checkpointer stores thread-scoped graph state, while a separate Store can hold cross-thread application data. Eeveetuber should preserve that conceptual separation even if its first implementation uses custom repositories.

---

## 13. Memory, history, and knowledge

### 13.1 Persistence layers and prompt-exposure tiers

| Layer | Purpose | Lifetime/store |
|---|---|---|
| Ephemeral media | Raw audio frames, temporary image frames, partial ASR | Memory/bounded temp spool; off by default for retention |
| Event/audit log | Correlated state, decisions, calls, failures | Append-only operational store with retention |
| Conversation history | User-visible transcript and utterance metadata | Relational DB |
| Thread/checkpoint state | Current run/job state for resume | Checkpoint repository |
| Working context | Current goals, unresolved questions, selected observations | Rebuilt/scoped per run |
| Episodic memory | Summaries of meaningful interactions/events | Long-term memory records |
| Semantic memory | Stable preferences, relationships, facts | Long-term memory records |
| Procedural skill packages | Owner-approved reusable workflows/instructions, separate from personal memory | Versioned SkillRepository with replay/promotion metadata |
| Persona canon | Character identity, lore, boundaries, public/private variants | Owner-controlled versioned profile |
| Knowledge/RAG | Documents, lore, game guides, project docs | Document/chunk/index stores |
| Artifacts | Reports, files, large tool output, recordings if enabled | File/blob store with metadata |

Persistence and prompt exposure are different decisions. Following Letta's strongest MemFS idea, Eeveetuber projects those stores into a bounded per-turn context hierarchy:

| Exposure tier | Contents | Prompt behavior | Mutation authority |
|---|---|---|---|
| T0 owner canon | Character identity, non-negotiable boundaries, owner-authored public/private variants | Always hot, small, instruction authority, independently versioned | Owner/editor only; never learned automatically |
| T1 hot personal context | Stable speaking style, owner profile, relationship summary, current-session goals/state | Always hot but individually budgeted; learned fields are clearly delimited as data | High-confidence low-risk updates or reviewed candidates, depending on class |
| T2 memory map | Compact paths/labels/descriptions, types, scopes, and revision metadata for cold records | Small always-hot signpost/index; no full record bodies | Derived deterministically from committed records |
| T3 cold memory | Full semantic facts, episodes, detailed preferences, prior events, topic summaries | Retrieved locally only when relevant and within the turn deadline | Class-specific admission and consolidation policy |
| T4 source/archive | Full transcript/events, superseded versions, artifacts, deletion tombstones | Never loaded wholesale; queried by stable ID/range in background or explicit recall | Append-only/source retention and explicit lifecycle policy |

Procedural skills are a separate namespace, not a T3 personal-memory subtype. Skill descriptions may be indexed for WORK jobs, but conversation/game/live profiles do not scan or dynamically load them before speech.

Every turn pins a `ContextSnapshot` containing its memory generation, character/profile revisions, privacy/mode recipe, token accounting, and selected record IDs. A background commit publishes a new snapshot for a later turn; it never mutates the meaning of an in-flight turn.

### 13.2 Realtime read path

~~~text
committed memory/persona revision
  -> background ContextSnapshotCompiler
  -> validate per-class budgets and deterministic trim/demotion
  -> publish immutable cached hot snapshot + compact memory map

partial ASR (optional)
  -> prefetch candidate IDs from local FTS/cache

final user turn
  -> select privacy/activity context recipe
  -> pin current cached snapshot
  -> bounded local lexical/metadata retrieval and rerank
  -> fetch only a small number of permitted full records
  -> stop at deadline; hot snapshot alone is a valid fallback
  -> call the one foreground dialogue model
~~~

The realtime selector is code, not another model. It uses source/identity filters, recency, confidence, scope, lexical score, optional precomputed embeddings, and a diversity/size budget. A remote embedding or classification call cannot be required to answer. Index writes, embeddings, and cache invalidation happen asynchronously after commit.

Explicit questions such as “what did we discuss last month?” may enter STANDARD mode or launch a background recall job. Casual banter does not pay that cost.

### 13.3 Background write and consolidation path

~~~text
interaction/event
  -> append transcript/audit
  -> enqueue bounded, source-labelled candidate material
  -> idle/post-session MemoryConsolidator
  -> deterministic admission filters
       durable value?
       allowed source/consent?
       sensitive/private?
       duplicate/contradiction?
       persona or policy injection?
  -> class-specific auto-commit or review candidate
  -> transactional versioned commit + derived-index outbox
  -> asynchronously rebuild indexes and publish a new ContextSnapshot
~~~

Adopt Letta's isolated-reflection principle but not its universal auto-merge behavior:

| Proposed change | Default treatment |
|---|---|
| Raw transcript/event with configured retention | Append automatically |
| High-confidence, low-sensitivity owner preference or stable relationship fact | Auto-commit is permitted with provenance and undo |
| Ambiguous, contradictory, sensitive, inferred, or public/viewer-sourced fact | Candidate/quarantine; do not place in hot context without review/confirmation |
| Episodic summary | Auto-commit only when its source window and visibility are explicit; otherwise candidate |
| Learned style/relationship summary | Candidate or conservative field update; bounded and reversible |
| Character canon/persona authority | Owner-reviewed proposal only; runtime file/record is read-only to agents |
| Capability/security/policy instruction | Reject as memory; change code/config through the normal owner workflow |
| Procedure or skill | Route to SkillLearner only in explicit WORK maintenance; replay evaluation and owner promotion required |

MemoryConsolidator may use a model because it is off the response path, but it receives bounded transcript ranges, source trust labels, existing neighboring records, and no capability to modify canon, policy, or skills. It produces typed diffs with evidence IDs rather than free-form self-rewrites. Only one consolidation transaction per identity namespace integrates at a time; conflict, no-change, rejection, and retry are explicit outcomes.

### 13.4 Local-first storage and versioning

Recommended initial storage:

- SQLite in WAL mode, SQLAlchemy 2, and Alembic migrations for sessions, messages, events, modes, jobs, memories, approvals, and metadata.
- A transactional outbox/job table for durable background scheduling and progress publication.
- SQLite FTS5 for lexical retrieval.
- A vector-index port. Begin with a simple local implementation only if evaluation shows benefit; allow Qdrant/Postgres later.
- Filesystem artifact store rooted in an explicit application data directory with content hashes and database metadata.
- Secret references in OS keychain/environment/secret provider; never store raw secrets in character YAML.
- A revision ledger and immutable candidate/commit records for memory/persona/skill changes. Git-style diffs, attribution, rollback, and merge outcomes are requirements even when SQLite is the primary store.
- Optional Markdown/git projection for owner-authored canon, character profiles, and promoted skills. Do not create a git commit for every conversational fact on the realtime path.

Crash recovery restores only committed turns/checkpoints and the last valid compiled context generation. It marks in-flight model/tool runs interrupted, reconciles the outbox, and automatically resumes only steps declared safe and idempotent. A non-idempotent external effect remains stopped until the runtime can prove it completed through its idempotency record or an operator explicitly chooses a recovery action.

### 13.5 Compaction and recall

Compaction is not long-term memory. It keeps a model request inside its context limit; memory captures selected durable information. Full source transcripts and artifacts remain separately available under retention policy.

Use a layered strategy inspired by Letta Code and the educational harnesses:

1. Externalize oversized tool/media results to the artifact store with a bounded preview and stable handle.
2. Remove or replace old consumed tool results while preserving tool-call/result pairing.
3. Prefer a background-prepared rolling conversation summary and structured current-state snapshot before context pressure becomes urgent.
4. When required, summarize older history while retaining recent complete turns and prior summary lineage.
5. If a foreground summarizer cannot finish within budget, use deterministic truncation to the last safe turn boundary plus the last committed summary; do not make speech wait indefinitely.
6. Search the immutable source history by stable message/event ID, then expand a bounded before/after range to restore provenance that a summary may omit.

### 13.6 RAG strategy

Start with predictable retrieval:

1. Ingest/canonicalize a source and record owner, URI, checksum, version, access scope.
2. Create small retrieval chunks linked to larger parent chunks.
3. Index lexical and dense representations plus metadata.
4. Classify whether retrieval is needed and which corpus is allowed.
5. Retrieve lexical+dense candidates, fuse (for example reciprocal rank fusion), filter by permissions/metadata, optionally rerank.
6. Give the model parent context with stable source identifiers.
7. Require answer citations when facts come from knowledge sources.
8. Evaluate retrieval recall, answer grounding, latency, and injection resistance.

Do not implement GraphRAG until ordinary hybrid retrieval has a measured failure that graph structure solves.

---

## 14. Security, privacy, and authority model

### 14.1 Actors

- Owner
- Trusted operator
- Local authenticated user
- Public viewer
- System scheduler
- Local sensor
- Built-in adapter
- Third-party plugin/MCP server

Each action and memory write identifies both the originating actor and the current authenticated approver. Public viewers cannot acquire owner authority by phrasing, tool descriptions, or mode changes.

### 14.2 Trust boundaries

Treat all of these as untrusted data:

- public and owner-supplied text until classified by policy;
- webpage/document/RAG content;
- screen OCR and game telemetry strings;
- tool and MCP output;
- MCP tool descriptions/prompts/resources;
- recalled memory content;
- provider-generated tool arguments;
- plugin events.

Only signed/versioned owner configuration and built-in system policy are instruction sources by default.

### 14.3 Required controls

- Keep the normal conversation fast lane safe by construction: no side-effecting tools, deterministic source/visibility filters, bounded local memory lookup, and output cancellation. Do not insert a second AI “safety” or approval service before speech.
- Enter the slower permission/approval path only when an owner requests a capability with an external effect or when a durable background job reaches an effect boundary.
- Localhost-only default; authenticated API/WebSocket sessions and origin checks for remote use.
- Deny/ask/allow capability rules keyed by actor, effect, resource, mode, privacy, and argument constraints.
- Exact, expiring approvals and visible effect summaries.
- Workspace/path canonicalization and allowlists before file access.
- Process/network/plugin sandboxing appropriate to platform; narrow environment and secret grants.
- Egress allowlists for integrations that need network access.
- Redaction before logs, prompts, traces, artifacts, and public output.
- Separate public/private persona and memory visibility.
- Camera/screen/audio retention off unless explicitly enabled.
- Kill switch that cancels speech, jobs, tool execution where possible, and scene/avatar control.
- Append-only audit records for security-relevant decisions.

---

## 15. Reliability and observability

### 15.1 Supervision and degradation

Every long-lived component reports STARTING, HEALTHY, DEGRADED, FAILED, and STOPPED. Supervisors define restart/backoff and state restoration. Examples:

- ASR fails: allow typed chat and show a quiet microphone error.
- primary TTS fails: use configured fallback or subtitles only.
- model fails: local/canned acknowledgement, preserve input, and retry only within policy.
- avatar disconnects: audio/subtitles continue; cues expire to neutral on reconnect.
- MCP/tool fails: job records error and continues only if workflow semantics allow.
- database write fails: stop durable side effects and report operator-visible degraded state.
- event queue fills: coalesce/drop low-priority telemetry first; never silently drop approvals, turns, or safety events.

### 15.2 Telemetry

Use OpenTelemetry-compatible traces/log correlation and Prometheus-style metrics where practical.

Required dimensions/metrics:

- session/correlation/job IDs, component, adapter, mode, trust/visibility (never raw sensitive text);
- end-of-speech to transcript, first model token, first TTS byte, first audio;
- barge-in detection-to-stop;
- avatar cue scheduling latency and lip-sync drift;
- queue depth, coalesced/dropped events, event-loop stalls;
- model/tool/TTS/ASR latency, errors, retries, timeouts, circuit state, cost/tokens;
- mode proposals/transitions/rejections/flapping;
- memory proposals/admissions/rejections/recall usefulness;
- context snapshot generation, hot-token use by class, cache age, local-recall deadline misses, and fallback-to-hot-only rate;
- reflection queue age/runtime/candidate outcome and skill-candidate replay/promotion outcome, never on first-audio spans;
- tool decision and approval outcomes;
- background job duration, checkpoints, resumes, duplicated-effect prevention.

Structured event recordings can be replayed with media payloads redacted or replaced by deterministic fixtures.

---

## 16. Test and evaluation strategy

### 16.1 Engineering tests

- **Unit:** state transitions, context recipes/snapshot budgets, policy rules, approval binding, avatar arbitration, memory admission/promotion, compaction.
- **Property:** event ordering, no mode flapping, idempotency, resource-lock exclusivity, cancellation invariants, cross-session separation.
- **Contract:** every model/ASR/TTS/avatar/platform/plugin/storage adapter against deterministic fakes.
- **Integration:** WebSocket lifecycle, SQLite transactions/migrations, TTS ordering, cancellation, checkpoint resume, MCP isolation.
- **End-to-end scenarios:**
  - casual voice turn with barge-in;
  - realtime voice turn proving one foreground model call, cached context fallback, and zero reflection/skill/auxiliary-model dependencies before first audio;
  - two simultaneous isolated sessions;
  - work request requiring exact approval and background progress;
  - process killed during a tool workflow then resumed;
  - gameplay telemetry flood while speaking;
  - live public prompt injection attempting private-memory/tool access;
  - TTS/model/avatar failures and recovery;
  - memory view/correct/forget/export.
- **Replay/golden:** recorded event traces, context assembly snapshots, provider-stream fixtures, avatar schedules.
- **Load/soak:** bounded queue pressure, multi-session load, four-hour media/session soak.

### 16.2 Behavioral evaluations

| Evaluation | Primary measures |
|---|---|
| Mode router | transition precision/recall, false switches/hour, dwell/flapping, reaction time |
| Conversation | response relevance, persona consistency, interruption handling, latency |
| Proactive behavior | useful-intervention rate, nuisance rate, cooldown violations |
| Memory | admission precision, useful recall, context assembly p50/p95, deadline fallback rate, contradiction rate, public-source contamination, privacy leakage, delete effectiveness |
| Skill learning | WORK-mode boundary correctness, replay win rate, regressions, unauthorized/live activation rate |
| RAG | retrieval recall, grounding/citation accuracy, latency, injection resistance |
| Tool safety | unauthorized action rate, approval correctness, argument-change resistance |
| Avatar | cue appropriateness, collision rate, lip-sync drift, neutral recovery |
| Live chat | moderation recall/precision, duplicate/spam suppression, fair sampling |
| Reliability | recovery time, duplicate external effects, queue loss by priority |

Borrow BearCode's replay/candidate/champion concept only as an **offline promotion pipeline**:

1. Freeze representative private/redacted replay suites.
2. Evaluate baseline and candidate configurations/prompts/rules.
3. Reject on any safety/privacy regression.
4. Require statistically/qualitatively meaningful benefit.
5. Require human review and versioned promotion.
6. Preserve rollback.

---

## 17. Technology baseline

These are recommended starting decisions, subject to a small spike where noted:

| Area | Baseline |
|---|---|
| Language/runtime | Python 3.12, asyncio, uv for environment/lock management |
| API | FastAPI, versioned HTTP/WebSocket protocol, Pydantic v2 DTOs |
| Storage | SQLite WAL, SQLAlchemy 2, Alembic, FTS5, transactional outbox/revision ledger; filesystem artifacts |
| Realtime events | Typed in-process pub/sub + per-session actor mailboxes; no external broker initially |
| Models | Eeveetuber ModelProvider interface; begin with one OpenAI-compatible and one local/Ollama adapter |
| MCP | Official Python SDK behind namespaced registry/policy/isolation adapter |
| Durable workflows | WorkflowRuntime port; LangGraph implementation spike for P2 |
| Observability | structlog or standard structured logging, OpenTelemetry, Prometheus-compatible metrics |
| Tests | pytest, pytest-asyncio, Hypothesis, deterministic fake adapters |
| Frontend | Reuse protocol/renderer knowledge; decide between a clean TypeScript web client and a temporary compatibility adapter |
| Packaging | Optional dependency groups per adapter; minimal core installation |

Before adopting an adapter from Open-LLM-VTuber, isolate its dependency group, write the target port's contract tests, and port the smallest implementation rather than importing its ServiceContext/factories.

---

## 18. Reuse, refactor, replace, defer

### 18.1 Open-LLM-VTuber

| Area | Decision | Notes |
|---|---|---|
| ASR adapters | Selectively port | Start with one local and one cloud option; normalize timing/cancellation |
| TTS adapters | Selectively port | Start with one reliable streaming provider plus local fallback |
| Model adapters | Reference/port | Replace BasicMemoryAgent/provider loops with ModelProvider events |
| Sentence/TTS pipeline | Refactor concept | Preserve incremental generation, add deadlines, cancellation, single completion semantics |
| Live2D model metadata | Port with license review | Convert to capability profiles |
| Expression prompt tags | Replace | Use UtterancePlan and PerformanceDirector |
| MCP SDK client | Reuse SDK, replace architecture | Namespace, policy gate, lifecycle, timeouts, trust/isolation |
| JSON history | Replace | Transactional relational schema/migrations |
| WebSocket protocol | Replace/version | Temporary compatibility shim is acceptable for migration |
| Frontend built assets | Do not make foundation | Obtain source/submodule and assess separately |
| Bilibili integration | Defer/port concept | Put behind normalized platform adapter and moderation |
| Group chat | Defer | First solve one character/session and public-chat aggregation |
| Hume/Letta special agents | Defer | Add only through the same contracts |

### 18.2 BearCode

| Area | Decision | Notes |
|---|---|---|
| Central loop | Reimplement | One provider-neutral loop with typed events |
| Tool schemas/policy gateway | Reimplement | Strong descriptors, actor authority, exact approvals |
| Read-before-write | Reimplement | Add workspace containment and transactional/optimistic checks |
| Context folding/large results | Reimplement | Keep audit source separate and preserve call/result structure |
| Memory categories/selective recall | Reinterpret | Add consent, provenance, trust, confidence, TTL, visibility |
| Skills | Later, owner-reviewed | Never mutate public persona live |
| Replay/candidate/champion eval | Reimplement offline | One of BearCode's strongest ideas |
| MCP namespacing/lazy discovery | Reimplement | Use official SDK and safe lifecycle |
| Subagent isolation | Reimplement later | Child authority is equal or narrower, never bypass |
| Shell/file tools | Do not copy | High-risk optional work plugin, disabled by default |
| Provider loops/Agent object | Replace | Too coupled and duplicated |
| Permission implementation | Do not copy | Verified bypass and approval defects |
| Session/memory filesystem layout | Replace | Inconsistent roots and resume semantics |
| Live automatic skill evolution | Do not ship | Candidate changes require offline eval and human promotion |

### 18.3 Letta Code

| Area | Decision | Notes |
|---|---|---|
| Agent identity across conversations | Reimplement | Identity/memory belongs to the character, while conversations remain independent threads |
| Hot system memory + external projection | Reimplement with hard budgets | T0/T1 full snapshot, T2 compact map, T3 cold records; learned data has lower authority than owner canon |
| Context revision caching | Reimplement | Compile and publish immutable snapshots; pin one generation per turn |
| Git-backed memory changes | Adapt, not primary store | SQLite transactional revision ledger for frequent facts; optional Markdown/git projection for canon and promoted skills |
| Background dreaming/reflection | Reimplement with narrower authority | Idle/post-session only, source-labelled typed diffs, class-specific promotion; never blocks conversation |
| Reflection worktrees/merge outcomes | Reinterpret | Preserve isolated candidates, conflicts, no-change, retry, diff, rollback semantics without requiring git per fact |
| Conversation compaction | Reimplement | Preserve source transcript and recent complete turns; prepare summaries in background; deterministic deadline fallback |
| Recall “needle then expand” | Reimplement | Stable IDs plus bounded context expansion over indexed SQLite history |
| Local JSONL/term scanning | Replace | Append/query transactional indexed stores; no synchronous full-store scan in REALTIME |
| Skills as external procedural memory | Reimplement later | Descriptions/content progressively disclosed only to WORK jobs; pin versions |
| Combined memory + skill reflection | Split | MemoryConsolidator cannot mutate skills; SkillLearner uses replay and owner promotion |
| Agent-writable/auto-merged persona | Do not adopt | Owner canon is read-only at runtime; learned relationship/style is separate and lower authority |
| TypeScript CLI/channel runtime | Reference only | Eeveetuber keeps its Python realtime/media architecture and owned contracts |
| Test/architecture discipline | Adopt | Dependency direction, zero-cycle checks, contract tests, and size/coverage ratchets |

---

## 19. Delivery roadmap

Each phase ends with a demonstrable vertical slice and a go/no-go gate. Avoid porting breadth before the architecture passes its contracts.

### Phase 0 — Decisions and executable skeleton

**Goal:** establish contracts and a testable process before media breadth.

- Confirm name/package, Windows-first support, operator/client shape, and Live2D renderer route.
- Create pyproject, lockfile, lint/type/test/CI baseline.
- Implement event envelope, cancellation, typed errors, structured logging.
- Implement in-process event bus and isolated session actor with bounded mailbox.
- Define memory record, context snapshot, revision, source/trust, and promotion-policy schemas before choosing retrieval models.
- Define ports and deterministic fake adapters.
- Create ADRs listed in section 20.

**Gate:** two fake sessions run concurrently, cancel cleanly, and produce correlated replay traces with no cross-session state.

### Phase 1 — P0 real-time vertical tracer

**Goal:** one character can reliably hear, respond, speak, and animate.

- Versioned WebSocket protocol and minimal operator UI.
- Text + microphone + selected-image input.
- One VAD/ASR, one model, one TTS, one Live2D adapter.
- Turn state machine, timeouts, barge-in, ordered TTS, degradation.
- UtterancePlan, capability profile, PerformanceDirector, scheduler.
- SQLite transcript/events, migrations, basic history UI, and stable message/event IDs.
- P0 ContextSnapshotCompiler with owner-authored T0 canon, bounded T1 persona/session state, revision pinning, cache, and hot-only fallback. No background reflection yet.
- Operator mute/stop/neutral/kill controls.
- Latency metrics, adapter health, E2E and soak tests.

**Gate:** P0 functional requirements pass; declared speech and context-snapshot latency profiles are measured; first-audio traces have one foreground model and no auxiliary AI dependency; four-hour soak has bounded memory/queues.

### Phase 2 — P1 companion intelligence

**Goal:** mode-aware companion with controlled long-term memory.

- Independent state axes and deterministic ModeCoordinator.
- Conversation/work/game/idle/degraded profiles; manual lock.
- Model capability negotiation and abstract reasoning profiles.
- Proactive attention policy.
- Owner-read-only persona canon, lower-authority learned relationship/profile state, and visibility scopes.
- T2 memory map, T3 cold records, SQLite FTS5 indexes, partial-ASR prefetch, bounded local recall, and hot-only deadline fallback.
- Semantic/episodic/profile candidates, source-labelled class-specific admission, idle/post-session MemoryConsolidator, revision diffs, UI view/correct/forget/export.
- Background-prepared rolling summaries and stable-ID “needle then expand” transcript recall.
- Privacy/retention controls and stream-safe context recipes.
- Mode, memory, persona, and proactive replay evaluations.

**Gate:** mode false-switch and memory admission/recall targets are defined/met; context assembly meets NFR-LAT-006 under a large history; public-source contamination, deletion, and privacy tests pass.

### Phase 3 — P2 safe harness and durable work

**Goal:** safely perform useful work without blocking the VTuber.

- Minimal provider-neutral agent loop with budgets/middleware/compaction.
- Capability registry, policy engine, exact approval broker, audit.
- First safe read-only tools and artifact store.
- MCP adapter with explicit trust, namespace, timeouts, narrow secrets, isolation.
- WorkflowRuntime and LangGraph spike/implementation.
- Checkpointed work/research job, progress events, crash/resume/idempotency.
- Versioned procedural skills exposed only to explicit WORK/background jobs, with the active revision pinned per job. Skill use is manual/configured first; no automatic evolution is required in this phase.
- Knowledge ingestion and deterministic hybrid RAG with citations/evals.

**Gate:** adversarial tool/MCP suite passes; kill-and-resume creates no duplicate effect; foreground latency remains inside budget during background work.

### Phase 4 — P3 streaming and gameplay

**Goal:** a stream-ready, context-aware performer.

- OBS integration and broadcast state.
- Bilibili first platform adapter; add others by demand.
- Chat normalization, identity, moderation, aggregation, ranking, rate controls.
- Read-only foreground-process/game telemetry adapter.
- Gameplay reaction policy and background strategy job.
- Stream-safe information-flow enforcement.
- Deterministic show/cue timeline.

**Gate:** live-chat flood/injection tests and private-data noninterference tests pass; operator safety drill succeeds.

### Phase 5 — P4 ecosystem

**Goal:** expand only where usage/evaluation justifies it.

- Stable plugin manifest/SDK and third-party process isolation.
- More ASR/TTS/model/avatar/platform adapters.
- Optional WORK-mode SkillLearner that produces candidate procedural packages from explicit maintenance sessions.
- Frozen replay comparison, regression checks, owner promotion, and rollback for skill candidates; no conversation/game/live self-evolution.
- Specialized workers for measured tasks.
- Optional remote deployment hardening.
- Candidate/champion configuration promotion pipeline.

**Gate:** public API compatibility policy, security review, plugin conformance suite, and upgrade/migration story exist.

### Requirement traceability

This table is intentionally at requirement-family level. Individual issue/test IDs should link back to the exact requirement row when implementation begins.

| Requirement families | Primary owner modules | Main verification | Delivery |
|---|---|---|---|
| FR-SES, FR-IN, FR-TURN | runtime/sessions, api, dialogue | state/property, concurrent-session, reconnect, cancellation E2E | Phases 0–1 |
| FR-ASR, FR-TTS | media/vad, media/asr, media/tts | adapter contracts, audio golden, timeout/cancel/failure E2E | Phase 1 |
| FR-PERF | avatar, dialogue | arbitration property tests, playback synchronization, renderer contract | Phase 1 |
| FR-COG | agent, model adapters | provider golden streams, capability mismatch, budget tests | Phases 1–3 |
| FR-MODE | modes, runtime | labeled replay, no-flapping properties, manual override E2E | Phase 2 |
| FR-AGT | agent, workflows | deterministic loop/compaction tests, full-audit preservation | Phase 3 |
| FR-TOOL, FR-POL, FR-MCP | capabilities, integrations/mcp | policy matrix, malicious MCP, approval hash/expiry, isolation | Phase 3 |
| FR-JOB | workflows, storage | boundary crash injection, kill/resume, idempotency, foreground latency | Phase 3 |
| FR-MEM | memory, storage, operator UI | admission/retrieval eval, privacy controls, delete non-recurrence | Phase 2 |
| FR-SKL | skills, workflows, replay/evals, operator UI | mode isolation, pinned revision, replay promotion, rollback | Phases 3 and 5 |
| FR-RAG | knowledge, storage | retrieval/grounding/citation/injection evaluation | Phase 3 |
| FR-PRO | dialogue/attention, modes | nuisance/usefulness replay and cooldown tests | Phase 2 |
| FR-CHAT, FR-GAME, FR-SHOW | integrations/channels, perception, avatar | flood/replay/information-flow/show-timeline E2E | Phase 4 |
| FR-OPS | operator UI, runtime, capabilities | operator drill, kill/cancel/privacy E2E | Phases 1–4 |
| FR-CFG, FR-PLUG | config, plugins, adapters | schema, conformance, dependency-boundary, isolation tests | Phases 0–5 |
| NFR-LAT, NFR-PER | all realtime modules | reference-profile benchmark, queue pressure, event-loop stall monitor | Phase 1 onward |
| NFR-REL | runtime, workflows, storage | soak, fault injection, restart, duplicate-effect prevention | Phase 1 onward |
| NFR-SEC, NFR-PRI | api, capabilities, memory, secrets | threat fixtures, auth/origin, redaction, noninterference | Every phase |
| NFR-OBS, NFR-TST, NFR-PORT | observability, tests, adapters | trace completeness, fake contracts, CI matrix | Phase 0 onward |

---

## 20. Architecture decision records to create

| ADR | Proposed decision |
|---|---|
| ADR-001 | Greenfield modular monolith; port adapters rather than fork either project |
| ADR-002 | Separate real-time interaction/media plane from durable cognition/control plane |
| ADR-003 | Eeveetuber-owned typed events and ports; framework/provider types stay at adapters |
| ADR-004 | Per-session actor/mailbox with bounded queues and structured cancellation |
| ADR-005 | Semantic avatar intents plus deterministic performance scheduling |
| ADR-006 | Orthogonal activity, broadcast, interaction, reasoning, autonomy, and privacy axes |
| ADR-007 | SQLite WAL + migrations for local-first transactional state |
| ADR-008 | History/checkpoints, long-term memory, procedural skills, artifacts, and RAG are separate concerns; prompt exposure uses bounded T0–T4 tiers |
| ADR-009 | Host-owned capability policy and exact approval; MCP is untrusted |
| ADR-010 | LangGraph only behind WorkflowRuntime for durable/background workflows |
| ADR-011 | Memory consolidation and skill learning are separate: idle memory reflection has class-specific authority; skill evolution is WORK-only, replayed, and human-promoted; persona canon is owner-only |
| ADR-012 | No external event broker/microservices until measured scale requires them |
| ADR-013 | SQLite revisions/indexes are primary memory storage; optional Markdown/git projection is for reviewable canon and promoted skills |
| ADR-014 | REALTIME first audio depends on one foreground model stream plus cached/local deterministic context only—no auxiliary AI guard, reflection, or skill scan |

---

## 21. Open product decisions

These do not block the architecture document, but should be answered before their affected phase:

1. Is Windows the only initial target, or must macOS/Linux be P0?
2. Should the UI remain browser-based, or should a Tauri/Electron desktop shell be considered?
3. Can the existing Open-LLM web renderer source/submodule be reused under acceptable licenses, or should it be replaced?
4. Which Live2D model and assets are legally cleared for development, demos, and distribution?
5. Which initial model, ASR, and TTS profiles define the latency target machine?
6. Is Bilibili the first public platform, and are YouTube/Twitch in scope later?
7. May screen/camera/audio ever be retained? If yes, what consent UI and default retention are required?
8. Is the first release single-user/single-machine, or must remote authenticated users connect?
9. Which work capabilities are genuinely needed first: web research, notes, project files, calendar, or something else?
10. What game telemetry sources are acceptable, and what anti-cheat/terms boundaries apply?
11. What viewer identity/memory behavior is desired: anonymous session, platform account, opt-in recognition, or none?
12. What are the persona's public/private canon, safety boundaries, speaking style, and allowed improvisation?
13. Which data must be encrypted at rest, and what deletion/backup guarantees are promised?
14. Which low-risk owner/profile facts may auto-commit, and which always require confirmation? The default proposed here is conservative auto-commit with provenance/undo, never for public, sensitive, contradictory, or canon changes.
15. What are the initial token budgets for T0 canon, T1 personal/session context, T2 memory map, and retrieved T3 records on the reference model?
16. When may background consolidation run: after a conversation, during measured idle time, overnight, or only on explicit request? What token/cost budget is acceptable?
17. Should owner-authored canon and promoted skills be projected to a separate git repository for review/sync, or is database history plus export sufficient initially?

---

## 22. First implementation backlog

This is the recommended first set of issues after the open P0 decisions:

1. Repository scaffold, dependency groups, lockfile, lint/type/test/CI.
2. ADR-001 through ADR-005.
3. EventEnvelope and event schema registry.
4. SessionActor, bounded priority mailbox, supervisor, and cancellation scope.
5. Fake ASR/model/TTS/avatar adapters and a deterministic E2E harness.
6. Interaction state machine with transition/property tests.
7. Versioned WebSocket API for text, audio events, status, utterance, and avatar cues.
8. SQLite schema/migrations for sessions, messages, events, and adapter health.
9. Memory/persona revision schema and P0 ContextSnapshotCompiler with hard budgets, immutable generations, cache, and hot-only fallback.
10. ModelProvider and capability negotiation.
11. Incremental UtteranceSegment validation/fallback and final UtterancePlan assembly.
12. Avatar capability profile, PerformanceDirector, and PresentationScheduler.
13. Port one ASR, model, TTS, and Live2D path.
14. Barge-in/cancellation and ordered audio.
15. Operator mute/stop/neutral/kill controls.
16. OpenTelemetry trace correlation, first-audio dependency assertion, and latency metrics.
17. Two-session isolation E2E, context-generation consistency, fault injection, and four-hour soak runner.

Do not begin with general shell tools, autonomous reflection/skill evolution, all provider ports, multi-agent orchestration, or public chat. The vertical tracer should include the **memory read foundation**—versioned persona/context snapshots and stable history IDs—but postpone learned durable writes until the fast lane is measured. It should prove lifecycle, timing, isolation, cancellation, context coherence, and avatar semantics first.

---

## 23. Definition of “solid base”

The base is ready for feature expansion only when:

- a new adapter can be added without editing the dialogue/agent core;
- two sessions cannot observe or mutate one another's state;
- all long waits and queues are bounded, cancellable, and observable;
- speech can be interrupted without corrupting history or avatar state;
- avatar cues are semantic, scheduled, and recover to neutral;
- activity and reasoning switch with explicit evidence/reason codes and manual override;
- every spoken turn pins a bounded context generation, can fall back to cached hot identity, and never waits for reflection, skill learning, or an auxiliary AI approval service;
- a background job can pause for approval, survive restart, and avoid duplicate effects;
- tool authority is host-owned and cannot be amplified by a child worker, mode, public message, or MCP server;
- memory is tiered, typed, provenance-aware, consent-aware, revisioned, inspectable, correctable, and deletable; canon, learned personal context, and procedural skills have different mutation authority;
- private context is demonstrably excluded from stream-safe output;
- every external adapter has a fake, contract suite, timeout, health state, and fallback;
- replay traces can reproduce important state/policy decisions;
- architecture decisions, migrations, operational runbooks, and compatibility expectations are documented.

---

## 24. Research notes and source map

### Local code

- Open-LLM startup/API: [run_server.py](../Open-LLM/Open-LLM-VTuber/run_server.py), [server.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/server.py), [websocket_handler.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/websocket_handler.py)
- Open-LLM composition/conversation: [service_context.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/service_context.py), [conversations/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/conversations/)
- Open-LLM agent/output: [basic_memory_agent.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/agent/agents/basic_memory_agent.py), [transformers.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/agent/transformers.py), [output_types.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/agent/output_types.py)
- Open-LLM avatar/tools/history: [live2d_model.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/live2d_model.py), [mcpp/](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/mcpp/), [chat_history_manager.py](../Open-LLM/Open-LLM-VTuber/src/open_llm_vtuber/chat_history_manager.py)
- BearCode loop/tools: [agent.py](../Agent/BearCode/agents/agent.py), [tools.py](../Agent/BearCode/agents/tools.py)
- BearCode memory/session: [memory.py](../Agent/BearCode/agents/memory.py), [session.py](../Agent/BearCode/agents/session.py), [session_memory.py](../Agent/BearCode/agents/session_memory.py)
- BearCode skills/evaluation: [skills.py](../Agent/BearCode/agents/skills.py), [skill_evolution.py](../Agent/BearCode/agents/skill_evolution.py), [online_skill_eval.py](../Agent/BearCode/agents/online_skill_eval.py)
- BearCode MCP/subagents: [mcp_client.py](../Agent/BearCode/agents/mcp_client.py), [subagent.py](../Agent/BearCode/agents/subagent.py)
- Letta Code context compilation/tiers: [system-prompt-compilation.ts](../Agent/letta-code/src/backend/local/system-prompt-compilation.ts), [memory-filesystem.ts](../Agent/letta-code/src/agent/memory-filesystem.ts), [memory-runtime.ts](../Agent/letta-code/src/agent/memory-runtime.ts)
- Letta Code versioning/reflection: [memory-git.ts](../Agent/letta-code/src/agent/memory-git.ts), [memory-worktree.ts](../Agent/letta-code/src/agent/memory-worktree.ts), [post-turn-reflection.ts](../Agent/letta-code/src/cli/helpers/post-turn-reflection.ts), [reflection.md](../Agent/letta-code/src/agent/subagents/builtin/reflection.md)
- Letta Code compaction/recall/tools: [compaction.ts](../Agent/letta-code/src/backend/local/compaction.ts), [transcript-search.ts](../Agent/letta-code/src/backend/local/transcript-search.ts), [conversation-search.ts](../Agent/letta-code/src/backend/conversation-search.ts), [memory.ts](../Agent/letta-code/src/tools/impl/memory.ts)

### External architecture references

- Claude Code official lifecycle concepts: [how Claude Code works](https://code.claude.com/docs/en/how-claude-code-works), [hooks guide](https://code.claude.com/docs/en/hooks-guide), [permissions](https://code.claude.com/docs/en/permissions), [memory](https://code.claude.com/docs/en/memory)
- LangGraph: [overview](https://docs.langchain.com/oss/python/langgraph/overview), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [streaming](https://docs.langchain.com/oss/python/langgraph/streaming), [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- LangChain: [agents](https://docs.langchain.com/oss/python/langchain/agents), [middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview), [human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop), [memory concepts](https://docs.langchain.com/oss/python/concepts/memory), [retrieval](https://docs.langchain.com/oss/python/langchain/retrieval)
- Letta official: [Letta Code repository](https://github.com/letta-ai/letta-code), [memory and dreaming](https://docs.letta.com/configuration/memory), [MemFS](https://docs.letta.com/concepts/memfs), [conversations and compaction](https://docs.letta.com/concepts/conversations), [skills](https://docs.letta.com/configuration/skills), [subagents](https://docs.letta.com/configuration/subagents), [permissions](https://docs.letta.com/configuration/permissions), [context repositories](https://www.letta.com/blog/context-repositories/)
- Educational harness patterns: [learn-claude-code hooks](https://github.com/shareAI-lab/learn-claude-code/tree/main/s04_hooks), [permissions](https://github.com/shareAI-lab/learn-claude-code/tree/main/s03_permission), [compaction](https://github.com/shareAI-lab/learn-claude-code/tree/main/s08_context_compact), [memory](https://github.com/shareAI-lab/learn-claude-code/tree/main/s09_memory), [MCP/plugins](https://github.com/shareAI-lab/learn-claude-code/tree/main/s14_mcp_plugin), [workflow runtime](https://github.com/shareAI-lab/learn-claude-code/tree/main/s16_workflow_runtime)
- RAG patterns: [All-in-RAG architecture](https://github.com/datawhalechina/all-in-rag/blob/main/docs/chapter8/01_env_architecture.md), [hybrid retrieval](https://github.com/datawhalechina/all-in-rag/blob/main/docs/chapter8/03_index_retrieval.md), [query routing](https://github.com/datawhalechina/all-in-rag/blob/main/docs/chapter9/04_intelligent_query_routing.md)

---

## 25. Change log

- **2026-08-19:** Implemented the greenfield Python backbone: owned event/session/cancellation contracts, bounded priority mailboxes, context snapshots, typed memory admission and SQLite revisions/FTS, incremental dialogue and fake TTS/model adapters, semantic avatar arbitration, versioned FastAPI/WebSocket tracer, migrations, provenance policy, ADRs, architecture checks, and automated tests.
- **2026-08-19:** Added Letta Code 0.30.25 audit and revised the plan around tiered context snapshots, local deadline-bound recall, isolated background memory consolidation, class-specific promotion, and WORK-only skill evolution. Explicitly removed auxiliary AI checks/reflection/skill scanning from the realtime first-audio path.
- **2026-08-19:** Initial codebase audit, gap analysis, target requirements, architecture, and phased plan.
