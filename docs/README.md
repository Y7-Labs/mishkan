# MISHKAN Documentation

This index is the entry point for durable MISHKAN documentation.

## Domains

| Domain | Authority |
|---|---|
| `PROJECT` | Product intent, approved requirements, decisions, and—when ready—the delivery roadmap |
| `SYSTEM` | Approved contracts, models, architecture, and system design derived from requirements |
| `VALIDATION` | Factual test and runtime evidence created only when execution evidence exists |
| `OPERATIONS` | Deployment, migration, recovery, and runbooks created only for real environments |

## Current documents

| Document | Status | Role |
|---|---|---|
| [`PROJECT/PRD.md`](PROJECT/PRD.md) | Accepted 1.4 — D-032 | Product problem, free-form missions, mission-scoped environments, 59-identity organization, executive control, and contextual capabilities |
| [`PROJECT/SRS.md`](PROJECT/SRS.md) | Accepted 1.6 — D-032 | Canonical verifiable requirements and complete PRD traceability |
| [`PROJECT/DECISION_LOG.md`](PROJECT/DECISION_LOG.md) | Living | Central registry for accepted, open, and superseded decisions |
| [`PROJECT/IMPLEMENTATION_PLAN.md`](PROJECT/IMPLEMENTATION_PLAN.md) | Accepted 1.6 — D-041 | I00/I01 retention, vertical I02–I11 delivery, I05 context/telemetry and mission-environment work packages, acceptance, and Git-flow plan |
| [`SYSTEM/CONTRACT.md`](SYSTEM/CONTRACT.md) | Accepted 1.4 — D-033 | Sequence 03 promises, invariants, refusals, sessions, artifacts, and mediation |
| [`SYSTEM/RESPONSIBILITIES.md`](SYSTEM/RESPONSIBILITIES.md) | Accepted 1.2 — D-034 | Sequence 04 exact primary ownership and handoffs for RSP-001–026 |
| [`SYSTEM/MODEL.md`](SYSTEM/MODEL.md) | Accepted 1.3 — D-037 | Mission-centered Sequence 05 behavior, including agent-authored environment planning, binding lifecycle, and governed generation |
| [`SYSTEM/ARCHITECTURE.md`](SYSTEM/ARCHITECTURE.md) | Accepted 1.3 — D-037 | Transactional modular-monolith structure, environment-module ports, format/engine boundaries, and architecture decisions |
| [`VALIDATION/foundation.md`](VALIDATION/foundation.md) | Passed | Observed local and remote acceptance evidence for the foundation |
| [`VALIDATION/local-crewai.md`](VALIDATION/local-crewai.md) | Passed | Observed local and remote acceptance evidence for real CrewAI execution |
| [`VALIDATION/policy-tools.md`](VALIDATION/policy-tools.md) | Passed and integrated | Local, live-model, cloud, and remote evidence for the governed capability registry, public policy, Process, Bash, File, Search, and CrewAI gateway |
| [`VALIDATION/durability.md`](VALIDATION/durability.md) | Passed — D-038 | Local, live CrewAI/Ollama, and remote Linux/macOS evidence for the durable daemon, mutations, artifacts, sessions, recovery, security, and performance |
| [`VALIDATION/web-browser-mcp.md`](VALIDATION/web-browser-mcp.md) | Passed and integrated — D-040 | Original evidence, corrected local/remote conformance gate, and green `develop`/`main` integration matrices for Web, Browser, MCP, and governed harness clients |
| [`VALIDATION/skills-engineering.md`](VALIDATION/skills-engineering.md) | Passed — D-042 | Complete local and remote evidence for skills, context packages, telemetry, recommendations, engineering environments, and real Docker/Compose/Dev Container/Podman adapters |
| [`VALIDATION/organization-missions.md`](VALIDATION/organization-missions.md) | Passed, integrated, and promoted — D-044 | Local, remote, `develop`, history-synchronization, and `main` promotion evidence for the 59-identity organization, contextual missions and crews, PM/CTO governance, communication, interventions, environment planning, and professional evolution |
| [`VALIDATION/attributed-knowledge.md`](VALIDATION/attributed-knowledge.md) | Passed — D-046 | Local, authenticated live-provider, performance, and remote Linux/macOS evidence for explicit knowledge classes, immutable attribution, mem0/Cognee/Graphify integration, governed mutation, promotion, and degraded operation |

## Authority rules

- PRD owns product intent; SRS owns observable requirements after their recorded gates. Proposed
  amendments never silently replace the last accepted baseline.
- The decision log owns decision status. A separate approval file is not created for each gate.
- System documents derive from accepted requirements and decisions; they do not replace them.
- Validation contains observed evidence, not self-review or speculative checklists.
- Operations documents are created only when tied to an actual environment or procedure.
- Working notes, source comparisons, gate checklists, and drafting reviews stay in the task, `/tmp`,
  or an external work area and are not versioned as project documentation.
- Obsolete material is removed from the active tree. Git history is not a documentation domain.

## Documentation lifecycle

1. Discuss and investigate without creating a durable file.
2. Promote only an accepted requirement, decision, contract, model, procedure, or observed result.
3. Put it in the domain that owns its authority.
4. Update the central decision log when its status or meaning changes.
5. Supersede or remove obsolete documents instead of retaining parallel versions.
