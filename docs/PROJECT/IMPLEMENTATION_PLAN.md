# MISHKAN Implementation and Acceptance Plan

**Status:** Accepted by D-041 on 2026-08-29
**Version:** 1.6
**Derived from:** PRD 1.4, SRS 1.6, Contract 1.4, Responsibility Map 1.2, System Model 1.3, and
Architecture 1.3 accepted by D-032–D-035 and D-037

## 1. Purpose and gate authority

This post-architecture plan defines vertical delivery increments and their acceptance evidence. The
`SWE-BASICS-BEFORE-CODE` framework ends at Sequence 05; this plan does not invent another numbered
framework stage.

I00 and I01 remain accepted exactly as implemented and evidenced. Their code, historical commits,
and validation records are not rewritten. D-032 through D-036 accepted the version 1.4 documentary
baseline in order on 2026-08-25. D-036 resumes I02. D-037 accepted the version 1.5 detail of later
mission-environment delivery. D-041 accepts this version 1.6 delivery amendment and authorizes I05.
It adds an interpretable, content-addressed context-package work package and optional vendor-neutral
telemetry export without changing the accepted requirements, system authority, CrewAI runtime
boundary, or later increment gates.

## 2. Delivery laws

1. CrewAI 1.x is present in production from the walking skeleton and remains the sole runtime for
   agents and teams. There is no production runtime selector or competing `AgentRuntime`.
2. The persistent organization contains 59 professional identities; Mission Crews and task graphs
   are contextual. Free-form objectives do not require a finite outcome catalogue.
3. Mission templates, tools, skills, engines, environments, and packs are versioned inputs selected
   from project and execution-location evidence. They are never static workflows or authority.
4. MISHKAN deterministically owns application state, policy, effects, evidence, artifacts, and
   acceptance. Operational values remain public, versioned, configurable data.
5. Availability, discovery, instructions, and credentials confer no authority. A binding requires
   a concrete runnable adapter and policy decision for the actual target.
6. Commit, push, release, deployment, and migration are typed stateful capabilities, not universal
   prohibitions. Policy may allow, approval-gate, or deny their exact scope.
7. A policy-authorized native skill correction may activate immediately and reversibly. Staging is
   contextual; a newly discovered community extension remains a candidate until authorized.
8. Every increment ends in a runnable path and a red-capable acceptance test. Validation documents
   record only observed evidence after a real gate.
9. Final schema shapes, adapter products, dependency patches, benchmarks, and OS profiles are
   locked by their owning increment, not guessed in this baseline.
10. Every environment-dependent mission records an explicit environment decision. Containerization
    is never assumed: reuse, authorized host-native execution, generation, proposed project change,
    and unresolved dependency are all truthful outcomes.
11. Task context is a bounded, attributable projection of accepted state. A materialized context
    package is portable input for CrewAI or an external harness, not a workflow, authorization
    source, mutable state store, or replacement for durable artifacts.
12. OpenTelemetry is the neutral telemetry boundary. LangSmith MAY be configured as an optional
    derived observability and evaluation destination; it is disabled by default, receives only
    policy-authorized sanitized data, and never owns run state, acceptance, prompts, skills, or
    recovery.

## 3. Target repository boundaries

```text
MISHKAN/
├── AGENTS.md
├── pyproject.toml
├── uv.lock
├── src/mishkan/
│   ├── domain/          # contracts, identities, errors, invariants
│   ├── application/     # commands, queries, transaction orchestration
│   ├── organization/    # 59 profiles, branches, pools, templates
│   ├── missions/        # Mission Brief, crews, lifecycle, assignments
│   ├── conversations/   # channels, messages, decisions, escalations
│   ├── planning/        # agent-authored candidate plans, normalized plans, and revisions
│   ├── crewai/          # sole production agent/team runtime integration
│   ├── policy/          # public policy and deterministic decisions
│   ├── registry/        # tools, engines, packs, adapters, availability
│   ├── environments/    # eligibility, verified bindings, descriptor and materialization evidence
│   ├── capabilities/    # file, edit, process, shell, PTY, Web, Browser
│   ├── mcp/             # client, server facade, mediation, transports
│   ├── skills/          # SKILL.md, bundles, learning, lifecycle
│   ├── knowledge/       # attributed retrieval and degraded operation
│   ├── artifacts/       # immutable manifests and working references
│   ├── persistence/     # transactions, repositories, leases, outbox
│   ├── daemon/          # mishkand HTTP/OpenAPI, SSE, health
│   ├── scheduling/      # durable triggers and history
│   ├── worker/          # stateless remote execution
│   ├── cli/             # thin clients over application commands
│   └── sdk/             # typed Python client and public models
├── definitions/
│   ├── organization/v1/ # exact 59 persistent identities
│   ├── missions/        # optional versioned templates, never task graphs
│   ├── policies/        # public reference policies and profiles
│   ├── tools/           # native contracts and configured toolsets
│   ├── packs/           # contextual technical-pack definitions
│   └── schemas/         # exported versioned JSON Schemas
├── migrations/          # explicit engineer-executed migrations
├── deploy/              # Compose and release packaging
├── tui/                 # separate operational Go/Bubble Tea client
├── tests/               # unit, contract, integration, acceptance, fault, benchmark
└── docs/                # durable authority and observed evidence only
```

These are initial modular ownership boundaries, not service boundaries or permission grants.

## 4. Progressive Git delivery protocol

- Continue the active work on `feat/i02-policy-tools` without rewriting its existing history.
- Use `Y4NN777 <axel.studiesmail@gmail.com>` for every commit.
- Commit coherent behavior with its tests, run the narrow gate plus Ruff, format check, strict mypy,
  and `git diff --check`, then push the green checkpoint normally.
- Merge `topic → develop` only after the owning gate. Promote `develop → main` only after the
  integrated gate. Never merge a topic branch directly into `main`.
- Do not force-push unless the engineer explicitly authorizes a resolved history-repair target.
- MISHKAN's own Git effects still cross the same public capability policy; this protocol governs
  development of MISHKAN.

## 5. Accepted increments retained

### I00 — Contract-bearing foundation — accepted

The installable CLI, versioned configuration, provenance, stable errors, schema compatibility,
UUID/time contracts, and initial verification baseline remain accepted. See `docs/VALIDATION/foundation.md`.

### I01 — Real local CrewAI walking skeleton — accepted

The real CrewAI/Ollama repository-bound initialization path, structured acceptance, durable state,
and repository-specific planning evidence remain accepted. See `docs/VALIDATION/local-crewai.md`.

Neither increment is retroactively claimed to implement requirements introduced after its gate.

## 6. Rebaselined delivery increments

### I02 — Truthful native capabilities

**Runnable result:** A real CrewAI/Ollama task in each of multiple materially different repositories
can resolve and invoke bounded File/Read/Search, direct process, and full Bash capabilities through
the existing registry, policy, and effect gateway. Only adapters that actually execute may bind.
Large output is captured through the minimum immutable Artifact surface.

**Build scope:**

- retain the already tested registry, public policy, gateway, approval, and effect evidence;
- remove every binding whose adapter is abstract, missing, or unavailable at the execution location;
- implement file identity, metadata, listing, bounded reads, literal/glob/regex/structural search,
  provenance, partial coverage, symlink-safe resolution, and mutation-base evidence;
- implement direct process and full Bash semantics with explicit executable or command, arguments,
  working directory, environment delta, stdin, network, credentials, resource bounds, timeout,
  output bounds, declared effects, and settlement;
- add the minimum immutable artifact manifest/body path required when output exceeds bounded inline
  results;
- verify actual paths, symlinks, environment, network, credentials, Git/external effects, and
  uncertain completion without hardcoded private operational bans.

**Primary trace:** PLN-005–008, SAF-001–013, TOL-001–027, FIL-001–007, EXE-001–003,
TC-006.

**Gate:** two repository fixtures produce different exact bindings and native commands; a real
CrewAI/Ollama run succeeds locally; missing adapters never bind; forbidden resolved targets are
refused; secret canaries never persist; an uncertain stateful effect is not retried automatically.

### I03 — Mutations, sessions, artifacts, and durable daemon

**Runnable result:** An authorized mission can apply and verify recoverable change sets, operate PTY
and managed jobs, store complete artifacts, survive process loss, and resume through `mishkand`
from the first required result not accepted.

**Build scope:**

- implement structured edits and patches, exact base preconditions, recovery journals, verification,
  conditional rollback, command-driven mutation, and explicit Git effects;
- implement PTY and managed jobs with owner, readiness, cursors, signals, cancellation, deadlines,
  settlement, loss, and uncertainty;
- complete immutable artifacts, streaming, media validation, collections, compare-and-swap working
  references, retention, hold, garbage collection, missing-content recovery, and previews;
- introduce `mishkand`, short SQLAlchemy transactions, SQLite/WAL, explicit migrations, outbox,
  snapshots, resumable SSE, JSONL export, and typed application commands;
- complete task lifecycle, dependency release, bounded predicates, duplicate completion,
  cancellation, crash checkpoints, and deterministic resume.

**Primary trace:** SYS-006, RUN-004–005, RUN-007–012, EDT-001–008, EXE-004–008,
ART-001–008, OBS-001–008, NFR-003–004, TC-009.

**Gate:** crash during edit, artifact transfer, PTY/job execution, acceptance, and event delivery;
prove recovery without silent overwrite or repeated accepted effect, CAS conflict detection, cursor
gap detection, and at least 100 valid events per second on the reference environment.

### I04 — Web, Browser, MCP, and external harnesses

**Runnable result:** CrewAI tasks use typed Web and Browser surfaces and mediated MCP peers, while
external harnesses submit and inspect governed MISHKAN work through HTTP/OpenAPI or MCP without a
runtime or policy bypass.

**Build scope:**

- implement configured Web search, retrieval, extraction, crawl, cache, provenance, citation,
  redirects, network policy, SSRF protection, and truthful degradation;
- provide concrete adapters for the roles selected by configuration, including direct Brave search,
  SearXNG brokering, configured free search sources, HTTPX transport, configured extraction, and a
  configured crawler, without treating a component name as availability proof;
- implement Playwright browser sessions and Chrome DevTools diagnostics with observation-bound
  targets, origin checks, authenticated-state sensitivity, screenshots, and uncertain effects;
- implement MCP client, application server facade, and mediation over STDIO and Streamable HTTP,
  including identity, discovery, schema drift, progress, cancellation, reconnect, and long work;
- expose HTTP/OpenAPI, resumable SSE, and the non-authoritative local STDIO MCP bridge to compatible
  harnesses.

**Primary trace:** WEB-001–007, BRW-001–008, MCP-001–009, TC-008.

**Gate:** contract tests cover configured search roles, provenance, SSRF and redirect escapes,
authenticated browser state, stale observations, MCP schema drift, reconnect, cancellation, and
indeterminate external effects. A harness request reaches CrewAI only through an accepted
application command.

### I05 — Skills and Engineering Tools foundation

**Runnable result:** MISHKAN recognizes engineer/project/machine context, selects concrete
`SKILL.md` packages and technical packs progressively, applies policy-authorized live corrections,
and truthfully materializes representative development environments.

**Build scope:**

- implement confirmed engineer profile, privacy-safe project recognition, safe machine probes,
  independent observed states, provenance, configured community catalogues, and inspectable
  recommendations that never auto-activate;
- implement SKILL.md packages, Level 0 catalogue, Level 1 instructions, Level 2 references, bundles,
  contextual selection, hit/partial/miss evidence, slash invocation, and `/learn`;
- implement a versioned `ContextPack` manifest that projects exact identity, Mission Brief and plan
  revisions when available, task contract, selected skills, attributed references, accepted input
  artifacts, output contract, verification rules, token/size bounds, and source fingerprints;
- materialize context packages deterministically from authoritative MISHKAN state and immutable
  artifacts. Directory layout is an inspectable interchange form only: folder order MUST NOT become
  scheduling state, edits MUST NOT grant authority, and generated packages MUST reproduce from the
  same manifest and referenced bytes;
- introduce a vendor-neutral OpenTelemetry observation port with explicit `off`, `metadata_only`,
  and `sanitized` disclosure profiles. Add LangSmith only as an optional exporter selected through
  public configuration and credential references; exporter failure is visible degradation and
  cannot change mission settlement;
- correlate derived spans and evaluation evidence by mission, run, task, agent, plan, binding, and
  context-package fingerprints. External evaluations remain candidate evidence and MUST NOT bypass
  MISHKAN validation, independent evaluation, or durable acceptance;
- implement provenance, trust, configurable scans, quarantine, immediate/review/staged/deny policy,
  coherent live patch/edit, atomic activation, reset, archival, restoration, and crash recovery;
- implement engine discovery, adapter justification, independent availability states, technical
  packs, reproducible environment materialization, evidence adapters, and visible fallback;
- define and publish versioned `EnvironmentObservation`, `EnvironmentBindingRequest`,
  `EnvironmentBinding`, `EnvironmentDescriptorSet`, and `EnvironmentVerification` contracts with
  stable compatibility, lifecycle, provenance, affected-task, and refusal semantics;
- discover project-owned environment evidence without mutation: Dev Container definitions,
  Containerfile/Dockerfile inputs, verified Compose files, Podman Kubernetes/Quadlet inputs, Nix or
  mise configuration, language manifests and lockfiles, CI setup, documented commands, target
  platforms, and safe engine/version probes;
- resolve accepted requests for `reuse_existing`, `host_native`, `generate`,
  `propose_project_change`, and `unresolved`, including multiple bindings for missions that span
  different repositories, services, platforms, or execution locations. Return a precise
  incompatibility for replanning instead of substituting another engineering outcome;
- implement independent versioned adapters for Development Container metadata and the selected
  conforming CLI; Podman detection plus Containerfile/Dockerfile build and bounded run behavior;
  and, only where required by the operation, Podman Kubernetes YAML or Quadlet. Treat Compose as a
  separate compatibility binding whose concrete provider must be observed and verified;
- generate the smallest mission-specific descriptor set as immutable artifacts and optional typed
  Edit/Patch change sets. Preserve base revisions, target platform/architecture, base-image
  identity, build context, features/dependencies, mounts, user model, network/resources,
  credential references, lifecycle commands, cleanup, and compatibility limits when applicable;
- verify parse/schema validity, acquisition or build, startup/readiness, workspace access,
  dependency availability, representative project commands, artifacts, cleanup, and repeatability
  at the intended location. Preserve logs, image identities, effects, timing, limitations, and
  `verified`, `failed`, `cancelled`, or `uncertain` settlement;
- prove that existing project definitions are reused when compatible and cannot be overwritten by
  generation alone; project persistence must cross I03 Edit/Patch while build/start/probe/cleanup
  crosses I03 Terminal/Process or a justified typed adapter;
- accept project commands as Terminal/Process inputs and provide representative fixtures for Go,
  JavaScript/TypeScript, Java/Kotlin, Android, Python, Rust, C, and Swift.

**Primary trace:** CTX-001–008, SKL-001–025, ENG-001–013.

**Gate:** a project-specific missing procedure can yield a Research-authored candidate; effective
policy can activate a safe native correction immediately or require review; a community candidate
cannot self-install; every fixture proves actual engine/environment state or an honest unsupported
result; restoration is byte-identical and retains evidence. Environment acceptance additionally
uses at least: one existing Dev Container reused without mutation; one greenfield descriptor set
generated and verified; one Podman Containerfile build and bounded run on a compatible worker; one
multi-service or lifecycle fixture that either proves its selected Compose/Podman adapter or
truthfully refuses it; one incompatible platform; one secret-reference case; one stale-base
conflict; one build interruption with non-fabricated settlement; and one cleanup/re-run proving the
declared reproducibility boundary.

Context-package acceptance additionally proves deterministic rematerialization, exact source
attribution, bounded layered loading, omission and staleness visibility, and rejection of a direct
filesystem edit as authority. Telemetry acceptance proves that `off` emits nothing externally,
`metadata_only` exports no prompt, result, credential, path content, or artifact body, `sanitized`
passes the configured content inspector before export, and an unavailable or rate-limited exporter
cannot block or alter the authoritative run. No LangSmith Prompt Hub object or remotely supplied
serialized prompt becomes a skill, prompt, plan, or executable configuration in I05.

#### I05 context and observability work packages

These packages adopt the useful context-structure principles of Interpretable Context Methodology
without adopting filesystem orchestration. They make the context boundary inspectable and
reproducible while retaining MISHKAN state, policy, CrewAI coordination, and artifacts as the
respective authorities.

| Package | Depends on | Runnable deliverable | Required proof |
|---|---|---|---|
| I05-C01 — Context contract | I03 Artifact, accepted context and skill contracts | versioned `ContextPackManifest`, typed source references, disclosure and size budgets, output and verification contracts | schema round-trip; exact identity/revision provenance; duplicate logical paths and unresolved required sources refuse |
| I05-C02 — Deterministic materialization | C01 plus File/Artifact reads | bounded materialized workspace with identity, task contract, stable references and per-run inputs separated by layer | same manifest and bytes reproduce the same digest; changed source invalidates it; direct edits do not change authoritative state |
| I05-C03 — Layered consumption evidence | C01–C02 plus skill selection | progressive Level 0/1/2 load record and context hit/partial/miss evidence attached to the consuming task | only declared needed content loads; omissions, truncation, staleness and source lineage remain visible |
| I05-C04 — Neutral telemetry | application events, CrewAI boundary and Content Inspector | optional OpenTelemetry span projection with bounded public disclosure profiles and stable MISHKAN correlation fields | exporter disabled by default; no secret canary crosses the exporter; exporter failure degrades without changing run state |
| I05-C05 — LangSmith evaluation adapter | C04 | optional LangSmith OTLP/SDK destination for trace inspection and evaluation experiments, without prompt/configuration import | cloud test uses an explicit credential reference and disposable project; local fake proves payload contract; feedback imports only as attributable candidate evidence |

C01–C03 complete before context packaging is offered to external harnesses. C04 is vendor-neutral;
C05 is optional and MUST NOT become a dependency of local execution. The ContextPack manifest, not
the presentation directory, is fingerprinted into task evidence. Knowledge retrieval introduced in
I07 may contribute attributed source artifacts to this contract without changing it into a live,
unbounded retrieval channel.

#### I05 environment work packages

These packages implement the general environment capability. They do not originate a mission or
choose a mission's engineering outcome.

| Package | Depends on | Runnable deliverable | Required proof |
|---|---|---|---|
| I05-E01 — Observation | I02 File/Search/Process and context contracts | read-only observation of repository, prospective workspace, machine/worker, existing descriptors, platform, architecture, engines, versions, and unknowns | fixture snapshots distinguish observed, detected, executable, healthy, project-used, and authorized; probes do not mutate |
| I05-E02 — Contracts and persistence | I03 metadata/outbox/artifacts | repositories and schemas for `EnvironmentObservation`, `EnvironmentBindingRequest`, `EnvironmentBinding`, `EnvironmentDescriptorSet`, `EnvironmentAttempt`, and `EnvironmentVerification` | schema round-trip, version compatibility, optimistic-concurrency conflict, outbox atomicity, and artifact-reference integrity |
| I05-E03 — Eligibility and resolution | E01–E02 plus registry/policy | deterministic candidate presentation and compatibility-only resolution for the five requested outcomes | resolver cannot author or substitute an outcome; incompatible platform, missing adapter, stale observation, and changed policy return bounded evidence |
| I05-E04 — Dev Container format | E02–E03 | validation of selected Development Container metadata and referenced image/build/Compose inputs; concrete CLI binding only when observed | existing definition reused byte-for-byte; schema/version incompatibility visible; generated definition remains an artifact until Edit/Patch |
| I05-E05 — OCI and Podman build | E02–E03, I03 Process/Job | Containerfile/Dockerfile dialect evidence, Podman availability binding, bounded image build/run/probe/cleanup, and image identity capture | compatible build/run succeeds; absent Podman is unavailable rather than simulated; timeout/cancel/loss settles honestly; no secret enters build context |
| I05-E06 — Multi-service lifecycle | E02–E03, I03 sessions | independently bound Compose provider and optional Podman Kubernetes YAML/Quadlet lifecycle adapters | provider compatibility is observed; unsupported semantics refuse; readiness differs from liveness; cleanup and residual resources are evidenced |
| I05-E07 — Descriptor generation path | E02–E06, I03 Artifact/Edit | authorized request result contract accepts context-specific descriptor members as immutable artifacts and optionally produces exact-base change sets | no module-authored engineering outcome; no overwrite of compatible existing definitions; stale base conflicts; logical paths cannot escape project scope |
| I05-E08 — Verification and invalidation | E01–E07 | location-bound verification profiles, settlement, dependency evidence, and context-fingerprint invalidation | parse/build/start/workspace/dependency/project-command/artifact/cleanup coverage is explicit; changed context invalidates only matching bindings |

E01–E03 are format-neutral and complete first. E04–E06 are independent adapters and may proceed in
parallel once their contracts are stable. E07 composes descriptor artifacts and project changes;
it does not create another file writer. E08 consumes real effect results and is the only package
that can produce a location-bound `verified` record. A descriptor adapter can report validation,
not mission readiness.

Initial application operations are versioned commands and queries for observation, candidate
inspection, compatibility resolution, descriptor-set validation, materialization attempt,
verification, invalidation, and bounded status/history. Their final transport names are frozen in
I05 contract tests; CLI, SDK, HTTP, MCP, CrewAI tools, and later TUI clients call the same
application semantics rather than implementing provider-specific control paths.

### I06 — Organization, missions, communication, and professional evolution

**Runnable result:** PM and CTO agents turn a free-form CEO objective into a Mission Brief, compose a
temporary crew from the 59 persistent identities, coordinate CrewAI work through independent
assurance and reporting, converse durably, and process rare governed CEO interventions.

**Build scope:**

- load the exact SRS roster from versioned professional definitions with explicit branches, pools,
  independence, authority, and profile evidence;
- implement free-form mission origins, optional templates, Mission Briefs, crew revisions,
  accountable assignments, lifecycle, PM/CTO agreement, scoped disagreement, and CEO escalation;
- add environment intent to the Mission Brief and require a versioned mission environment decision
  before dependent tasks become eligible. PM confirms product/developer-experience needs, CTO
  confirms platform, security, quality, and operability coverage, and the Mission Lead tracks the
  decision as a plan dependency rather than selecting a container format by role;
- implement a bounded CrewAI environment-planning turn in which an accountable Mission Crew agent
  authors `MissionEnvironmentPlan` from the Brief, observed eligible candidates, project evidence,
  alternatives, and unknowns. Normalize and accept it through RSP-005; let RSP-025 only validate
  compatibility and resolve the requested binding or return an incompatibility for replanning;
- support per-context environment bindings inside one mission; expose reused definitions,
  generated descriptor artifacts, proposed project change sets, verification evidence,
  degradation, and affected tasks through mission inspection and conversations;
- when repository revision, platform, required engine, policy, or mission scope invalidates a
  binding, create a new environment and plan revision, preserve prior evidence, and pause only
  tasks that depend on the invalid binding;
- support greenfield, existing, multi-repository, product, research, incident, modernization,
  platform, and operational missions without a fixed workflow catalogue;
- implement Executive, Mission, Branch, and authorized Direct conversations; separate messages,
  events, commands, decisions, escalations, and notifications;
- implement comment, answer, approve/reject, pause, resume, reassign, stop, and risk-accept commands
  with full attribution and client parity;
- implement evidence-based professional evolution without self-certification, authority change,
  independence change, or failure erasure;
- expose `org`, `mission`, `conversation`, `intervention`, and `advisory` through CLI, SDK, HTTP, and
  MCP application contracts.

**Primary trace:** PRJ-008–010, PLN-012–021, ORG-001–016, MSN-001–016.

**Gate:** materially different mission fixtures produce different Briefs, crews, plans, tools, and
evidence; PM/CTO disagreement pauses only dependent work and pings the CEO; producer/evaluator and
orchestrator/reporter conflicts are rejected; a restart preserves the Executive conversation and
an authorized intervention has identical TUI-independent API semantics. Greenfield,
existing-project, multi-repository, and platform-specific fixtures produce different environment
decisions; no dependent task starts from a generated-but-unverified descriptor; and a binding
revision pauses only its dependent task set. Tests also reject resolver-authored outcomes, missing
CrewAI authorship lineage, missing accountable owners, and silent substitution after an adapter
incompatibility.

#### I06 mission-environment work packages

| Package | Accountable behavior | Durable output | Gate evidence |
|---|---|---|---|
| I06-M01 — Brief intent | PM/CTO clarification records locations, platforms, existing definitions, isolation, network, resources, credentials by reference, required checks, and known unknowns | Mission Brief environment-intent revision | omission of a required fact blocks only dependent planning; no default container format is inferred |
| I06-M02 — Context partition | planning identifies materially distinct repositories, prospective workspaces, service groups, platforms, architectures, and execution locations | versioned `EnvironmentContext` set with task dependencies | multi-repository and mixed-location fixtures create separate contexts; equivalent contexts are not duplicated |
| I06-M03 — CrewAI proposal | one contextually selected Mission Crew agent owns a bounded CrewAI task that compares eligible alternatives and requests one outcome per context | `MissionEnvironmentPlan` candidate with owner, CrewAI lineage, rationale, constraints, effects, verification, cleanup, alternatives, and unknowns | role, template, installed engine, or resolver alone cannot choose; consequential choices cross PLN-012–018 |
| I06-M04 — Plan acceptance and binding | RSP-005 validates the proposal and RSP-025 resolves only compatible adapters/locations | accepted plan revision plus binding or precise incompatibility | resolver-authored proposal and silent fallback are rejected; replanning preserves compatible accepted work |
| I06-M05 — Generation and verification tasks | CrewAI releases accountable descriptor, mutation, build/start/probe/cleanup, evaluation, and reporting tasks according to dependencies | accepted descriptor artifacts/change sets, attempts, verification, evaluation, and report references | producer cannot independently evaluate generated environment; description alone releases no runtime-dependent task |
| I06-M06 — Revision and communication | mission governance exposes state and reacts to context, policy, adapter, or scope changes | binding revision, scoped pause/resume, conversation/event/escalation evidence | only dependent tasks pause; PM/CTO and CEO clients see identical state and can govern the same commands |

The proposal owner is selected from the actual Mission Crew by competence, evidence, availability,
conflict, and scope. `Platform_Engineer` or an architect may often be suitable, but no persistent
identity receives a static environment-generator privilege. Contributors may supply product,
architecture, security, delivery, reliability, developer-experience, or project evidence while one
owner remains accountable and independent assurance remains separate.

#### Environment delivery boundary across increments

| Increment | Environment responsibility | Acceptance boundary |
|---|---|---|
| I02 | Read project evidence and execute bounded direct/Bash probes | No environment generation or mutation is claimed |
| I03 | Supply immutable artifacts, Edit/Patch change sets, jobs, recovery, and durable events | Descriptor bytes and effects can be stored, applied, observed, and recovered truthfully |
| I05 | Observe engines and context; resolve accepted requests; generate, materialize, verify, and settle bindings | The environment capability works independently without choosing engineering outcomes |
| I06 | Have Mission Crew agents author the environment plan; make it a required dependency visible to PM, CTO, crews, and clients | Environment-dependent tasks cannot outrun their accepted proposal and verified binding |
| I09 | Re-resolve and verify bindings against advertised remote-worker location and immutable envelope evidence | A local verification is never reused as proof for an incompatible worker |
| I11 | Expand platform/version fixtures, benchmarks, fault tests, packaging, and operational guidance | Supported combinations are published only from measured conformance evidence |

I05 MUST NOT invent its own file mutation, process lifecycle, artifact store, policy, mission
runtime, or engineering decision-maker. I06 MUST NOT infer an environment from an agent role,
optional mission template, or installed engine. The cross-increment chain is the agent-authored
`MissionEnvironmentPlan`, its resolved `EnvironmentBinding`, and referenced artifacts, change sets,
adapter evidence, and plan dependencies.

#### Mission-environment acceptance scenario matrix

| Scenario | Required mission decision | Required evidence before dependent work |
|---|---|---|
| Existing repository with compatible `.devcontainer` | `reuse_existing` unless an agent-authored consequential decision justifies otherwise | exact observed files and revisions, specification/CLI compatibility, location-bound build or up, readiness, project command, and cleanup |
| Greenfield isolated development | `generate` or `propose_project_change` with bounded format constraints | mission-specific Dev Container and any referenced build input as artifacts; optional exact-base project change; verified target platform and declared lifecycle |
| Podman image workflow | requested `generate`/`reuse_existing` bound to observed Podman | Containerfile/Dockerfile dialect and base image identity, build context, build result, bounded run, readiness/project checks, logs, image identity, cleanup settlement |
| Podman service lifecycle | explicit service/lifecycle semantics | verified reason for Kubernetes YAML or Quadlet, supported Podman version and target, start/readiness/stop/cleanup evidence; no automatic generation beside every image build |
| Existing Compose project | `reuse_existing` with an exact provider binding | Compose document revisions, observed Docker Compose/Podman Compose/provider compatibility, service readiness and cleanup; provider substitution requires replanning |
| Host-native project toolchain | `host_native` | exact engine/toolchain versions, location, environment delta, required commands, limits, and evidence showing isolation requirements are still satisfied |
| Multi-repository or mixed worker mission | one requested outcome and binding per material context | distinct context and location fingerprints, dependency mapping, no reuse of verification across incompatible locations |
| Unsupported platform or absent engine | `unresolved` or explicit replanning | precise missing compatibility/adapter/authority evidence and scoped blocked tasks; no fabricated descriptor or readiness |
| Changed repository base or descriptor | revised proposal/binding when material | stale-base conflict, preserved prior artifacts, new context fingerprint, and invalidation of only affected task bindings |
| Secret-bearing dependency acquisition | any compatible outcome using references only | late credential resolution, no secret in plan/descriptor/build context/log, redaction evidence, and cleanup of temporary credential exposure |
| Interrupted build/start/cleanup | unchanged decision until reconciliation proves otherwise | `cancelled` or `uncertain` attempt, bounded logs/artifacts, observed residual resources, reconciliation action, and no blind retry of an uncertain effect |

Acceptance uses real adapters when the scenario claims support. A skipped engine-specific fixture can
prove only truthful unavailability, not adapter conformance. Supported Dev Container, Podman,
Compose, platform, and architecture combinations are published from these measured fixtures in I11
rather than presumed by the design.

#### Exact requirement-to-package trace for mission environments

| Requirement or invariant | Primary implementation package | Acceptance evidence |
|---|---|---|
| PLN-021 — agent-authored proposal | I06-M03–M04 | CrewAI lineage, accountable owner, alternatives/unknowns, descriptor constraints, affected tasks, verification and cleanup criteria; deterministic resolver cannot originate it |
| MSN-016 — Brief intent and eligibility dependency | I06-M01–M02, I06-M06 | complete intent or explicit unknown; context partition; unresolved dependency and scoped pause; no environment-dependent task released early |
| ENG-009 — per-context requested outcome and binding | I05-E01–E03, I06-M04 | one of five outcomes per material context, compatible versioned binding or precise incompatibility, no silent substitution |
| ENG-010 — truthful descriptor selection | I05-E03–E06, I06-M03 | format/engine/specification versions selected only for requested semantics and observed compatible adapters |
| ENG-011 — descriptor evidence | I05-E02, I05-E07 | immutable member artifacts preserve applicable base, platform, image, build, mount, user, network/resource, credential-reference, lifecycle, artifact, and limitation fields |
| ENG-012 — governed mutation | I05-E07 with I03 Edit/Artifact | artifact-first generation, exact-base preview/application, authorization, recovery, rollback, and existing-definition protection |
| ENG-013 — verification and settlement | I05-E05–E08, I06-M05 | location-bound coverage and `verified`/`failed`/`cancelled`/`uncertain` settlement with logs, identities, effects, limitations, and cleanup |
| CTR-020 / INV-038–039 — contextual truth and fallback | I05-E01–E03, I05-E08 | independent states, concrete adapter, visible degradation, semantics-preserving fallback only |
| INV-042 — description is not readiness | I05-E04–E08, I06-M05 | generated or parsed descriptors cannot release runtime-dependent tasks without matching verification |
| RSP-005/008/022/025 separation | I06-M01–M06 | PM/CTO intent, CrewAI proposal, plan validation, compatibility resolution, effects, evaluation, and acceptance remain separately attributable |

### I07 — Attributed knowledge and degraded operation

**Runnable result:** Each explicit `literal`, `episodic`, `semantic`, or `structural` query selects
only compatible configured sources and produces an immutable attributed evidence bundle. Safe work
continues with visible limitations when optional services fail; no hidden classifier selects the
query class.

**Build scope:** implement versioned self-hosted mem0 and Cognee clients; integrate Graphify through
its supported CLI/MCP surface; add initial and incremental refresh, explicit-class source
selection, attribution, staleness, miss evidence, timeouts, promotion decisions, and local Compose
profiles using configured Ollama routes rather than paid dependencies.

**Primary trace:** KNW-001–006.

**Gate:** current API contract tests, incremental graph evidence, cheapest-compatible selection,
and useful degraded execution with all optional knowledge services unavailable.

### I08 — Persistent headless scheduling

**Runnable result:** A timezone-aware scheduled mission survives daemon restart, creates at most one
run per occurrence, respects overlap policy, and can be invoked through generated OS scheduler
definitions.

**Build scope:** pin a verified stable APScheduler 3.11.x patch; implement persistent jobs, IANA
timezone validation, one-shot and five-field cron, run-now, pause, resume, remove, history,
coalescing, configurable misfire/retry/backoff, overlap decisions, and exports for cron, systemd,
and launchd against the same idempotent run command.

**Primary trace:** AUT-001–007.

**Gate:** restart, duplicate trigger, timezone rejection, overlap, misfire, retry, history, and
equivalent internal/OS-triggered invocation tests.

### I09 — Distributed execution

**Runnable result:** At least two stateless workers execute immutable envelopes with individual mTLS
identity; loss of one expires its lease and recovers work while exactly one completion is accepted.

**Build scope:** require PostgreSQL and shared blob storage; implement short-lived one-time
enrollment, certificate issue/rotation/revocation, capabilities, heartbeats, leases, idempotent
claims, immutable envelopes, revision checks, artifacts/patch results, loss recovery, duplicates,
timeouts, and partial-network reconciliation.

**Primary trace:** DST-001–010.

**Gate:** Compose-based two-worker tests prove individual revocation, revision mismatch refusal,
worker loss recovery, expired lease behavior, duplicate completion containment, and exactly-once
accepted result semantics.

### I10 — Operational Go/Bubble Tea TUI

**Runnable result:** `mishkan watch` provides live organization-to-task drill-down and can issue the
same authorized interventions as other clients while staying responsive, bounded, and recoverable.

**Build scope:** implement Overview, Missions/Runs, Agents, Teams/Branches, Conversations/Events,
Knowledge, Security, and Schedules views; snapshot plus SSE, filtering/search, compact layout,
bounded history, reconnect/backoff, offline status, accessible help, and explicit command
confirmation. Every mutation calls the application API and owns no local authority.

**Primary trace:** NFR-006, TC-004.

**Gate:** Bubble Tea routing, resize, filtering, bounded buffers, reconnect, daemon restart, event
burst, golden snapshots, complete drill-down, and command-parity contract tests.

### I11 — Complete packs, hardening, and release

**Runnable result:** Linux and macOS Python packages, daemon/worker images, and Go TUI binaries pass
repeatable security, reliability, performance, packaging, migration, and recovery gates with SBOMs
and signed artifacts.

**Build scope:** complete technical packs without making them mandatory; lock supported dependency
patches and OS profiles from evidence; run branch coverage, contract/E2E modes, benchmarks, fault
injection, dependency and secret scanning; publish packages, images, binaries, SBOMs, signatures,
upgrade/migration guidance, and operational runbooks. Database migrations remain engineer-executed.

**Primary trace:** NFR-001, NFR-005, NFR-008–010, TC-005.

**Gate:** Python 3.11–3.13 Linux/macOS matrices, at least 80 percent branch coverage for the named
critical modules, startup/event/advisory gates, secret containment, process/service/network fault
tests, verified SBOM/signatures, and clean backup/restore and explicit migration rehearsals.

## 7. Dependency and release order

```mermaid
flowchart LR
    I00["I00 accepted"] --> I01["I01 accepted"]
    I01 --> I02["I02 native capabilities"]
    I02 --> I03["I03 mutations + daemon"]
    I03 --> I04["I04 Web + Browser + MCP"]
    I04 --> I05["I05 skills + engineering foundation"]
    I05 --> I06["I06 organization + missions"]
    I06 --> I07["I07 knowledge"]
    I07 --> I08["I08 scheduling"]
    I08 --> I09["I09 distributed"]
    I03 --> I10["I10 operational TUI"]
    I09 --> I11["I11 release"]
    I10 --> I11
```

I10 may begin only after I03 stabilizes snapshot, SSE, and application-command contracts. It may
then progress alongside I04–I09. Every increment is independently runnable and accepted before
promotion; distributed execution does not weaken the local gate.

## 8. Complete SRS-to-increment ownership

Each requirement has exactly one primary accepting increment. Cross-cutting invariants still apply
to every affected gate.

| Increment | Primary SRS requirements |
|---|---|
| I00 | SYS-001–005, SYS-007, NFR-007 |
| I01 | PRJ-001–007, PLN-001–004, PLN-009–011, RUN-001–003, RUN-006, NFR-002, TC-001–003, TC-007 |
| I02 | PLN-005–008, SAF-001–013, TOL-001–027, FIL-001–007, EXE-001–003, TC-006 |
| I03 | SYS-006, RUN-004–005, RUN-007–012, EDT-001–008, EXE-004–008, ART-001–008, OBS-001–008, NFR-003–004, TC-009 |
| I04 | WEB-001–007, BRW-001–008, MCP-001–009, TC-008 |
| I05 | CTX-001–008, SKL-001–025, ENG-001–013 |
| I06 | PRJ-008–010, PLN-012–021, ORG-001–016, MSN-001–016 |
| I07 | KNW-001–006 |
| I08 | AUT-001–007 |
| I09 | DST-001–010 |
| I10 | NFR-006, TC-004 |
| I11 | NFR-001, NFR-005, NFR-008–010, TC-005 |

## 9. Gate decisions

1. D-032 accepts PRD 1.4 and SRS 1.6.
2. D-033 accepts Contract 1.4.
3. D-034 accepts Responsibility Map 1.2.
4. D-035 accepts System Model 1.2 and Architecture 1.2 and supersedes D-030.
5. D-036 accepts Implementation Plan 1.4 and explicitly authorizes resumption of I02.
6. D-037 accepts the Model 1.3, Architecture 1.3, and Implementation Plan 1.5 mission-environment
   detail amendment while preserving D-036's I02 authority and the later increment gates.

All six decisions were accepted in order on 2026-08-25. D-036 therefore resumes I02 only; it does
not accept I02 evidence in advance or authorize later increments outside their declared gates.
D-037 accepts the design/delivery clarification and does not suspend or broaden I02.
