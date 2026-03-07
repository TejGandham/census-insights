# Text-to-SQL Landscape: How We Compare to Industry Leaders

**Date:** 2026-02-07
**Purpose:** Evaluate our Census Agent's text-to-SQL architecture against production-grade systems from Google, Amazon, Snowflake, Databricks, and open-source frameworks.

---

## 1. Executive Summary

Our architecture — catalog-first discovery + agentic tool-calling + error recovery with metadata refresh — follows the same design patterns used by Google's BigQuery Conversational Analytics, Amazon's Bedrock Agents, and Snowflake's Cortex Analyst. These are the three most battle-tested production text-to-SQL systems in the industry.

The major architectural choices are validated:

| Decision | Industry Consensus | Our Implementation |
|----------|-------------------|---------------------|
| Agentic tool-calling over single-shot prompting | Universal for large schemas | Yes (GPT-4.1 + 3 tools) |
| Schema discovery before SQL generation | Universal | Yes (search_catalog first) |
| Error recovery with metadata refresh | Universal | Yes (_classify_sql_error + _auto_search_catalog) |
| Separate analytical vs. export modes | Uncommon — we're ahead here | Yes (sql_query vs export_csv) |
| Temperature ramping on errors | Used by Google Gemini, some open-source | Yes (0.0 -> 0.3) |

The primary gap is **search quality**: our ILIKE-based catalog search is weaker than the vector-embedding and semantic-model approaches used by leaders. This is the single highest-ROI improvement available.

---

## 2. Major Production Systems — Deep Dive

### 2.1 Google: BigQuery Conversational Analytics + Gemini

**Scale:** Billions of queries/day across BigQuery's global user base.

**Architecture (as of late 2025):**

Google's approach has three tiers:

1. **Gemini in BigQuery** (inline SQL assistance): Autocomplete, SQL generation from natural language, query explanation, error fixing. Uses schema context from the current project. Single-shot prompting with schema injected into context.

2. **BigQuery Data Canvas**: Visual, node-based interface where users chain natural language queries. Gemini finds relevant tables, generates SQL, visualizes results. Multi-step — each node builds on previous results.

3. **Conversational Analytics (data agents)**: The most sophisticated tier. Users create **data agents** that define:
   - **Knowledge sources**: specific tables, views, or UDFs the agent can access
   - **Custom metadata**: table/field descriptions, business glossaries
   - **Instructions**: how to interpret and query the data
   - **Verified queries ("golden queries")**: pre-approved SQL for specific question types

**Schema Discovery:** Data agents have bounded scope (you select which tables they can see). Within that scope, metadata is auto-discovered from BigQuery's Information Schema. For the Gemini CLI path, Google uses MCP Toolbox for Databases with `list_tables` and `describe_table` tools — the same tool-calling pattern we use.

**Error Recovery:** Gemini automatically retries with corrections by re-invoking schema tools to refresh metadata. Google's ADK (Agent Development Kit) uses a multi-agent pipeline: orchestrator → understanding → generation → review → execution. The review agent validates SQL before execution.

**Key Innovation:** "Verified queries" — pre-approved SQL templates that the agent matches against incoming questions. When a match is found, the agent uses the verified SQL instead of generating from scratch. This dramatically improves accuracy for known question patterns.

**How We Compare:**
| Aspect | Google | Us |
|--------|--------|-----|
| Schema scope | Bounded by agent config (select tables) | All ~1,193 tables searchable via catalog |
| Discovery mechanism | Information Schema + MCP tools | search_catalog tool against acs_catalog |
| Verified queries | Yes — golden SQL for known patterns | No — all SQL is generated fresh |
| Multi-agent pipeline | Yes (understand → generate → review → execute) | Single agent with tool loop |
| Error recovery | Re-invoke schema tools, retry | _classify_sql_error + _auto_search_catalog |

**Verdict:** Our architecture is functionally equivalent to Google's ADK path. We lack verified queries (a significant accuracy booster for known patterns) and the multi-agent review step.

---

### 2.2 Amazon: Bedrock Agents + RASL

**Scale:** Enterprise customers with 100+ tables, petabyte-scale Redshift/Athena warehouses. Amazon's own WWRR organization executes 450,000+ SQL queries annually against their data warehouses.

**Architecture:**

Amazon's approach uses **Bedrock Agents** with Lambda-backed action groups:

1. **Dynamic Schema Discovery**: Two Lambda tools — `list_tables` (returns table names + descriptions) and `describe_table` (returns columns, types, sample values for a specific table). The agent calls these at runtime, not at prompt construction time.

2. **Error Handling Pipeline**: When SQL execution fails, the error code is classified (syntax, permission, missing entity). Based on classification:
   - **Syntax error**: Agent regenerates SQL with the error message as context
   - **Missing entity**: Agent re-calls `describe_table` to refresh schema knowledge
   - **Permission error**: Agent reports to user

3. **Metadata Enrichment**: Amazon emphasizes that raw schema is insufficient. They recommend enriching Athena/Glue catalog metadata with:
   - Human-readable column descriptions
   - Sample values for categorical columns
   - Business glossary mappings
   - Table relationship documentation

**RASL (Amazon Research, 2025):** A research paper from Amazon on "Retrieval Augmented Schema Linking for Massive Database Text-to-SQL." Key insight: decompose schemas into **discrete semantic units** (table descriptions, column descriptions, relationship descriptions) and index them separately for targeted retrieval. This outperforms dumping the entire schema into the prompt, especially for databases with 100+ tables.

**BGL Case Study (2026):** Australian financial services company with 400+ analytics tables. Uses Claude Agent SDK + Bedrock AgentCore. Demonstrates the pattern at our exact scale (~400-1200 tables).

**How We Compare:**
| Aspect | Amazon Bedrock | Us |
|--------|---------------|-----|
| Schema discovery | list_tables + describe_table (2 tools) | search_catalog (1 tool, two-phase) |
| Error classification | Explicit error code routing | _classify_sql_error with regex pattern matching |
| Metadata enrichment | Glue catalog + manual descriptions | acs_catalog has table_title + variable labels |
| Schema linking | RASL — vector-indexed semantic units | ILIKE keyword matching |
| Scale validated | 400+ tables (BGL), 1000+ tables (WWRR) | ~1,193 tables |

**Verdict:** Our error recovery pattern matches Amazon's. Their RASL approach to schema linking (vector-indexed semantic units) is more sophisticated than our ILIKE search. However, our acs_catalog with 30K+ labeled variables provides rich metadata that compensates — the agent gets human-readable labels like "MEDIAN HOUSEHOLD INCOME IN THE PAST 12 MONTHS" rather than just column names.

---

### 2.3 Snowflake: Cortex Analyst + Arctic-Text2SQL-R1

**Scale:** Production deployment across Snowflake's customer base. Claims 90%+ accuracy on real-world use cases.

**Architecture:**

Cortex Analyst is the most technically documented production system. It uses:

1. **Semantic Model (YAML)**: The cornerstone innovation. A human-authored YAML file that describes:
   - Tables and their business meaning
   - Columns with descriptions, data types, sample values
   - Metrics (calculated measures with formulas)
   - Dimensions (categorical groupings)
   - Time grains (date hierarchies)
   - Relationships between tables
   - Verified queries for known patterns

   Think of it as "onboarding documentation for a new analyst who knows SQL but doesn't know your data."

2. **Multi-LLM Agentic System**: Uses a collection of Llama and Mistral models (not just one). Multiple specialized agents interact with guardrails at every step:
   - Intent classification agent
   - Schema linking agent
   - SQL generation agent
   - SQL validation agent
   - Result interpretation agent

3. **Cortex Search Integration**: When questions reference values not in the semantic model (e.g., "sales for Acme Corp"), Cortex Search does fuzzy value matching against actual database contents.

**Accuracy Claims:**
- 90%+ on real-world use cases
- Nearly 2x as accurate as single-prompt GPT-4o generation
- ~14% more accurate than "other solutions on the market"

**Arctic-Text2SQL-R1 (Research, 2025):** Snowflake's reinforcement-learning trained models. Key results:
- #1 on BIRD benchmark (execution accuracy)
- 7B model outperforms prior 70B-class systems
- Uses execution-correctness reward signal only (no brittle intermediate supervision)
- Demonstrates that smaller, RL-trained models can beat much larger general-purpose models

**How We Compare:**
| Aspect | Snowflake Cortex Analyst | Us |
|--------|------------------------|-----|
| Schema description | Semantic model YAML (human-authored) | System prompt + acs_catalog (auto-populated from Census API) |
| Multi-LLM | Yes — specialized models per stage | Single model (GPT-4.1) for all stages |
| Verified queries | Yes — in semantic model | No |
| Value matching | Cortex Search (fuzzy) | ILIKE on catalog labels |
| Guardrails per step | Yes — validation between every agent | DML check + error classification only |

**Verdict:** Cortex Analyst is the gold standard for enterprise text-to-SQL. Their semantic model approach (human-authored YAML) produces higher accuracy than any automated discovery method. We can't match their multi-LLM architecture, but their key insight — rich metadata descriptions — is something we partially have via acs_catalog and could enhance.

---

### 2.4 Databricks: AI/BI Genie

**Scale:** Integrated into Databricks SQL, backed by Unity Catalog metadata.

**Architecture:**
- Uses Unity Catalog for automatic schema discovery (table descriptions, column tags, lineage)
- RAG retrieval limits schema context to ~20 relevant tables (even in databases with thousands)
- "Plays it safe" — requires fairly precise questions. Prefers to ask for clarification rather than guess
- Instruction tuning: admins write natural language instructions about business logic, naming conventions, and metric definitions
- Supports "trusted assets" — curated tables/views that Genie preferentially uses

**How We Compare:**
| Aspect | Databricks Genie | Us |
|--------|-----------------|-----|
| Schema scope | Unity Catalog (auto) + curated trusted assets | acs_catalog (auto from Census API) |
| Context limiting | RAG retrieval, ~20 tables in context | Two-phase catalog search, top 15 tables |
| Ambiguity handling | Asks clarifying questions | No — best-effort guess |
| Admin instructions | Natural language business logic | System prompt with Census conventions |

**Verdict:** Genie's approach of limiting context to ~20 tables mirrors our top-15 table cap in catalog search. Their "trusted assets" concept maps to our system prompt's guidance about common tables. We match their architecture but lack their clarification-asking behavior.

---

### 2.5 Open Source: Vanna.ai

**Scale:** 22,500+ GitHub stars, the most popular open-source text-to-SQL framework.

**Architecture (Vanna 2.0):**

1. **RAG Training**: You "train" a Vanna model by providing:
   - DDL statements (schema)
   - Documentation (business glossary, column descriptions)
   - Question-SQL pairs (training examples)
   These are indexed in a vector store (ChromaDB, Pinecone, etc.)

2. **Query Flow**: Question → RAG retrieval (relevant DDL + docs + similar SQL examples) → LLM generates SQL → execute → return table + chart + summary

3. **Self-Learning**: Successful queries are automatically added back as training examples, improving accuracy over time.

4. **Vanna 2.0 additions**: User-aware permissions, row-level security, streaming responses, audit logs.

**How We Compare:**
| Aspect | Vanna.ai | Us |
|--------|---------|-----|
| Schema context | RAG from vector store (DDL + docs + examples) | search_catalog tool (ILIKE on acs_catalog) |
| Example learning | Auto-learns from successful queries | No learning — fresh generation every time |
| Execution | Generates SQL + runs it | Same (via MindsDB proxy) |
| Output | Table + chart + NL summary | Table/text + optional CSV export |
| UI | Streamlit/custom web component | Chainlit with streaming steps |

**Verdict:** Vanna's self-learning loop (successful queries become future training examples) is a powerful pattern we don't have. Their RAG approach with question-SQL pair retrieval means the LLM sees "here's a similar question and the SQL that worked" — this significantly reduces hallucination. This is the most actionable improvement path for us.

---

## 3. Academic Benchmarks

### 3.1 Current Leaderboards (as of early 2026)

**BIRD (BIg Bench for LaRge-scale Database Grounded Text-to-SQL):**
The industry-standard benchmark. 12,751 question-SQL pairs, 95 databases, 33.4 GB total, 37 professional domains.

| Rank | Model | Score (Execution Accuracy) |
|------|-------|---------------------------|
| 1 | Gemini 2.0 Pro | 59.3% |
| 2 | Gemini 2.0 Flash | 58.7% |
| 3 | Gemini 2.0 Flash-Lite | 57.4% |
| — | Arctic-Text2SQL-R1 32B (Snowflake) | Claims #1 (test set, different eval) |
| — | Distyl + GPT-4o | First to cross 70% (mid-2024, test set) |
| — | RoboPhD (evolved Claude Opus 4.5) | 73.67% (test set) |

Note: Dev set and test set scores differ. The leaderboard shows dev set scores; some claims cite test set scores.

**Spider 2.0:**
Enterprise-level text-to-SQL. Databases with 1,000+ columns, multi-query workflows exceeding 100 lines of SQL. Much harder than original Spider. Top systems achieve ~30-40% on Spider 2.0 vs ~90% on original Spider — showing the gap between academic and real-world complexity.

**Key Insight from Benchmarks:**
A production text-to-SQL blog from 2026 reports: "86% accuracy on academic benchmarks but 6% on real databases. That is not a typo." This gap between benchmark and production accuracy is the central challenge — and why agentic approaches with error recovery (like ours) matter more than single-shot accuracy.

### 3.2 What Benchmarks Tell Us About Our System

Our system isn't benchmark-optimized, but our architectural patterns align with top-performing systems:

| Pattern | Used by Top Benchmark Systems | We Use It |
|---------|------------------------------|-----------|
| Agentic multi-step | Yes (all top-10 on BIRD use agents) | Yes |
| Schema linking / filtering | Yes (critical for large schemas) | Yes (catalog search) |
| Execution-based error correction | Yes (retry on failure) | Yes (with column name auto-lookup) |
| RAG for schema context | Yes (most top systems) | Partial (ILIKE, not vector RAG) |
| Verified/golden queries | Yes (Cortex Analyst, Google) | No |
| RL-trained specialized models | Yes (Arctic-Text2SQL-R1) | No (general-purpose GPT-4.1) |

---

## 4. Key Architectural Patterns Ranked by Impact

Based on the research, here are the patterns that matter most for production accuracy, ranked by ROI:

### Tier 1: Foundation (We Have These)

**1. Agentic Tool-Calling Loop**
Every production system has moved from single-shot prompting to multi-step agent loops. Single-shot GPT-4o achieves roughly half the accuracy of agentic systems (per Snowflake's benchmarks).
- **Us:** GPT-4.1 with 3 tools in a max-10-iteration loop.
- **Status:** Done.

**2. Schema Discovery Before SQL Generation**
No production system dumps the entire schema into the prompt. All use some form of "find relevant tables first, then generate SQL."
- **Us:** search_catalog tool with two-phase (AND then OR) matching.
- **Status:** Done.

**3. Error Recovery with Metadata Refresh**
When SQL fails, re-fetch schema information and retry. This alone can double success rates on complex queries.
- **Us:** _classify_sql_error identifies the table, _auto_search_catalog fetches correct column names, appends to error message for retry.
- **Status:** Done — and more sophisticated than most open-source implementations.

### Tier 2: High-Impact Improvements (We Should Add)

**4. Semantic Search for Schema Linking**
ILIKE keyword matching misses semantic relationships. "How much money do people make" won't match "MEDIAN HOUSEHOLD INCOME." Vector embeddings or PostgreSQL full-text search would close this gap.
- **Industry:** Amazon RASL uses vector-indexed semantic units. Vanna uses ChromaDB/Pinecone. Google uses embedding-based retrieval.
- **Us:** ILIKE on table_title and label. GIN full-text indexes exist but are unused.
- **Effort:** Low (FTS) to Medium (pgvector embeddings).
- **Expected Impact:** 15-25% improvement on semantically complex queries.

**5. Verified/Golden Queries**
Pre-approved SQL for known question patterns. Google and Snowflake both use this as a primary accuracy booster.
- **Industry:** Cortex Analyst stores them in the semantic model YAML. Google stores them as part of data agent configuration.
- **Us:** Nothing — all SQL is generated fresh.
- **Effort:** Medium — need a storage mechanism + similarity matching.
- **Expected Impact:** Dramatic for repeated query patterns. Snowflake claims this is key to their 90%+ accuracy.

**6. Question-SQL Self-Learning (RAG)**
Store successful query-answer pairs and retrieve similar ones as few-shot examples for future questions.
- **Industry:** Vanna.ai's core innovation. Successful queries auto-added to training set.
- **Us:** No learning loop. Conversation history provides in-session context but is lost on refresh.
- **Effort:** Medium — need a vector store + retrieval pipeline.
- **Expected Impact:** Significant for repeated question types.

### Tier 3: Nice-to-Have (Lower Priority)

**7. Ambiguity Detection + Clarification**
Ask "Did you mean income or earnings?" instead of guessing.
- **Industry:** Databricks Genie, Amazon Q, Google ADK all do this.
- **Us:** Best-effort generation without clarification.
- **Effort:** Low (system prompt instruction) to Medium (separate classification step).

**8. Multi-Agent Pipeline**
Separate agents for understanding, generation, review, and execution.
- **Industry:** Snowflake uses 5+ specialized agents. Google ADK has 5 stages.
- **Us:** Single GPT-4.1 agent handles all stages.
- **Effort:** High — requires orchestration framework.
- **Expected Impact:** Moderate. Single-agent with good prompting can match multi-agent for our domain.

**9. SQL Validation Before Execution**
A separate "review" step that checks SQL for common errors before running it.
- **Industry:** Google ADK review agent, Snowflake validation agent.
- **Us:** Only DML check — no structural SQL validation.
- **Effort:** Medium.

**10. Semantic Model (YAML)**
Human-authored descriptions of business meaning for every table and column.
- **Industry:** Snowflake's core differentiator.
- **Us:** System prompt + auto-populated acs_catalog. Census's own metadata (table_title, label) serves as an auto-generated semantic model.
- **Note:** Our acs_catalog with 30K labeled variables is effectively an auto-generated semantic model. The gap is that it wasn't human-curated for the agent's needs.

---

## 5. Honest Assessment: Strengths and Weaknesses

### What We Do Well

1. **Scale handling:** 1,193 tables with 30K+ variables. This is in the same ballpark as Amazon's BGL case study (400+ tables) and larger than most demos. Our catalog-first approach handles this without performance issues.

2. **Dual export model:** Separating sql_query (analytical, 500-row cap) from export_csv (full dataset, 100K-row cap) is a UX pattern most systems don't have. Google and Snowflake return results inline only.

3. **Error recovery sophistication:** Auto-looking up correct column names on SQL failure AND ramping temperature is more sophisticated than most open-source implementations and matches enterprise systems.

4. **Domain-specific prompt engineering:** Our system prompt is tuned to Census conventions (MindsDB quirks, IN bug workaround, area_type filters, tract GEO_ID format). Domain-specific prompts consistently outperform generic ones in production.

5. **Transparent tool execution:** Streaming SQL steps as expandable accordions in the Chainlit UI. Most chat-based SQL tools show a spinner then results. We show the agent's reasoning.

6. **Rich auto-populated metadata:** The acs_catalog with Census's own human-written labels ("Estimate!!Total:!!Male:!!Under 5 years") provides excellent context without manual curation.

### Where We Fall Short

1. **Search quality:** ILIKE is our weakest link. Every leader uses either vector embeddings or full-text search. Our GIN indexes exist but aren't used. This is the #1 priority fix.

2. **No learning loop:** Every query starts from zero (within a session, history helps, but across sessions, nothing is retained). Vanna's self-learning pattern — storing successful query-SQL pairs for future RAG — is a proven accuracy booster we lack entirely.

3. **No verified queries:** Google and Snowflake both use pre-approved SQL for known patterns. For a Census data agent, common questions ("top 10 states by population", "median income by county") could be pre-verified for instant high-accuracy responses.

4. **No ambiguity detection:** When a query is vague ("show me the data"), our agent guesses. Leaders ask clarifying questions. For Census data specifically, this matters — "income" could mean household income, per capita income, family income, or earnings.

5. **Single model, single agent:** We use one GPT-4.1 instance for everything. Leaders use specialized models for different stages (understanding, generation, validation). For our scale, this is acceptable — but a validation step would catch errors before execution.

6. **No admin credentials separation:** Still using the ETL admin user for runtime queries. Every production system isolates query credentials.

---

## 6. Recommended Improvements (Priority Order)

### Quick Wins (Days)

| # | Improvement | Effort | Expected Impact |
|---|-----------|--------|-----------------|
| 1 | **Switch catalog search to PostgreSQL FTS** — replace ILIKE with `plainto_tsquery` against existing GIN indexes | 2-4 hours | 15-25% better recall on multi-word searches |
| 2 | **Create read-only PG user** — `census_reader` with SELECT-only grants | 1 hour | Security hardening, matches every production system |
| 3 | **Add ambiguity prompt instruction** — tell agent to ask for clarification when catalog returns multiple plausible tables | 30 min | Better UX for vague queries |

### Medium-Term (Weeks)

| # | Improvement | Effort | Expected Impact |
|---|-----------|--------|-----------------|
| 4 | **Add verified queries** — store ~20-50 golden query-SQL pairs for common Census questions, match incoming questions against them | 1-2 weeks | Dramatic accuracy boost for common patterns (est. 95%+ for matched queries) |
| 5 | **Question-SQL RAG** — store successful query pairs in pgvector, retrieve as few-shot examples | 1-2 weeks | 10-20% accuracy improvement across all queries |
| 6 | **SQL validation step** — before executing, check SQL for common errors (missing JOINs, wrong column names, invalid WHERE clauses) | 1 week | Reduces wasted tool-call iterations |

### Longer-Term (Months)

| # | Improvement | Effort | Expected Impact |
|---|-----------|--------|-----------------|
| 7 | **Semantic model** — create a YAML or JSON description of key Census tables with business meaning, common query patterns, and relationship documentation | 2-4 weeks | Matches Snowflake's approach; biggest accuracy lever |
| 8 | **Multi-agent pipeline** — separate catalog-discovery, SQL-generation, and SQL-review into distinct agents | 2-4 weeks | Cleaner reasoning, better error isolation |
| 9 | **Self-learning loop** — automatically store successful query-answer pairs, auto-improve over time | 2-4 weeks | Compounding accuracy improvement |

---

## 7. Conclusion

Our Census Agent's architecture is **solidly in the mainstream** of production text-to-SQL design. The agentic tool-calling loop, catalog-first discovery, and error recovery with metadata refresh are the same patterns used by Google, Amazon, and Snowflake.

We are **not** a toy demo — the system handles 1,193 tables with 30K+ variables, which is larger than most enterprise deployments documented in case studies.

The gap to enterprise-grade systems is primarily in **search quality** (ILIKE vs vector/FTS), **learning** (no retention of successful patterns), and **guardrails** (no multi-agent validation). These are addressable incrementally without architectural changes.

The fastest path to meaningfully higher accuracy:
1. Switch ILIKE to FTS (hours)
2. Add 20-50 verified queries for common Census questions (days)
3. Add question-SQL RAG with pgvector (weeks)

These three changes would bring our system's accuracy profile close to what Snowflake and Google achieve in their enterprise products.

---

## Sources

- [Google: NL2SQL with BigQuery and Gemini](https://cloud.google.com/blog/products/data-analytics/nl2sql-with-bigquery-and-gemini) (Nov 2024)
- [Google: Conversational Analytics in BigQuery](https://docs.google.com/bigquery/docs/conversational-analytics) (Nov 2025)
- [Amazon: Dynamic Text-to-SQL with Bedrock Agents](https://aws.amazon.com/blogs/machine-learning/dynamic-text-to-sql-for-enterprise-workloads-with-amazon-bedrock-agents/) (Apr 2025)
- [Amazon: WWRR Conversational Data Assistant](https://aws.amazon.com/blogs/machine-learning/build-a-conversational-data-assistant-part-1-text-to-sql-with-amazon-bedrock-agents) (Jul 2025)
- [Amazon: BGL + Claude Agent SDK Case Study](https://aws.amazon.com/blogs/machine-learning/democratizing-business-intelligence-bgls-journey-with-claude-agent-sdk-and-amazon-bedrock-agentcore/) (Feb 2026)
- [Amazon Research: RASL — Retrieval Augmented Schema Linking](https://arxiv.org/pdf/2507.23104) (Jul 2025)
- [Snowflake: Cortex Analyst Behind the Scenes](https://snowflake.com/en/engineering-blog/snowflake-cortex-analyst-behind-the-scenes) (Aug 2024)
- [Snowflake: Cortex Analyst Accuracy Evaluation](https://snowflake.com/en/engineering-blog/cortex-analyst-text-to-sql-accuracy-bi/) (Aug 2024)
- [Snowflake: Arctic-Text2SQL-R1](https://snowflake.com/en/engineering-blog/arctic-text2sql-r1-sql-generation-benchmark/) (May 2025)
- [BIRD Benchmark Leaderboard](https://bird-bench.github.io/) (ongoing)
- [Spider 2.0](https://spider2-sql.github.io/) (ICLR 2025 Oral)
- [Vanna.ai](https://github.com/vanna-ai/vanna) (22K+ stars, MIT license)
- [RoboPhD: Self-Improving Text-to-SQL](https://arxiv.org/html/2601.01126v2) (Dec 2025)
- [LinkAlign: Scalable Schema Linking](https://arxiv.org/abs/2503.18596) (Mar 2025)
- [Production Text-to-SQL Lessons Learned](https://theqrmind.com/ai-tech/text-to-sql-production-lessons) (Dec 2025)
- [Building Production-Grade Multi-Agent Text2SQL Chatbots in 2026](https://medium.com/towardsdev/building-production-grade-multi-agent-text2sql-chatbots-in-2026-the-definitive-technical-guide-589c10ad987f) (Jan 2026)

*Last updated: 2026-02-07*
