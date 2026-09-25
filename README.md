# MISHKAN

[![CI](https://github.com/Y7-Labs/mishkan/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Y7-Labs/mishkan/actions/workflows/ci.yml)

**Delegate engineering missions, not isolated coding sessions.**

MISHKAN is a local-first software-engineering organization operated by persistent AI agents. Give
it an engineering objective; it forms a mission team for the project, coordinates planning and
execution, preserves decisions and evidence, and returns independently reviewed results—while you
retain authority over consequential actions.

> **Pre-release:** MISHKAN is under active development and currently installs from source. The
> implemented product is a local, single-daemon system for Linux and macOS. Persistent scheduling,
> distributed workers, and the operational TUI remain roadmap work.

## Organization model

The target version 1 organization contains exactly **59 persistent professional identities**. The
human engineer remains outside that roster as CEO. The identities keep their responsibility,
project knowledge, demonstrated competence, and learning over time, but they do not all join every
mission.

The permanent organization has nine responsibility branches:

- Product and Experience;
- Architecture and System Design;
- Software Engineering;
- Data and AI Engineering;
- Platform, Delivery, and Reliability;
- Security and Supply Chain;
- Independent Assurance;
- Research and Decision Support;
- Documentation and Organizational Learning.

Five explicit pools provide reusable specialists for software engineering, quality evaluation,
security evaluation, mission reporting, and documentation. A pool narrows professional
responsibility; it never grants tools, authority, or automatic participation. The complete roster
and pool membership are defined in [SRS Appendix A](docs/PROJECT/SRS.md#appendix-a--organization-version-1-roster).

Each objective becomes a contextual mission rather than an invocation of a fixed workflow:

```text
Human CEO
    │ objective, constraints, decisions, risk acceptance
    ▼
PM + CTO
    │ agreed Mission Brief and confirmed crew composition
    ▼
Temporary Mission Crew
    ├── one accountable Mission Lead
    ├── only the producers and specialists the mission needs
    ├── independent evaluators for acceptance evidence
    └── an independent Reporter for the final account
```

The PM owns product purpose, priorities, functional acceptance, and formal crew composition. The
CTO owns technical direction, feasibility, risk coverage, and technical readiness. If they cannot
agree, only the dependent work pauses; independent work may continue, and an actionable escalation
is sent to the CEO when the decision exceeds their authority.

Durable Executive, Mission, Branch, and authorized Direct channels keep decisions attached to the
work. From any supported client, the CEO can inspect the same organization state and, when needed,
comment, answer an escalation, suspend or resume work, request reassignment, stop a mission, or
accept an explicit risk under the configured policy.

This model supports free-form objectives for existing repositories, greenfield products,
multi-repository systems, research, incidents, modernization, platform work, and operations.
Versioned mission templates may provide guidance, but they never impose a universal task graph,
fixed crew, or static role-to-tool matrix.

## How the current system is structured

- **`mishkand`** accepts commands, owns durable run state, and publishes events.
- **Agent coordination** currently uses CrewAI 1.x, a Python multi-agent automation framework. It
  supplies agents, tasks, crews, and flows; it does not own MISHKAN policy or application state.
- **Public configuration and policy** decide which capability may act on which target and with
  which effect.
- **The capability gateway** executes approved file, shell, Git, browser, Web, and MCP operations
  and records their evidence.
- **Artifacts and recovery journals** preserve large outputs and reconcile interrupted effects
  without silently replaying uncertain work.

Operational CLI, Python, HTTP/SSE, and MCP clients all enter through the same MISHKAN authority:

```text
CLI · Python SDK · HTTP/SSE · MCP bridge
                    │
                    ▼
                mishkand
       ┌────────────┼──────────────┐
       ▼            ▼              ▼
 Objective/run  Policy and      Events, artifacts,
   lifecycle    capabilities    and recovery state
       │
       ▼
 Agent coordination
 (CrewAI framework)
```

## Available today

| Area | Implemented surface |
|---|---|
| Organization and missions | 59 versioned professional identities, contextual Mission Crews, PM/CTO governance, accountable assignments, independent assurance, durable conversations, scoped escalations, governed CEO interventions, and evidence-based professional evolution |
| Repository work | Scoped file reads and search, direct processes, Bash, exact change sets, and governed Git effects |
| Long-running work | PTY sessions and managed jobs with cursors, signals, cancellation, settlement, and recovery evidence |
| Control plane | Loopback HTTP API, idempotent commands, SQLite/WAL, explicit Alembic migrations, snapshots, JSONL export, and resumable SSE |
| Artifacts | Chunked uploads, content-addressed immutable bodies, collections, compare-and-swap references, holds, retention, and reconciliation |
| Web and browser | Bounded search/fetch/extract/crawl plus governed Playwright Chromium sessions and diagnostic evidence |
| MCP and harnesses | Governed STDIO and Streamable HTTP clients, a filtered MCP facade, and a stateless local STDIO bridge |
| Skills and context | Progressive `SKILL.md` packages, bundles, contextual invocation, `/learn`, recoverable lifecycle, deterministic context packages, and candidate-only community recommendations |
| Engineering environments | Evidence-based engine discovery, technical packs, immutable descriptor sets, governed environment attempts, and measured Docker/Compose/Dev Container/Podman adapters |
| Observability | Bounded OpenTelemetry projection and optional non-authoritative LangSmith feedback import under public disclosure policy |
| Attributed knowledge | Explicit literal, episodic, semantic, and structural queries; immutable evidence bundles and Context Packs; authenticated mem0, Cognee, and Graphify adapters; governed ingestion, memory capture, promotion, and visible degraded operation |

The detailed implementation status and evidence are maintained in the
[documentation index](docs/README.md), not duplicated here.

## Quick start

### Requirements

- Linux or macOS
- Python 3.11, 3.12, or 3.13
- [uv](https://docs.astral.sh/uv/)
- Git
- For model-backed runs: a reachable model provider. The local preset uses
  [Ollama](https://ollama.com/) on `127.0.0.1:11434`.

Some capability and test paths also require `ripgrep` or a Playwright Chromium installation. They
are not required to start the daemon.

### 1. Install from source

```bash
git clone https://github.com/Y7-Labs/mishkan.git
cd mishkan
uv sync --locked --dev
uv run mishkan --help
```

### 2. Create the local control plane

Run these commands from the repository that MISHKAN should govern. For this first run, that can be
the MISHKAN checkout itself.

```bash
uv run mishkan config setup --preset local --output .mishkan/config.yaml
uv run mishkan --config .mishkan/config.yaml config validate
uv run mishkan --config .mishkan/config.yaml daemon setup
uv run mishkand --config .mishkan/config.yaml
```

`daemon setup` creates the SQLite database and an instance token at
`.mishkan/runtime/api-token.json`. The token file is written with mode `0600`; operational CLI and
SDK requests use it for authentication. `mishkand` does not create or migrate a database silently.

### 3. Verify the daemon

Leave `mishkand` running and use another terminal:

```bash
curl --fail-with-body http://127.0.0.1:8888/v1/health

uv run mishkan --config .mishkan/config.yaml --json events list --limit 5
```

The health endpoint is intentionally minimal and unauthenticated. Other daemon operations require
the instance token.

### 4. Submit a repository objective

The generated local preset routes planning and execution to `qwen2.5-coder:7b`. Before submitting
a run, make sure that exact model exists in `ollama list`, or replace both model values in
`.mishkan/config.yaml` with exact names installed on your Ollama instance. Validate the edited file
again before continuing.

```bash
ollama list
uv run mishkan --config .mishkan/config.yaml config validate

uv run mishkan --config .mishkan/config.yaml init \
  "Inspect this repository and propose an evidence-based initialization plan" \
  --repository .
```

Inspect the accepted run and follow its durable event stream:

```bash
uv run mishkan --config .mishkan/config.yaml run list
uv run mishkan --config .mishkan/config.yaml events tail --after 0
```

`init` submits work to the daemon. The repository passed with `--repository` must match the
workspace configured for that daemon instance.

## Interfaces

### CLI

Run `uv run mishkan --help` for the authoritative command tree.

| Group | Purpose |
|---|---|
| `config`, `schema` | Generate, inspect, validate, edit, and export public configuration and schemas |
| `daemon`, `db` | Initialize the local instance, rotate its token, and inspect or explicitly upgrade its schema |
| `init`, `run`, `events` | Submit repository work, inspect or recover runs, and query or stream evidence |
| `artifact` | Upload, inspect, reference, retain, reconcile, and collect immutable artifacts |
| `change`, `git` | Apply recoverable filesystem change sets and explicit Git effects |
| `terminal`, `job` | Operate daemon-owned interactive sessions and managed processes |
| `mcp` | Connect, inspect, call, cancel, and reconcile governed MCP peers |
| `skill`, `context` | Inspect, invoke, learn, evolve, and resolve procedural skills and confirmed project/engineer context |
| `environment` | Observe engines, resolve compatible bindings, validate descriptors, execute adapters, and inspect settlement evidence |
| `telemetry` | Inspect configured disclosure and import attributable non-authoritative evaluation evidence |
| `knowledge`, `memory`, `code-graph` | Query attributed evidence, inspect sources and operations, capture accepted episodic memory, govern ingestion/promotion, and use Graphify structure through MISHKAN |
| `org`, `mission` | Inspect the persistent organization, contextual Mission Briefs, crews, assignments, environments, runs, and evidence |
| `conversation`, `intervention` | Use durable channels and submit governed comments, escalation answers, pauses, resumptions, reassignments, stops, and risk acceptances |
| `advisory` | Inspect contextual community candidates without activating them automatically |

The package also installs:

- `mishkand` — the local application authority and HTTP/SSE server;
- `mishkan-mcp-stdio` — a stateless STDIO bridge to the daemon's configured MCP facade.

### Python SDK

The synchronous SDK reads the same instance token and calls the same HTTP API as the CLI:

```python
from pathlib import Path

from mishkan import Mishkan

with Mishkan(
    "http://127.0.0.1:8888",
    token_file=Path(".mishkan/runtime/api-token.json"),
) as client:
    print(client.health())
    print(client.snapshot())
```

### HTTP, SSE, and MCP

The daemon exposes its versioned API under `/v1`, including commands, health, snapshots, events,
runs, tasks, artifacts, change sets, execution sessions, and bounded knowledge queries, corpora,
operations, promotions, and source health. Event streaming resumes from
`Last-Event-ID`; retained-history gaps require a fresh snapshot. The configured MCP facade exposes
only its allowlisted application operations and does not grant authority through discovery.

See the exported [public schemas](definitions/schemas/) for wire contracts and the
[system contract](docs/SYSTEM/CONTRACT.md) for invariants and refusal semantics.

## Configuration and credentials

MISHKAN configuration is versioned YAML. Multiple `--config` flags are merged from lowest to
highest precedence; `MISHKAN_CONFIG` may provide the same ordered list using the operating system's
path separator.

```bash
uv run mishkan --config base.yaml --config project.yaml config show
uv run mishkan --config base.yaml --config project.yaml config validate
```

Operational limits, tool sources, policy sources, provider routes, knowledge sources and selection
order, network and disclosure profiles, session profiles, and MCP connections belong in public
configuration. Secret values do not: configuration stores credential references, and providers
resolve their values at the execution boundary.

The optional local knowledge stack is defined in `docker-compose.local.yaml`. It binds mem0,
Cognee, Graphify MCP, and `mishkand` to loopback, uses pinned provider identities, and routes local
LLM and embedding work through configured Ollama models. Supply the credential references required
by the Compose file, validate the generated MISHKAN configuration, and start only the profiles the
mission needs. Provider availability does not grant authority, and unavailable optional providers
produce visible degraded evidence rather than fabricated success.

## Common failures

**`mishkand` refuses to start because the schema is absent or outdated**

Run `mishkan daemon setup` for a new instance. For an existing instance, inspect it with
`mishkan db status` and apply the explicit `mishkan db upgrade`; the daemon never migrates itself.

**A model-backed run reports that all candidates failed**

Check that the provider is running, compare configured model names with `ollama list`, and run
`config validate` after editing the routes. Configuration validity does not claim that an external
service is healthy.

**`ollama serve` reports that `127.0.0.1:11434` is already in use**

An Ollama server is normally already listening on that address. Confirm it with `ollama list`
instead of starting another process.

## Development

CI runs on Linux and macOS with Python 3.11–3.13. Install Chromium and `ripgrep` before running the
same complete gate locally.

```bash
uv run playwright install chromium

uv run pytest --cov=mishkan --cov-branch --cov-fail-under=80
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv run mishkan schema export --output definitions/schemas
git diff --exit-code -- definitions/schemas
uv build
```

External-model acceptance tests are marked separately and require an explicitly configured model
service. The normal deterministic CI gate does not treat an unavailable paid provider as success.

## Documentation

- [Documentation index and authority rules](docs/README.md)
- [Product requirements](docs/PROJECT/PRD.md)
- [Software requirements](docs/PROJECT/SRS.md)
- [System contract](docs/SYSTEM/CONTRACT.md)
- [Architecture](docs/SYSTEM/ARCHITECTURE.md)
- [Implementation plan and roadmap](docs/PROJECT/IMPLEMENTATION_PLAN.md)
- [Decision log](docs/PROJECT/DECISION_LOG.md)
- [Observed validation evidence](docs/VALIDATION/)

The requirements-to-architecture chain follows
[SWE-BASICS-BEFORE-CODE](https://github.com/MatrixCollab/SWE-BASICS-BEFORE-CODE). Working notes and
temporary reviews are intentionally kept outside the versioned documentation baseline.

## Support

Use [GitHub Issues](https://github.com/Y7-Labs/mishkan/issues) for reproducible bugs and feature
requests. Include the MISHKAN version, Python version, operating system, redacted configuration,
and the relevant event or error envelope.
