# Chainlit vs Streamlit: Comprehensive Multi-Role Analysis

Status: historical framework-selection rationale. This is not the current implementation spec.

For the current built system, use `docs/analysis/2026-03-07-current-implementation-design.md`.

**Date**: 2026-02-05
**Versions Analyzed**: Chainlit 2.9.6 (Jan 20, 2026) | Streamlit 1.54.0 (Feb 4, 2026)
**Analysis Method**: Parallel evaluation from three perspectives (Developer, Architect, SRE)

---

## Executive Summary

This document provides a comprehensive comparison of Chainlit and Streamlit for building AI-powered applications, specifically evaluated for conversational AI / text-to-SQL agent use cases. The analysis was conducted from three distinct professional perspectives to ensure coverage of development experience, architectural fitness, and operational characteristics.

**Key Finding**: Chainlit is the architecturally correct choice for conversational AI agents. Streamlit's re-execution model is fundamentally hostile to chat/agent workloads, though it excels for data dashboards and analytics.

---

## Table of Contents

1. [Unanimous Findings](#1-unanimous-findings)
2. [Developer Perspective](#2-developer-perspective)
3. [Architect Perspective](#3-architect-perspective)
4. [SRE Perspective](#4-sre-perspective)
5. [Cross-Role Insights](#5-cross-role-insights)
6. [Decision Matrix](#6-decision-matrix)
7. [Recommendation](#7-recommendation)

---

## 1. Unanimous Findings

These findings were consistent across all three analysis perspectives:

| Finding | Implication |
|---------|-------------|
| **Chainlit is purpose-built for conversational AI; Streamlit is purpose-built for data dashboards** | Framework selection should be based on primary use case |
| **Streamlit's re-execution model is architecturally hostile to chat/agent workloads** | Every widget interaction re-runs the entire Python script from top to bottom |
| **Both require sticky sessions to scale horizontally** | In-process session state prevents stateless scaling |
| **Neither provides true multi-tenancy out of the box** | Tenant-level isolation must be built manually |
| **Streamlit has dramatically larger ecosystem and corporate backing (Snowflake)** | 43K vs 11.4K GitHub stars; Snowflake resources vs community maintenance |
| **Chainlit's Jan 2026 CVEs (ChainLeak) are a real production concern** | CVE-2026-22218, CVE-2026-22219, CVE-2025-68492 — all patched in 2.9.4+ |

---

## 2. Developer Perspective

### 2.1 Developer Experience (DX)

#### Setup Time
Both frameworks achieve zero-friction setup:

```bash
# Chainlit
pip install chainlit
chainlit hello        # verifies install with demo UI
chainlit run app.py   # starts your app

# Streamlit
pip install streamlit
streamlit hello       # shows gallery of demos
streamlit run app.py  # starts your app
```

**Verdict**: Tie

#### Learning Curve

| Framework | Initial Learning | Long-term Complexity |
|-----------|-----------------|---------------------|
| **Chainlit** | Slightly steeper (decorators, async) | Fewer surprises — event model is predictable |
| **Streamlit** | Easier minute one | Harder at hour ten — re-execution model causes confusion |

Chainlit uses 5-6 decorators (`@cl.on_chat_start`, `@cl.on_message`, `@cl.on_stop`, `@cl.on_chat_end`, `@cl.on_chat_resume`) and a handful of classes (`cl.Message`, `cl.Step`, `cl.Action`, `cl.Element`).

Streamlit's re-execution model is the single biggest conceptual hurdle: every widget interaction re-runs the entire script, requiring careful state management via `st.session_state`.

#### Documentation Quality

| Framework | Quality | Notes |
|-----------|---------|-------|
| **Chainlit** | Good | Well-organized, covers lifecycle hooks, streaming, MCP, auth. Some gaps since community transition (May 2025) |
| **Streamlit** | Excellent | Best-in-class for Python frameworks. Comprehensive API reference, tutorials, active forum |

**Verdict**: Streamlit wins decisively

#### IDE Support & Debugging

**Chainlit** provides specific debugging pattern:
```python
if __name__ == "__main__":
    from chainlit.cli import run_chainlit
    run_chainlit(__file__)
```
This enables breakpoints in VS Code/PyCharm. The `-w` flag enables hot-reload.

**Streamlit** re-runs the full script on file changes automatically. Breakpoints hit on every widget click due to re-execution model.

**Verdict**: Slight edge to Chainlit for debugging complex logic

### 2.2 Code Architecture

#### Chainlit: Event-Driven / Decorator Pattern

```python
import chainlit as cl

@cl.on_chat_start
async def start():
    cl.user_session.set("history", [])

@cl.on_message
async def handle(message: cl.Message):
    history = cl.user_session.get("history")
    history.append({"role": "user", "content": message.content})

    msg = cl.Message(content="")
    await msg.send()

    async for token in my_llm_stream(history):
        await msg.stream_token(token)

    await msg.update()
    history.append({"role": "assistant", "content": msg.content})
```

Key traits:
- **Async-native**: All handlers are `async def` by default
- **Session as explicit object**: `cl.user_session.set()` / `.get()` — per WebSocket connection
- **Steps and nesting**: `@cl.step` creates visible execution traces in UI
- **Separation of concerns**: Python is backend only; frontend is pre-built React

#### Streamlit: Imperative / Script-as-App Pattern

```python
import streamlit as st

st.title("Chat Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

if prompt := st.chat_input("Ask something"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        response = st.write_stream(my_llm_stream(st.session_state.messages))

    st.session_state.messages.append({"role": "assistant", "content": response})
```

Key traits:
- **Top-to-bottom re-execution**: Every interaction re-runs entire script
- **Widgets are inline**: UI elements declared where they appear
- **Caching as first-class**: `@st.cache_data`, `@st.cache_resource` are essential
- **No native async**: Synchronous by default; must bridge with `asyncio.run()`

#### State Management Comparison

| Aspect | Chainlit | Streamlit |
|--------|----------|-----------|
| Mechanism | `cl.user_session` (dict-like, per WS connection) | `st.session_state` (dict-like, per browser tab) |
| Persistence | Optional: SQLAlchemy, PG, custom data layers | None built-in |
| Thread resume | `@cl.on_chat_resume` — built-in | Must serialize/deserialize manually |
| Cache | `@cl.cache` for expensive computations | `@st.cache_data`, `@st.cache_resource` (more mature; session-scoped since v1.53) |

**Verdict**: Chainlit's event-driven model is superior for chat/agent applications

### 2.3 AI/LLM Integration

| Capability | Chainlit | Streamlit |
|------------|----------|-----------|
| **Native streaming** | First-class: `await msg.stream_token(token)` | `st.write_stream()` — adequate |
| **Step tracing** | Built-in nested execution traces visible in UI | `st.status()` only — single container |
| **MCP support** | Native as of v2.x | None |
| **LangChain integration** | `LangchainCallbackHandler` — automatic step rendering | Manual wiring required |
| **LlamaIndex integration** | `LlamaIndexCallbackHandler` | Manual wiring required |
| **Multi-modal** | Native: images, files, audio, video as `Element` objects | Basic support |
| **Human-in-the-loop** | `AskUserMessage`, `AskFileMessage`, `AskActionMessage` | Build it yourself |
| **Chat profiles** | Switch agent configs from UI | Build it yourself |
| **Human feedback** | Built-in thumbs up/down with persistence | Build it yourself |

**Verdict**: Chainlit wins by a wide margin for AI/LLM use cases

### 2.4 Testing

| Framework | Testing Support |
|-----------|-----------------|
| **Chainlit** | Minimal. No built-in test runner, no mock message facility, no test client |
| **Streamlit** | `st.testing.v1.AppTest` — headless test runner, widget simulation, pytest integration |

```python
# Streamlit testing example
from streamlit.testing.v1 import AppTest

def test_chat():
    at = AppTest.from_file("app.py")
    at.run()
    at.chat_input[0].set_value("Hello").run()
    assert "Hello" in at.chat_message[1].markdown[0].value
```

**Verdict**: Streamlit wins definitively

### 2.5 Known Pain Points

#### Chainlit Pain Points
1. **Community maintenance risk**: Chainlit SAS stepped back May 2025; project now community-maintained
2. **Security vulnerabilities**: Three CVEs disclosed Jan 2026 (patched in 2.9.4+)
3. **No layout beyond chat**: No `st.columns()` equivalent; chat-only UI
4. **Recoil dependency**: `@chainlit/react-client` depends on abandoned Recoil library
5. **Testing story is weak**: No `AppTest` equivalent
6. **Sync/async confusion**: Mixing sync and async code can cause event loop issues

#### Streamlit Pain Points
1. **Re-execution model for chat**: Every user message re-runs entire script (partially mitigated by `st.fragment` for isolating reruns to specific functions, but the fundamental model remains)
2. **No native async**: Must bridge with `asyncio.run()`
3. **Widget identity fragility**: Changing widget properties can reset state
4. **No WebSocket push**: Cannot push messages outside rerun cycle
5. **Heavy dependency tree**: Pandas, NumPy, Pyarrow required even if unused
6. **Agent step visualization**: No built-in nested trace tree

---

## 3. Architect Perspective

### 3.1 System Architecture

| Dimension | Chainlit | Streamlit |
|-----------|----------|-----------|
| **Server** | FastAPI (ASGI) + python-socketio on Uvicorn | Tornado (custom protocol layer) |
| **Frontend** | React + TypeScript SPA (pre-built, opinionated chat UI) | React SPA (rendering engine for declarative widget tree) |
| **Communication** | Bidirectional Socket.IO (WebSocket + long-polling fallback) | WebSocket via Tornado using Protocol Buffers |
| **Serialization** | JSON over Socket.IO | Protocol Buffers over WebSocket |
| **Data Flow** | Event-driven, push-based | Re-execution model (script replay) |

#### Data Flow Diagrams

**Chainlit**:
```
User Input (Browser)
  --> Socket.IO event (WebSocket)
  --> Server: on_message callback
  --> Your Python logic (LLM call, DB query, etc.)
  --> cl.Message().send() / cl.Step()
  --> Socket.IO event (WebSocket)
  --> Browser: renders message/step in chat UI
```

**Streamlit**:
```
User Interaction (widget change)
  --> WebSocket: send widget state delta (protobuf)
  --> Server: FULL SCRIPT RE-EXECUTION from line 1
  --> Server: generate new widget tree as protobuf deltas
  --> WebSocket: send deltas back to frontend
  --> Browser: re-render changed components
```

### 3.2 Scalability

| Aspect | Chainlit | Streamlit |
|--------|----------|-----------|
| **Session Management** | In-process; each WebSocket = `ChainlitSession` in memory | In-process; each tab = thread + `session_state` in memory |
| **Horizontal Scaling** | Sticky sessions required; no built-in cross-instance state | Sticky sessions required + media file split-brain problem |
| **Concurrency Model** | Async (asyncio) — I/O-bound work doesn't block | Multi-threaded within single Tornado process; GIL serializes CPU |
| **Practical Ceiling** | Dozens to low hundreds per instance | ~50-100 concurrent active users before degradation |
| **Partial Rerun** | N/A (event-driven — only triggered code runs) | `st.fragment` allows isolating reruns to decorated functions, reducing but not eliminating overhead |

**Media File Split-Brain (Streamlit-specific)**: Media files are stored on local filesystem and served over HTTP. In multi-replica deployment, HTTP request may hit different replica than WebSocket session, causing `MediaFileStorageError`. Requires sticky sessions or external storage (S3).

### 3.3 Security Model

| Aspect | Chainlit | Streamlit |
|--------|----------|-----------|
| **Authentication** | Password + OAuth + Header delegation | OIDC only (since v1.42, Feb 2025) |
| **Authorization** | Application-level (implement in callbacks) | Application-level |
| **Token Management** | JWT with `CHAINLIT_AUTH_SECRET` | OIDC identity cookie (30-day expiry) |
| **CORS** | Configurable via `.chainlit/config.toml` | Limited configurability |
| **Data Isolation** | Socket.IO room-per-connection | Session-scoped; `st.cache_*` shared across sessions by default, but session-scoped caching available since v1.53 |

#### Known Vulnerabilities (Chainlit)

| CVE | Severity | Description | Fixed In |
|-----|----------|-------------|----------|
| CVE-2025-68492 | Medium | Authorization bypass — users can view/take ownership of other users' threads | 2.8.5 |
| CVE-2026-22218 | High (7.1 CVSS 4.0) | Arbitrary file read via `/project/element` update flow | 2.9.4 |
| CVE-2026-22219 | High | SSRF via `/project/element` when using SQLAlchemy data layer | 2.9.4 |

**ChainLeak Attack Chain**: SSRF can reach cloud metadata endpoints (169.254.169.254), enabling cloud environment takeover by leaking IAM credentials.

### 3.4 Integration Patterns

| Capability | Chainlit | Streamlit |
|------------|----------|-----------|
| **FastAPI Integration** | First-class via `mount_chainlit()` | Experimental via `st.App` ASGI entry point (v1.53+); Streamlit is migrating from Tornado to Starlette long-term |
| **LLM Framework Integration** | Native callbacks for LangChain, LlamaIndex, etc. | Manual wiring |
| **MCP Support** | Native as of v2.x | None |
| **Multi-Platform Delivery** | Web, Copilot widget, Slack, Teams, Discord | Web only |
| **REST API** | Via FastAPI parent app | None |
| **Webhooks** | Via FastAPI integration | None built-in |
| **Database Connectors** | BYO | First-class via `st.connection()` |

**FastAPI Mount Example (Chainlit)**:
```python
from fastapi import FastAPI
from chainlit.utils import mount_chainlit

app = FastAPI()

@app.get("/api/health")
def health():
    return {"status": "ok"}

mount_chainlit(app=app, target="my_cl_app.py", path="/chat")
```

### 3.5 Architectural Constraints & Lock-In

| Dimension | Chainlit | Streamlit |
|-----------|----------|-----------|
| **UI Constraint** | Chat-first (cannot build dashboards, CRUD apps) | Widget-driven (cannot define custom HTTP endpoints) |
| **Transport Constraint** | Requires WebSocket support | Requires WebSocket support |
| **Execution Constraint** | Async-first | Re-execution model pervades all code |
| **Lock-In Risk** | Low-Moderate (thin lifecycle hooks) | Moderate-High (execution model deeply coupled) |
| **Migration Path** | Replace UI layer; agent logic is portable | Must rewrite entire UI layer and restructure control flow |

### 3.6 Fitness for Purpose

#### Choose Chainlit When:
- Building conversational AI (chatbot, agent, copilot)
- Need to integrate with existing backend (FastAPI mount)
- Need multi-platform delivery (web, Slack, Teams, Discord)
- Need persistent conversation threads with history
- Need real-time token streaming
- Team has backend engineering experience

#### Choose Streamlit When:
- Building data dashboards or exploratory analytics
- Prototyping rapidly
- Team is data scientists, not software engineers
- Want managed hosting (Community Cloud, Snowflake SiS)
- Building internal tools with <50 concurrent users

#### Choose Neither When:
- Need production-grade system with thousands of concurrent users
- Need fine-grained authorization (RBAC, ABAC)
- Need general-purpose web application with forms, CRUD, complex layouts

---

## 4. SRE Perspective

### 4.1 Operational Characteristics

| Metric | Chainlit | Streamlit |
|--------|----------|-----------|
| **Web server** | FastAPI on Uvicorn (ASGI) | Tornado (single-threaded event loop) |
| **Concurrency model** | Async coroutines; multiple Uvicorn workers via Gunicorn | Single process, multi-threaded |
| **Script execution** | Event-driven (code runs only when triggered) | **Full script re-execution** on every widget change |
| **Worker scaling** | N Uvicorn workers behind Gunicorn | No native multi-worker; scale via multiple processes |

### 4.2 Resource Consumption

| Metric | Chainlit | Streamlit |
|--------|----------|-----------|
| **Idle memory (no users)** | ~60-90 MB | ~80-120 MB |
| **Per-session memory** | ~2-5 MB | ~15-50 MB (up to 100 MB with DataFrames) |
| **CPU (idle)** | <1% single core | <1% single core |
| **CPU (active, per user)** | Low (dominated by LLM call) | **High** (full script re-execution per interaction) |
| **GC behavior** | Standard CPython GC; session cleanup on disconnect | **Memory leak pattern**: `session_state` persists after tab close (GitHub #12506 — confirmed bug, closed as "not planned" to fix) |

### 4.3 Observability

| Capability | Chainlit | Streamlit |
|------------|----------|-----------|
| **OpenTelemetry** | **Native** — built-in OTEL SDK | None built-in |
| **Prometheus metrics** | Not built-in; add via `prometheus-fastapi-instrumentator` | Not built-in |
| **Structured logging** | Python `logging` module | Python `logging` to stdout |
| **Health check endpoint** | Must add via FastAPI (~5 lines) | `/_stcore/health` (built-in, returns 200) |
| **Literal AI integration** | Was first-party; `literalai-python` repo archived (Apr 2025) — may be deprecated | N/A |

### 4.4 Reliability

| Failure Mode | Chainlit | Streamlit |
|--------------|----------|-----------|
| **OOM kill** | Less likely (~5 MB/session) | **Common in production** (RSS grows monotonically) |
| **WebSocket disconnect** | Socket.IO auto-reconnect with exponential backoff | Reconnects, but **app state can be lost** |
| **Session persistence** | Optional data layer (SQLAlchemy; Literal AI integration may be deprecated) | None — `session_state` is ephemeral |
| **Backend crash** | Worker crash isolated; Gunicorn respawns | Process crash kills ALL sessions |
| **Graceful shutdown** | Uvicorn handles SIGTERM with configurable timeout | Sessions terminate immediately |

### 4.5 Performance

| Metric | Chainlit | Streamlit |
|--------|----------|-----------|
| **Cold start time** | ~1.5-3s | ~2-4s |
| **First meaningful paint** | ~1-2s after connection | ~2-3s |
| **Latency per interaction** | <10ms framework overhead | **50-500ms+ framework overhead** (script re-execution) |
| **Streaming latency** | Native token-by-token over WebSocket | Buffered via `st.write_stream` |
| **Throughput (concurrent)** | ~500-1000 WebSocket connections per worker | ~50-100 active users before degradation |

### 4.6 Containerization

| Aspect | Chainlit | Streamlit |
|--------|----------|-----------|
| **Recommended base** | `python:3.11-slim-bookworm` | `python:3.11-slim-bookworm` |
| **Minimal image size** | ~250-350 MB | ~300-400 MB |
| **Entrypoint** | `chainlit run app.py -h --host 0.0.0.0 --port 8000` | `streamlit run app.py --server.address 0.0.0.0 --server.port 8501` |
| **Default port** | 8000 | 8501 |
| **Resource limits** | 256 MB min / 512 MB comfortable | **512 MB min / 1 GB recommended** |
| **Key gotcha** | Must expose WebSocket path `/ws/socket.io/` | Must set `server.headless=true` in containers |

### 4.7 Kubernetes Orchestration

| Aspect | Chainlit | Streamlit |
|--------|----------|-----------|
| **Liveness probe** | `httpGet` on custom `/health` or `tcpSocket` on 8000 | `httpGet` on `/_stcore/health` |
| **Sticky sessions** | **REQUIRED** | **REQUIRED** (plus media file split-brain mitigation) |
| **HPA scaling metric** | CPU or custom (active WebSocket connections) | CPU/memory (no metrics exposed) |
| **Pod disruption** | Pre-stop hook sends Socket.IO disconnect | No graceful drain; sessions lost |

#### Scaling Formula (Rule of Thumb)

| Concurrent Users | Chainlit Pods (512 MB, 0.5 vCPU) | Streamlit Pods (1 GB, 1 vCPU) |
|------------------|----------------------------------|-------------------------------|
| **100** | 1 pod | 2-3 pods |
| **1,000** | 3-5 pods | 20-50 pods |
| **10,000** | 25-35 pods | 200-500 pods (impractical) |

### 4.8 Cost Profile (AWS EKS, us-east-1, on-demand)

| Scale | Chainlit (monthly) | Streamlit (monthly) | Ratio |
|-------|-------------------|---------------------|-------|
| **100 users** | ~$30 | ~$70-210 | 2-7x |
| **1,000 users** | ~$150-210 | ~$1,400-3,500 | 9-17x |
| **10,000 users** | ~$750-1,050 | ~$14,000-35,000 | **15-30x** |

### 4.9 Security Operations

| Metric | Chainlit | Streamlit |
|--------|----------|-----------|
| **Direct dependencies** | ~25-30 | ~35-45 |
| **Transitive dependencies** | ~80-100 | ~120-150 |
| **High-risk dependencies** | `python-socketio`, `sqlalchemy`, `httpx` | `pyarrow`, `tornado`, `protobuf` |
| **CVE history (recent)** | 3 CVEs in Jan 2026 (patched) | No major recent CVEs |
| **Security team** | Community-maintained | Snowflake-backed, bug bounty program |

### 4.10 SLI/SLO Recommendations

| SLI | Chainlit Target | Streamlit Target |
|-----|-----------------|------------------|
| **Availability** | 99.9% | 99.5% |
| **Latency (p95 time-to-first-token)** | <2s | <5s |
| **Error budget** | 0.1% per 30-day window | 0.5% per 30-day window |
| **Session durability (1 hour)** | 99% with sticky sessions | 95% |

### 4.11 Operational Burden Score

| Dimension | Chainlit (1=low, 5=high) | Streamlit (1=low, 5=high) |
|-----------|-------------------------|---------------------------|
| Resource efficiency | **1** | 4 |
| Observability out-of-box | **2** | 4 |
| Scaling complexity | **2** | 5 |
| Security posture | 4 | **2** |
| Upgrade risk | 4 | **2** |
| K8s friendliness | **2** | 3 |
| Community/support | 3 | **1** |
| **Overall** | **2.6** | **3.0** |

---

## 5. Cross-Role Insights

### 5.1 Where Roles See the Same Feature Differently

#### FastAPI Composability

| Role | Perspective |
|------|-------------|
| **Developer** | "Convenient for adding health checks" |
| **Architect** | "Architecturally critical — Chainlit is composable, Streamlit is monolithic" |
| **SRE** | "Can add `/metrics` with `prometheus-fastapi-instrumentator` in 5 lines" |

#### Streamlit's Re-Execution Model

| Role | Perspective |
|------|-------------|
| **Developer** | "Confusing at hour 10 — must guard all state" |
| **Architect** | "Fundamentally hostile to long-running stateful processes" |
| **SRE** | "10 widget clicks = 10 full script re-runs — why it freezes at 100 users" |

#### Security Concerns

| Role | Chainlit Risk | Streamlit Risk |
|------|---------------|----------------|
| **Developer** | "CVEs are patched, community is active" | "No major recent CVEs" |
| **Architect** | "ChainLeak enables cloud takeover via SSRF to IMDS" | "Shared `st.cache_data` is data-isolation footgun" |
| **SRE** | "Block 169.254.169.254 in network policy even after patching" | "Memory leak is the real operational CVE" |

### 5.2 Role-Specific Unique Insights

#### Developer-Only
- Testing: Streamlit has `AppTest`; Chainlit has essentially nothing
- Async: Chainlit is async-native; Streamlit requires `asyncio.run()` bridging
- Recoil dependency: `@chainlit/react-client` depends on abandoned library

#### Architect-Only
- Lock-in gradient: Chainlit = low-moderate; Streamlit = moderate-high
- Media file split-brain: Streamlit-specific multi-replica problem
- Migration path: Chainlit agent logic is portable; Streamlit apps are deeply coupled

#### SRE-Only
- Memory per session: 2-5 MB vs 15-50 MB
- Memory leak: Streamlit `session_state` persists after tab close (confirmed bug, closed as "not planned" — team won't fix)
- Failure isolation: Chainlit worker crash is isolated; Streamlit process crash kills ALL sessions

---

## 6. Decision Matrix

| Dimension | Chainlit | Streamlit | Decisive Role |
|-----------|----------|-----------|---------------|
| Chat/Agent DX | **Winner** | Workable | Developer |
| Dashboard/Analytics DX | Not designed | **Winner** | Developer |
| Streaming quality | **Winner** | Adequate | Developer |
| Testing | Weak | **Winner** | Developer |
| Documentation | Good | **Winner** | Developer |
| Execution model fit (chat) | **Winner** | Hostile | Architect |
| Backend composability | **Winner** | Monolithic | Architect |
| Lock-in risk | **Lower** | Higher | Architect |
| API surface | **Winner** | None | Architect |
| Multi-platform delivery | **Winner** | Web only | Architect |
| Horizontal scaling cost | **Winner** (15-30x) | Expensive | SRE |
| Memory efficiency | **Winner** | 3-10x more | SRE |
| Observability | **Winner** | Almost nothing | SRE |
| Security posture | Concerning | **Winner** | SRE |
| Failure isolation | **Winner** | Process-level | SRE |
| Community/longevity | Uncertain | **Winner** | All |

---

## 7. Recommendation

### For MindsDB Text-to-SQL Agent POC

**Recommendation**: Chainlit

**Rationale** (stacking across roles):

| Role | Reason |
|------|--------|
| **Developer** | Native streaming, step tracing, MCP support, human-in-the-loop — all built-in |
| **Architect** | Event-driven model fits agent workflows; FastAPI composability; conversation persistence via pluggable data layer |
| **SRE** | 5-10x more resource-efficient per session; native OTEL; lighter operational burden |

### Risk Mitigation

The accepted risk is Chainlit's community maintenance model and recent CVEs. Mitigate with:

1. **Nginx reverse proxy** for TLS termination, rate limiting, primary authentication
2. **Network policy** blocking cloud metadata endpoints (169.254.169.254) to prevent SSRF exploitation
3. **Version pinning** — test upgrades in staging before production deployment
4. **Quarterly review** of maintainer community health and CVE disclosures

### When to Reconsider

Re-evaluate this decision if:
- Primary use case shifts from chat to dashboard/analytics
- Concurrent user requirement exceeds 5,000 (consider custom architecture)
- Snowflake integration becomes a requirement (Streamlit in Snowflake may be preferable)
- Chainlit community shows signs of abandonment (no releases for >3 months)
- **Streamlit completes Tornado → Starlette/ASGI migration**: As of v1.53 (Jan 2026), Streamlit has begun experimental Starlette support with `st.App`. Their stated long-term goal is full migration from Tornado to Starlette (GitHub #13600). If completed, this would make Streamlit mountable inside FastAPI — eroding one of Chainlit's key architectural advantages. Monitor this migration quarterly.

---

## Appendix A: Code Comparison

### Simple Chat Agent

**Chainlit** (25 lines):
```python
import chainlit as cl
from openai import AsyncOpenAI

client = AsyncOpenAI()

@cl.on_chat_start
async def start():
    cl.user_session.set("history", [
        {"role": "system", "content": "You are a helpful assistant."}
    ])

@cl.on_message
async def handle(message: cl.Message):
    history = cl.user_session.get("history")
    history.append({"role": "user", "content": message.content})

    msg = cl.Message(content="")
    await msg.send()

    stream = await client.chat.completions.create(
        model="gpt-4o",
        messages=history,
        stream=True,
    )

    async for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        await msg.stream_token(token)

    await msg.update()
    history.append({"role": "assistant", "content": msg.content})
```

**Streamlit** (28 lines):
```python
import streamlit as st
from openai import OpenAI

client = OpenAI()

st.title("Chat Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "system", "content": "You are a helpful assistant."}
    ]

for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

if prompt := st.chat_input("Ask something"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        stream = client.chat.completions.create(
            model="gpt-4o",
            messages=st.session_state.messages,
            stream=True,
        )
        response = st.write_stream(
            (chunk.choices[0].delta.content or "" for chunk in stream)
        )

    st.session_state.messages.append({"role": "assistant", "content": response})
```

**What Chainlit gives for free**: Streaming UI, message history display, copy buttons, avatar icons, responsive layout, WebSocket connection management, auto-reconnect, dark/light mode toggle, feedback buttons.

**What Streamlit gives for free**: Streaming UI, basic chat layout, responsive design. No copy buttons, no avatars, no dark mode toggle (must configure separately), no feedback buttons.

---

## Appendix B: Deployment Checklist

### Chainlit Production Deployment

- [ ] Run behind Nginx reverse proxy with WebSocket support
- [ ] Configure sticky sessions at load balancer
- [ ] Add `/health` and `/ready` endpoints via FastAPI
- [ ] Add Prometheus metrics via `prometheus-fastapi-instrumentator`
- [ ] Configure OTEL exporter for traces
- [ ] Set `CHAINLIT_AUTH_SECRET` for JWT signing
- [ ] Block 169.254.169.254 in network policy (SSRF mitigation)
- [ ] Pin Chainlit version; test upgrades in staging
- [ ] Configure data layer for conversation persistence (optional)

### Streamlit Production Deployment

- [ ] Run behind reverse proxy with WebSocket support
- [ ] Configure sticky sessions at load balancer
- [ ] Set `server.headless=true` in container
- [ ] Set aggressive `server.disconnected_timeout` (e.g., 60s)
- [ ] Externalize media storage to S3 (multi-replica)
- [ ] Add memory limits and scheduled restarts (memory leak mitigation)
- [ ] Use `streamlit_healthcheck` for liveness probes
- [ ] Monitor RSS growth and set OOM alerts at 60% of limit

---

## Appendix C: Verification Notes

This document was reviewed against primary sources on 2026-02-05. Key verification results:

| Claim | Source | Status |
|-------|--------|--------|
| Chainlit 2.9.6 (Jan 20, 2026) | GitHub releases, PyPI | Confirmed |
| Streamlit 1.54.0 (Feb 4, 2026) | PyPI (docs only cover through 1.53.0) | Confirmed |
| CVE-2026-22218 severity | NVD (CVSS 4.0: 7.1 HIGH) | **Corrected** from initial "Medium (6.5)" |
| CVE-2026-22219 (SSRF) | Zafran ChainLeak disclosure, NVD | Confirmed |
| ChainLeak attack chain (SSRF → IMDS → cloud takeover) | Zafran Security research | Confirmed |
| Recoil dependency in `@chainlit/react-client` | npm 0.3.1 (Jan 2026), GitHub #1448 (still open) | Confirmed |
| Streamlit `st.App` ASGI support | Streamlit 1.53.0 release notes, GitHub #13600 | Confirmed |
| Tornado → Starlette long-term migration | GitHub #13600: explicit goal statement | **Added** — not in initial analysis |
| `session_state` memory leak | GitHub #12506 (closed as "not planned") | **Corrected** — initial analysis said "confirmed bug"; actual status is wontfix |
| Session-scoped `st.cache_data` | Streamlit 1.53.0 release notes | **Added** — partially mitigates shared cache data isolation concern |
| `st.fragment` partial reruns | Streamlit docs | **Added** — partially mitigates re-execution overhead |
| Literal AI repo status | GitHub: `literalai-python` archived Apr 2025, `literalai-docs` archived Jun 2025 | **Added** — integration may be deprecated |
| Chainlit GitHub stars ~11.4K | GitHub, Django Packages | Confirmed |
| Streamlit GitHub stars ~43.2K | GitHub, Snyk | Confirmed |
