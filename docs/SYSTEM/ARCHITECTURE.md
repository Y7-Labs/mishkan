# MISHKAN Architecture

**Status:** Accepted by D-037; amended by D-051 on 2026-10-29
**Version:** 1.4
**Derived from:** System Model 1.4 and Responsibility Map 1.3 accepted by D-051,
plus retained decisions D-015, D-016, D-021, D-022, and D-029

## 1. Scope and non-negotiable boundaries

This document derives structure from the behavioral model. It does not authorize implementation.

- `mishkand` is the single authoritative application service.
- CrewAI 1.x is integrated directly as the sole production runtime for agents and teams. There is
  no competing `AgentRuntime`, runtime selector, or second tool-calling loop.
- CLI, SDK, `mishkan chat`, operational TUI, HTTP/OpenAPI, SSE, MCP, schedules, and harnesses are
  clients or triggers of the same application commands and queries.
- Policy values and operational restrictions are public, versioned, and configurable. Code owns
  schemas and integrity invariants, not a private deny-list.
- Mission plans, crews, skills, packs, engines, and capability bindings are contextual. No
  universal workflow or static role/tool matrix is embedded.
- Availability never grants authority, and no engine is bindable without a concrete executable
  adapter and location-specific evidence.
- Authoritative transitions use short transactions with an outbox. CrewAI, model calls, external
  effects, artifact body transfer, and event delivery remain outside those transactions.

## 2. Container view

**Question:** Which runnable and persisted boundaries exist, and where is authority held?

```mermaid
flowchart LR
    CEO["CEO / operator"]
    Harness["Codex, Claude, other harness"]
    Scheduler["OS or external scheduler"]
    Repos["Repositories and external systems"]
    Services["Models, knowledge, MCP, credentials, isolation"]

    subgraph Product["MISHKAN product boundary"]
        Clients["CLI · Python SDK · mishkan chat"]
        TUI["Go/Bubble Tea operational TUI"]
        HTTP["HTTP/OpenAPI + SSE"]
        Bridge["Local STDIO MCP bridge"]
        Daemon["mishkand transactional modular monolith"]
        CrewAI["CrewAI 1.x directly integrated"]
        Worker["mishkan-worker stateless leased executor"]
        Metadata[("SQLite/WAL local · PostgreSQL distributed")]
        Content[("Filesystem CAS local · S3-compatible blobs distributed")]
    end

    CEO <-->|"conversation, commands, inspection"| Clients
    CEO <-->|"drill-down and governed interventions"| TUI
    Harness <-->|"versioned application contract"| HTTP
    Harness <-->|"local MCP transport"| Bridge
    Scheduler -->|"idempotent run command"| HTTP
    Clients --> Daemon
    TUI -->|"snapshot, SSE, same commands"| Daemon
    HTTP --> Daemon
    Bridge -->|"stateless MCP/application translation"| Daemon
    Daemon --> CrewAI
    Daemon -->|"short transactions + outbox"| Metadata
    Daemon <-->|"immutable bodies"| Content
    Daemon <-->|"lease and immutable envelope"| Worker
    Daemon <-->|"governed effects and evidence"| Repos
    Worker <-->|"bounded task effects"| Repos
    Daemon <-->|"typed configured adapters"| Services
    Worker <-->|"task-scoped adapters"| Services
```

| Container | Authority and limits |
|---|---|
| `mishkan` CLI / SDK / chat | Typed clients; own no policy, mission state, plan, or CrewAI runtime |
| `mishkan watch` | Operational client with transparent drill-down and authorized commands; owns no authoritative projection or stronger authority |
| HTTP/OpenAPI + SSE | Versioned application facade and resumable observation transport |
| STDIO MCP bridge | Local transport bridge; stateless beyond connection lifecycle and never an authority source |
| `mishkand` | Sole application authority for organization, missions, conversations, policy, planning, coordination, effects, acceptance, artifacts, events, schedules, and workers |
| CrewAI integration | Materializes accepted MISHKAN definitions as CrewAI agents, tasks, crews, processes, and flows |
| `mishkan-worker` | Executes leased immutable task envelopes; cannot plan, authorize, accept, commit, push, deploy, or migrate unless the exact envelope and policy grant the particular capability |
| Metadata stores | Authoritative relational state, transactions, leases, and outbox through one repository contract |
| Content stores | Immutable artifact bodies addressed by integrity identity; metadata remains authoritative in the application store |

## 3. Modular-monolith component view

**Question:** How are all 26 responsibilities grouped without duplicating ownership?

```mermaid
flowchart TB
    API["Application interfaces and identity"]
    Org["Organization definitions"]
    Mission["Mission governance"]
    Teams["Collaboration plans and team effectiveness"]
    Conversation["Conversations and interventions"]
    Planning["Context and planning"]
    Policy["Policy and approvals"]
    Crew["Direct CrewAI integration"]
    Quality["Evaluation, reporting, acceptance"]
    Registry["Tool, engine, environment, and pack registry"]
    Environment["Mission environment resolution"]
    Gateway["Effect and capability gateway"]
    Native["Native capabilities and sessions"]
    Skills["Skills and professional learning"]
    Knowledge["Attributed knowledge"]
    Artifacts["Artifacts and working references"]
    Events["State, evidence, projections, and outbox"]
    Scheduling["Scheduling"]
    Workers["Worker coordination"]
    Conformance["Conformance and release assurance"]

    API --> Mission
    Mission --> Org
    Mission --> Conversation
    Mission --> Teams
    Teams -->|"milestone evidence"| Quality
    Teams -->|"bounded adaptation"| Mission
    Mission --> Planning
    Mission -->|"bounded clarification and planning"| Crew
    Crew -->|"candidate Brief and plan"| Mission
    Crew -->|"candidate plan results"| Planning
    Planning --> Policy
    Planning --> Environment
    Environment -->|"binding or incompatibility"| Planning
    Environment --> Registry
    Policy -->|"authorized mission and plan"| Crew
    Crew --> Quality
    Crew --> Registry
    Registry --> Gateway
    Crew --> Skills
    Crew --> Knowledge
    Gateway --> Native
    Native --> Artifacts
    Quality --> Artifacts
    Quality --> Events
    Conversation --> Events
    Scheduling --> Mission
    Workers --> Crew
    Events -.-> API
    Conformance -.-> Crew
    Conformance -.-> Gateway
```

| Primary component | Primary responsibilities | Distinct internal modules or boundary |
|---|---|---|
| Application interfaces and identity | RSP-001–003 | command/query facade and client identity |
| Execution-context evidence and planning | RSP-004–005 | repository/greenfield context and plan revisions |
| Policy and approvals | RSP-006 | decision authority |
| Organization definitions | RSP-007 | profiles, branches, explicit pools, templates |
| Mission application component | RSP-022 | mission governance, collaboration plans, deterministic team signals, bounded adaptation, retrospectives, and separate conversation/intervention modules under one primary owner |
| Direct CrewAI integration | RSP-008 | production coordination boundary |
| Evaluation, reporting, and acceptance | RSP-009–010 | independent assurance, milestone team-health interpretation, and deterministic acceptance |
| Effect and security gateway | RSP-011–012 | separate enforcement, native-capability/session adapters, and content-security modules |
| Attributed knowledge | RSP-013 | knowledge routing and provenance |
| Skill lifecycle and use | RSP-014, RSP-020 | skill catalogue, selection, learning, and lifecycle |
| State, evidence, projections, and outbox | RSP-015 | authoritative persistence and derived observation |
| Scheduling | RSP-016 | schedule and trigger governance |
| Worker coordination | RSP-017 | enrollment, leases, envelopes, and completion delivery |
| Conformance and release assurance | RSP-018–019 | quality and runtime conformance |
| Tool registry | RSP-021 | tool contracts, snapshots, and CrewAI bindings |
| Artifacts and working references | RSP-023 | immutable content metadata, CAS references, and recovery |
| MCP and harness mediation | RSP-024 | MCP client/server and external-client application translation |
| Mission environment, engine, and pack resolution | RSP-025 | observed eligibility, proposal compatibility, verified bindings, descriptor-adapter resolution, location-bound verification, and materialization evidence |
| Professional evolution | RSP-026 | scoped competence and profile evidence; team aggregates never change profiles directly |

Native file, edit, process, Bash, PTY, job, Web, and Browser adapters remain distinct modules behind
RSP-011; artifact operations terminate at RSP-023. These modules do not own policy or become
independent application services.

## 4. Application command and observation paths

All clients submit versioned commands to one application layer. The application authenticates the
client, validates schema and current expected state, resolves the requested mission or
conversation, applies policy, commits the state transition with an outbox fact, and returns a typed
result. A TUI intervention and the corresponding CLI, SDK, HTTP, or MCP command therefore have the
same behavior and refusal.

Queries use bounded projections and identify their cursor or snapshot version. SSE resumes from a
durable cursor and makes gaps explicit. A projection may lag and is never used as authorization
state. The STDIO MCP bridge translates transport messages into the same HTTP/application contract;
it does not cache authoritative mission or policy state.

The mission component stores versioned collaboration plans, immutable health evaluations,
adaptation decisions, and retrospective metadata through the same application transaction and
outbox boundary. It derives collaboration-history projections from accepted mission evidence rather
than maintaining a second registry. Deterministic signal collection is event-driven and bounded;
model-backed interpretation is dispatched as an independent CrewAI evaluation task at milestones,
never inside the task-completion transaction.

## 5. CrewAI integration boundary

The CrewAI module receives bounded authorized organizational-turn envelopes for clarification,
planning, research, learning, production, evaluation, and reporting, as well as accepted mission
and task-plan revisions with exact bindings and policy-scoped execution context. It materializes
supported CrewAI Agents, Tasks, Crews, Processes, and Flows directly and records CrewAI runtime
identities as lineage. Candidate Mission Briefs, plans, technical decisions, task results,
evaluations, and reports return to their deterministic owning responsibility for validation and
durable acceptance.

MISHKAN does not implement a framework-neutral production agent interface. Deterministic doubles
may replace externalized calls in tests, but no configuration can select them as a production
runtime. CrewAI candidate results still cross deterministic output validation, independent
evaluation, effect evidence, and acceptance before dependent work or mission completion advances.

## 6. Capability, engine, and session boundary

The registry distinguishes metadata discovery from concrete binding. A bindable entry includes an
adapter identity, schemas, observed execution location, readiness evidence, exact version, effects,
scopes, dependencies, and provenance. Toolsets and technical packs are contextual compositions,
not authority records. The accepted plan fingerprints exact bindings.

Every invocation crosses the effect gateway, which resolves real paths, commands, URLs, origins,
credentials, repositories, resources, and declared effects before policy. Execution adapters then
operate outside the transaction and return `completed`, `failed`, `cancelled`, or `uncertain`.
Only compatible configured fallbacks are eligible, and degradation is an explicit event and result
property.

PTY, managed job, browser, and MCP sessions store ownership, location, scopes, cursors, deadlines,
resources, state, and settlement evidence. Session handles are opaque application identities; a
caller cannot reuse authenticated browser state, shell environment, or MCP authority from another
task implicitly.

### Mission environment resolution

The mission-environment module is owned by RSP-025. It exposes observed eligible engines, formats,
locations, compatibility constraints, and unknowns to RSP-005 before a bounded CrewAI planning
turn. RSP-005 owns the resulting agent-authored `MissionEnvironmentPlan`; RSP-025 then validates its
requested outcome and resolves one binding per materially distinct repository, greenfield
workspace, service group, platform, or execution location. A binding fingerprints the accepted
proposal, evidence, selected adapter, descriptor identities, verification, and affected plan tasks.
An incompatibility returns to planning and never becomes a silent alternative engineering choice.

The module validates proposed formats or performs only the bounded selection explicitly left by
the accepted plan. It does not author project files or operate containers by itself. An accountable
CrewAI task produces descriptor bytes and invokes the gateway; manifests terminate at RSP-023. A
proposed project materialization is a typed change set dispatched through RSP-011's Edit/Patch
adapter. Image build, environment start, readiness probes, project commands, and cleanup cross the
same gateway through direct process, Bash, managed job, or a justified typed adapter. Their effects
remain individually authorized and settled.

Format adapters are independent and versioned. The Dev Container adapter validates the selected
specification and its image, build, or supported multi-container references. Podman image builds
consume verified Containerfile or Dockerfile inputs; Podman Kubernetes YAML and Quadlet are
separate runtime/service adapters selected only for those semantics. Compose is bound only through
a verified Compose-compatible adapter. None of these names proves host availability or makes a
generated description ready.

#### Environment module internals

The environment module is one application component with explicit internal ports; these ports are
not separately deployable authorities.

```mermaid
flowchart LR
    Planning["Planning / accepted MissionEnvironmentPlan"]
    Crew["CrewAI environment task"]
    Context["Context evidence"]
    Registry["Engine and adapter registry"]
    Gateway["Effect gateway"]
    Artifact["Artifact store"]

    subgraph Env["Mission environment component · RSP-025"]
        Observe["Observation assembler"]
        Eligible["Eligibility presenter"]
        Validate["Proposal compatibility validator"]
        Resolve["Per-context binding resolver"]
        Descriptor["Descriptor-set contract validator"]
        Verify["Verification and settlement recorder"]
        Invalidate["Context invalidation evaluator"]
    end

    Context --> Observe
    Registry --> Observe
    Observe --> Eligible
    Eligible --> Planning
    Planning --> Validate
    Validate --> Resolve
    Registry --> Resolve
    Resolve -->|"binding or incompatibility"| Planning
    Planning -->|"authorized generation task"| Crew
    Crew -->|"candidate bytes and rationale"| Gateway
    Gateway --> Artifact
    Artifact --> Descriptor
    Descriptor --> Planning
    Crew -->|"build · start · probe · cleanup"| Gateway
    Gateway --> Verify
    Verify --> Planning
    Context --> Invalidate
    Invalidate --> Planning
```

The observation assembler merges repository, prospective-workspace, execution-location, worker,
engine, policy-relevant, and existing-definition evidence without deciding an outcome. The
eligibility presenter returns candidates and unknowns to the CrewAI planning turn. The proposal
validator checks the normalized agent-authored request; the resolver selects only within its
bounded constraints and returns incompatibility rather than silently changing `reuse_existing`,
`host_native`, `generate`, `propose_project_change`, or `unresolved`.

The descriptor-set validator validates identities, formats, references, bases, and secret-free
structure after an accountable CrewAI task has produced candidate bytes. It does not generate those
bytes. The verification recorder consumes settled effect evidence and never treats an adapter
return, image identifier, or ready-looking file as accepted mission state by itself. The
invalidation evaluator compares stored fingerprints with new authoritative observations and emits
a replanning dependency; it does not mutate the accepted plan directly.

#### Environment application operations

| Operation | Input authority | Durable result | External effect |
|---|---|---|---|
| Observe environment context | accepted Mission Brief context and configured safe probes | `EnvironmentObservation` revision with facts and unknowns | read/probe only through governed capabilities |
| Present eligible choices | observation and registry snapshots | bounded candidate set attached to planning input | none |
| Submit environment proposal | CrewAI result plus accountable identity and lineage | normalized `MissionEnvironmentPlan` candidate | none |
| Resolve context binding | accepted plan revision and exact context fingerprint | compatible `EnvironmentBinding` or precise incompatibility | none |
| Commit descriptor set | accepted generation result and immutable member artifacts | `EnvironmentDescriptorSet` identity and validation state | artifact transfer only |
| Propose project persistence | descriptor set, logical paths, and exact base revisions | typed Edit/Patch change-set reference | no mutation until separately authorized |
| Materialize environment | binding, descriptor artifacts, location, and effect grant | one `EnvironmentAttempt` settlement | build/acquire/start through gateway |
| Verify environment | binding, location fingerprint, required check profile | `EnvironmentVerification` with coverage and limitations | probes/project commands/cleanup through gateway |
| Invalidate binding | changed context, plan, policy, adapter, engine, or evidence | invalidation fact and affected task set | none |

Application commands use expected-current revisions for the plan, binding, descriptor set, and
working references. Long builds, pulls, starts, probes, and cleanup never hold the metadata
transaction open. Their call identities and artifact references are recorded before a short
settlement transaction decides whether the environment remains unverified, failed, cancelled,
uncertain, verified, or invalidated.

#### Format and engine adapter boundaries

- A Dev Container format adapter validates the selected specification version, feature references,
  mounts, lifecycle commands, user model, and image/build/Compose references. A separate observed
  CLI adapter performs supported build or up operations when available.
- OCI build-input validation is distinct from execution. A Containerfile or Dockerfile can bind to
  a verified Podman, Docker/BuildKit, or other compatible engine without changing its recorded
  dialect and compatibility evidence.
- Podman Kubernetes YAML and Quadlet adapters exist only for requested pod/service lifecycle
  semantics. They are not automatic companions to every Containerfile.
- Compose is a descriptor family whose provider and compatibility mode are observed. Docker
  Compose, Podman Compose, or another configured implementation cannot be substituted silently.
- Host-native, Nix, mise, language-toolchain, and CI descriptions use the same binding and evidence
  contracts. Container formats receive no architectural priority merely because adapters exist.

Descriptor bytes and logs are immutable artifacts. Proposed logical project paths and their
change-set journal belong to Edit/Patch. Running process groups, jobs, containers, volumes, and
networks belong to the execution/effect boundary. Environment records reference those identities;
they do not duplicate their lifecycle state or cleanup authority.

## 7. Artifact and persistence architecture

Local mode uses SQLite in WAL mode and a filesystem content-addressed store under the project
metadata area. Distributed mode requires PostgreSQL and an S3-compatible blob store. Both profiles
implement the same repository, transaction, lease, outbox, artifact-manifest, and compare-and-swap
working-reference semantics.

| Consistency boundary | Atomic metadata records |
|---|---|
| Mission launch or revision | Mission Brief, PM/CTO confirmations, crew revision, command, state transition, outbox fact |
| Mission environment binding | accepted plan fingerprint, requested outcome, binding revision, adapter/location identity, compatibility or refusal, verification state, outbox fact |
| Plan acceptance | plan version, context fingerprints, agent authorship, environment proposal, policy decision, exact skill/tool/engine bindings, outbox fact |
| Result acceptance | accepted attempt, validated artifact references, dependency release, outbox fact |
| Intervention | expected current state, actor and authority, scoped effect, resulting state, outbox fact |
| Skill/profile activation | candidate version, evidence and policy decision, active pointer, lineage, outbox fact |
| Working-reference update | expected current revision, new immutable revision, conflict or new pointer, outbox fact |
| Schedule trigger | occurrence identity, overlap decision, at-most-one run, outbox fact |
| Worker lease | immutable task envelope, worker identity, attempt, expiry, state transition, outbox fact |

Blob transfer and integrity verification occur outside the metadata transaction. The manifest is
made available only after verified content exists. Missing content, partial transfer, or failed
cleanup becomes explicit reconciliation state; it never produces an accepted result.

## 8. Deployment profiles

### Local, cloud, and hybrid

One `mishkand` instance owns application state. SQLite/WAL and filesystem artifacts are sufficient.
Configured local or cloud inference and capability adapters may vary without changing authority.
Knowledge-service loss may degrade eligible work visibly.

### Distributed

PostgreSQL is required for shared coordination and S3-compatible storage for artifact bodies.
Stateless workers advertise observed capabilities, receive task-scoped mTLS identity and leases,
and return structured results and artifacts. Workers never gain acceptance authority. Lease expiry,
duplicate completion, revision mismatch, and partial network failure preserve exactly-once accepted
completion semantics.

## 9. Architecture decisions

### ADR-001 — Transactional modular monolith

**Status:** Accepted by D-035; retains D-021

Keep relational current state authoritative and append an outbox fact in the same short
transaction. Keep all application authority in `mishkand`; split processes only for clients,
workers, or justified isolation.

### ADR-002 — Persistence and artifact profiles

**Status:** Accepted by D-035; retains D-015

Use SQLite/WAL plus filesystem CAS locally and PostgreSQL plus S3-compatible blobs in distributed
mode. Engineer-executed migrations implement one explicit repository contract; startup never
silently mutates an unsupported schema.

### ADR-003 — One operational application interface

**Status:** Accepted by D-035; retains the HTTP/SSE basis from D-016

Expose the same commands and queries through CLI, SDK, chat, TUI, HTTP/OpenAPI, SSE, and the MCP
facade. The local STDIO bridge remains non-authoritative. There is no version 1 Web dashboard.

### ADR-004 — Direct CrewAI production integration

**Status:** Accepted by D-035; retains D-002 and D-016

Integrate CrewAI 1.x directly. Do not expose a production runtime selector or introduce an
`AgentRuntime` abstraction that competes with CrewAI coordination.

### ADR-005 — Contextual registries and typed adapters

**Status:** Accepted by D-035; retains the general-tool basis from D-029

Resolve native capabilities, skills, tools, engines, environments, packs, and external protocols
from configured sources and observed project/location evidence. Bind only concrete runnable
adapters. A mission environment decision may reuse observed state or generate attributable
descriptor artifacts, but it is authored as Mission Crew plan content through CrewAI; the resolver
cannot replace it with another engineering outcome. Materialization and verification still cross
ordinary governed effect boundaries. Discovery, availability, credentials, instructions, and
generated files grant no authority.

## 10. Gate effect

System Model 1.2 and Architecture 1.2 superseded the version 1.1 amendments and D-030 under D-035;
D-037 accepted the version 1.3 mission-environment detail. D-051 accepts the version 1.4 Mission
Crew effectiveness amendment. Architecture alone grants no effect authority; implementation
continues only under the accepted plan, policy, and increment gates.
