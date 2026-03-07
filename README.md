# Census Insights

```mermaid
mindmap
  root((Census Insights))
    Product
      Census-specific text-to-SQL app
    Runtime
      Browser
      Chainlit
      OpenAI tools
      MindsDB
      PostgreSQL
    Entry point
      docker compose up --build -d
    Primary docs
      Current implementation doc
      Historical framework rationale
```

This README stays intentionally small so the first thing a reader sees is the runtime shape, not a wall of setup prose.

## Start

```mermaid
flowchart LR
    Dev[Developer] --> Up[docker compose up --build -d]
    Up --> Stack[data-agent + mindsdb + postgres]
    Stack --> UI[http://localhost:8001]
```

This path is short because the repo is designed to come up as one local stack rather than through a manual multi-step bootstrap.

```bash
docker compose up --build -d
```

## Key docs

```mermaid
classDiagram
    class README
    class CurrentImplementation
    class HistoricalRationale

    README --> CurrentImplementation : how it works now
    README --> HistoricalRationale : why Chainlit was chosen
```

These links are separated so maintainers can jump directly to either runtime truth or historical context without mixing the two.

- Current implementation: `docs/analysis/2026-03-07-current-implementation-design.md`
- Historical framework rationale: `docs/analysis/2026-02-05-chainlit-vs-streamlit-comparison.md`

## Useful commands

```mermaid
flowchart TD
    A[Inspect running system] --> B[docker compose logs -f data-agent]
    C[Verify behavior] --> D[docker compose exec data-agent pytest tests/test_e2e.py -v --timeout=180]
    E[Stop stack] --> F[docker compose down]
```

These commands cover the three most common operational needs: inspect, verify, and stop.

```bash
# logs
docker compose logs -f data-agent

# run e2e tests
docker compose exec data-agent pytest tests/test_e2e.py -v --timeout=180

# stop stack
docker compose down
```
