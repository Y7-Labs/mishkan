# I07 Attributed Knowledge and Degraded Operation — Validation Evidence

**Status:** Passed — D-046  
**Observed:** 2026-09-25  
**Branch:** `feat/i07-attributed-knowledge`  
**Accepted checkpoint:** `9bafa68`  
**GitHub Actions:** [run 36148793888](https://github.com/Y7-Labs/mishkan/actions/runs/36148793888)

## Gate result

I07 is accepted on its topic branch. The final GitHub Actions run passed all seven jobs:

- Python 3.11, 3.12, and 3.13 on Linux;
- Python 3.11, 3.12, and 3.13 on macOS;
- the retained Linux gate for real governed Docker, Compose, Dev Container, and Podman adapters.

The final deterministic local gate passed 743 tests, skipped two unavailable engine fixtures,
deselected six external-model, live-provider, and performance gates, and reached 80.21 percent
branch-aware coverage. The I07 adapters, repository, provider runtime, and selection service each
meet the 80 percent branch-coverage requirement. Ruff, formatting, strict mypy across 223 source
files, deterministic schema export, `git diff --check`, and source/wheel builds passed.

The separately enabled authenticated live-provider gate passed three tests against the pinned
local stack in 61.28 seconds. Warm observations remained within the accepted bounds: mem0 recall
below 300 ms, Cognee raw CHUNKS retrieval below one second, and Graphify MCP graph statistics below
two seconds. The existing sustained 60-second event-capacity gate also remained green.

## Attributed retrieval and degraded operation

The observed implementation requires every query to declare exactly one class: `literal`,
`episodic`, `semantic`, or `structural`. Source selection follows public configuration and invokes
only compatible enabled sources. There is no hidden classifier or workflow.

Tests prove:

- repository literal evidence is read from the exact bound Git revision and remains subject to
  repository, path, symlink, byte, and result bounds;
- mem0 episodic retrieval and accepted-result capture preserve exact project, agent, run, and
  operation attribution;
- Cognee is used through its authenticated `/api/v1` API for add, cognify, and raw CHUNKS
  retrieval; generated answers are not accepted as evidence;
- Graphify uses discovered, schema-bound MCP tools for read queries and a governed `graphify
  update` job for refresh; no custom Graphify REST interface exists;
- every normalized item has an immutable content Artifact, source locator, source revision,
  ranking basis, staleness, scope, and inspection findings;
- the canonical bundle records every attempt, miss, unavailable source, limitation, and immutable
  bundle Artifact;
- Context Packs contain both the bundle lineage and the bounded immutable evidence bodies supplied
  to CrewAI as explicitly untrusted evidence;
- conflicting evidence remains separate, and optional-provider failure produces a visible degraded
  bundle while required unavailable evidence blocks only dependent work;
- secret, PII, prompt-injection, unsafe identifier, cross-project, and stale-revision cases retain
  stable refusal or limitation evidence.

CrewAI remains the sole production agent coordination runtime. MISHKAN owns deterministic source
selection, policy, inspection, lineage, persistence, and acceptance.

## Mutations, memory, and promotion

Ingestion, refresh, memory capture, cancellation, reconciliation, proposal, and decision are
durable application commands. A read never performs a hidden mutation. Provider calls execute
outside database transactions and carry the MISHKAN operation identity where supported.

Recovery tests prove duplicate-command idempotence, optimistic revisions, cancellation, lost
responses, uncertain settlement, reconciliation before retry, Graphify staging and compare-and-swap
publication, and preservation of historical evidence after revocation. Episodic capture requires a
durably accepted result and attributable evidence; raw conversations, rejected results, secrets,
and PII are not captured automatically. Cross-project or organizational promotion requires a
separate policy-bound CTO or CEO decision, and the proposer cannot approve its own promotion.

Knowledge evidence does not automatically change professional competence. The I06 evolution
process remains the only authority for competence promotion.

## Provider and local-stack evidence

The local Compose topology binds mem0, Cognee, Graphify MCP, and `mishkand` to loopback and uses
credential references rather than secret values in YAML. Images and packages are pinned; no
`latest` or `main` identity is used. The accepted live gate observed:

| Surface | Pinned identity | Contract proven |
|---|---|---|
| mem0 | source commit `47a69e1e72dc562b6fdd49a9ef892229afc7508a` | OSS paths without `/v1`, authenticated search/capture, Ollama LLM and 768-dimensional `nomic-embed-text` embeddings |
| Cognee | `cognee[api,ollama]==1.6.1` | authenticated `/api/v1`, multipart add, cognify, and raw CHUNKS retrieval |
| Graphify | `graphifyy[mcp]==0.9.67` | Streamable HTTP MCP discovery/query plus governed CLI refresh |
| Ollama profile | `ollama/ollama:0.12.3` with an exact image digest | optional container route; the same public configuration also supports host Ollama |

Telemetry is disabled where supported. The gate used no paid provider credentials and did not
silently fall back to one.

## Gaps found and closed by the gate

The final review found and closed these implementation or proof gaps:

- the first Cognee adapter assumed an incompatible JSON contract; it now follows the pinned 1.6.1
  multipart and CHUNKS contracts and preserves raw chunks rather than generated synthesis;
- mem0 initially accepted a write response while pgvector rejected the provider-internal
  1536-versus-768 embedding dimension; the dimension is now public configuration and the live gate
  proves actual capture and recall;
- Graphify refresh initially lacked complete governed execution and atomic publication evidence;
  it now stages the exact Git revision, stores immutable graph and diff Artifacts, uses CAS, and
  reconciles settlement before retry;
- initial Context Packs referenced only the bundle manifest; they now bind and project the actual
  bounded immutable evidence bodies into the CrewAI task context;
- negative branches for source authority, credentials, MCP schema drift, repository binding,
  operation identity, artifact availability, and lifecycle transitions were under-tested; the
  final gate exercises them explicitly;
- I07's public Pydantic contracts were implemented but absent from the versioned JSON Schema
  catalogue; all ten knowledge schemas and configuration 1.6 are now exported and CI-verified.

No hidden classifier, static workflow, private operational deny-list, paid local dependency, or
competing agent runtime was introduced.

## Decision

D-046 accepts I07 at `9bafa68` based on the local deterministic, live-provider, performance, and
seven-job remote evidence above. I07 may be promoted from its topic branch to `develop`. I08
Persistent Headless Scheduling remains unauthorized until an explicit engineer decision starts it
after promotion.
