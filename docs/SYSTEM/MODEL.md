# MISHKAN System Model

**Status:** Accepted by D-037; amended by D-051 on 2026-10-29
**Version:** 1.4
**Derived from:** PRD 1.5, SRS 1.7, System Contract 1.5, and Responsibility Map 1.3 accepted by
D-051

## 1. Purpose and modeling discipline

This document answers only the behavioral questions that constrain architecture. A participant
names an external actor or approved responsibility, not automatically a process, service, package,
database, or protocol. CrewAI is the sole production runtime for agents and teams; MISHKAN owns
deterministic state, policy, effects, evidence, and acceptance around it.

## 2. System context

**Question:** Who interacts with MISHKAN, and which authorities remain outside it?

```mermaid
flowchart LR
    CEO["Human CEO / engineer"]
    Operator["Operator"]
    Harness["Codex, Claude, or compatible harness"]
    Scheduler["External scheduler"]
    Worker["Approved remote worker"]
    subgraph M["MISHKAN system boundary"]
        App["Persistent organization, missions, conversations, and control plane"]
        CrewAI["CrewAI 1.x production coordination"]
    end
    Repo["Repositories and source-control hosts"]
    Models["Configured inference services"]
    Knowledge["Memory, knowledge, and structure sources"]
    MCP["Configured MCP peers"]
    Artifact["Artifact and blob stores"]
    Credentials["Credential resolver"]
    Isolation["Execution and browser isolation"]
    CEO <-->|"conversation, objectives, decisions, inspection"| App
    Operator -->|"configuration, policy, capacity"| App
    Harness <-->|"governed HTTP or MCP contract"| App
    Scheduler -->|"idempotent trigger"| App
    Worker <-->|"identity, lease, envelope, result"| App
    App --> CrewAI
    App <-->|"evidence and authorized effects"| Repo
    App <-->|"inference"| Models
    App <-->|"attributed retrieval"| Knowledge
    App <-->|"mediated discovery and calls"| MCP
    App <-->|"immutable content and references"| Artifact
    App -->|"late secret resolution"| Credentials
    App -->|"bounded effects"| Isolation
```

MISHKAN does not become the repository host, credential authority, model provider, artifact store,
isolation engine, or MCP peer. Availability and instructions from those systems confer no MISHKAN
authority.

## 3. Complete mission lifecycle

**Question:** Which durable states make mission progress and interruption unambiguous?

```mermaid
stateDiagram-v2
    [*] --> Proposed
    Proposed --> Clarifying: origin accepted
    Clarifying --> Planned: Mission Brief confirmed
    Clarifying --> Paused: executive disagreement
    Planned --> Active: plan authorized and crew ready
    Planned --> Paused: approval or dependency pending
    Active --> Blocked: required dependency or decision absent
    Active --> Paused: authorized intervention
    Active --> Evaluating: production results accepted
    Evaluating --> Remediating: evaluation rejects
    Remediating --> Active: corrected plan authorized
    Evaluating --> Completed: evaluation and report accepted
    Paused --> Planned: brief or plan changes
    Paused --> Active: resume authorized
    Blocked --> Active: limitation reconciled
    Blocked --> Failed: reconciliation proves terminal failure
    Remediating --> Failed: correction bound exhausted
    Proposed --> Cancelled: stop accepted
    Clarifying --> Cancelled: stop accepted
    Planned --> Cancelled: stop accepted
    Paused --> Cancelled: stop accepted
    Blocked --> Cancelled: stop accepted
    Active --> Cancelling: stop accepted
    Evaluating --> Cancelling: stop accepted
    Remediating --> Cancelling: stop accepted
    Cancelling --> Cancelled: effects settle or reconcile
    Active --> Failed: terminal failure
    Evaluating --> Failed: terminal acceptance failure
    Completed --> [*]
    Failed --> [*]
    Cancelled --> [*]
```

Every transition records actor or cause, reason, command or decision, organization and Mission Brief
versions, and evidence. Cancellation releases no new task; uncertain effects settle or reconcile
before the terminal record claims completion.

## 4. Mission Brief and PM/CTO agreement

**Question:** What becomes durable before mission planning may begin?

```mermaid
sequenceDiagram
    actor CEO
    participant M as Mission governance (RSP-022)
    participant C as CrewAI coordination (RSP-008)
    participant PM as PM agent
    participant CTO as CTO agent
    participant O as Organization definitions (RSP-007)
    participant V as Evidence and events (RSP-015)
    CEO->>M: free-form problem, objective, or strategic constraint
    O-->>M: exact organization version and eligible persistent identities
    M->>C: bounded clarification envelope for PM and CTO
    C->>PM: clarify value, scope, priority, and functional acceptance
    C->>CTO: clarify feasibility, risks, technical and assurance coverage
    PM-->>C: product candidate and confirmation or cited disagreement
    CTO-->>C: technical candidate and confirmation or cited disagreement
    C-->>M: candidate Mission Brief plus CrewAI lineage
    alt agreement within authority
        M->>V: persist Mission Brief, confirmations, proposed crew, and event
        V-->>M: mission becomes planned
    else unresolved or outside authority
        M->>V: persist scoped pause and actionable escalation
        V-->>CEO: options, consequences, recommendations, and required decision
    end
```

An optional template may contribute guidance and provenance before confirmation. It never supplies
implicit authority, a fixed crew, or a mandatory task graph.

PM and CTO are agents executed through CrewAI. Mission governance validates and persists their
candidate Brief and confirmations; it does not synthesize their product or technical reasoning.

## 5. Disagreement, partial pause, and CEO escalation

**Question:** How does disagreement avoid freezing unrelated work or becoming an implicit decision?

```mermaid
flowchart TD
    D["PM/CTO disagreement recorded"] --> Scope["Compute dependent disputed scope"]
    Scope --> Pause["Pause only dependent tasks and decisions"]
    Scope --> Continue["Continue independent eligible work"]
    Pause --> Resolve{"Can PM and CTO resolve within authority?"}
    Resolve -->|"yes"| Decision["Durable executive decision"]
    Resolve -->|"no"| Escalation["Actionable CEO escalation"]
    Escalation --> CEO["CEO command: decide, accept risk, change scope, or stop"]
    CEO --> Decision
    Decision --> Replan["Revalidate Brief, crew, plan, and policy"]
```

A conversation message or recommendation is not the decision. Only an authorized command changes
mission state.

## 6. Contextual Mission Crew composition

**Question:** How are permanent professional identities composed without a static team matrix?

```mermaid
sequenceDiagram
    participant M as Mission governance (RSP-022)
    participant O as Organization profiles (RSP-007)
    participant E as Professional evidence (RSP-026)
    participant P as Planning (RSP-005)
    participant PM
    participant CTO
    M->>O: Mission Brief and organization version
    O-->>M: 59 identities, branches, pools, authority, independence
    M->>E: request scoped competence, availability, conflict, and freshness evidence
    E-->>M: attributable profile evidence and unknowns
    M->>P: eligible identities plus risk and assurance needs
    P-->>M: proposed owners, contributors, evaluators, reporter, and Mission Lead
    CTO->>M: confirm technical, security, and assurance coverage
    PM->>M: confirm formal composition
    M->>M: fingerprint and persist crew revision
```

The Mission Lead is a temporary responsibility assigned to one of the 59 identities. Tools and
skills are selected later for exact tasks; professional identity never implies ambient capability.

### 6.1 Smallest sufficient crew, health, and adaptation

**Question:** How does a Mission Crew improve without a hidden score or enlarged authority?

```mermaid
sequenceDiagram
    participant C as CrewAI coordination (RSP-008)
    participant M as Mission governance (RSP-022)
    participant Q as Independent evaluation (RSP-009)
    participant L as Professional learning (RSP-026)
    participant PM
    participant CTO
    C->>M: attributable composition proposal, alternatives, collaboration rationale
    M->>M: deterministic competence, availability, authority, conflict, independence gates
    PM->>M: confirm product fit and formal composition
    CTO->>M: confirm technical, security, quality, and assurance coverage
    M->>M: persist smallest sufficient shape and collaboration-plan revision
    loop bounded deterministic collection
        M->>M: record flow, handoff, drift, duplication, risk, workload, and cost signals
    end
    M->>Q: milestone interval, evidence cursor, dimensions, and immutable references
    Q-->>M: healthy, attention, blocked, or uncertain with confidence and limitations
    alt correction stays within accepted membership and authority
        M->>M: persist bounded adaptation and affected task changes
    else material boundary changes
        M->>PM: request composition or scope decision
        M->>CTO: request independence, authority, or risk decision
    end
    M->>L: retrospective proposals and explicitly non-promoted findings
    L-->>M: governed learning lifecycle outcome; no automatic profile change
```

A `single_owner` shape is valid and preferred when collaboration has no justified value. A
`consultative` or `multi_producer` shape requires explicit benefit from expertise, independence,
parallelism, context separation, or assurance. Collaboration history is a cited projection from
accepted missions; it can inform the proposal but current mission fit can always displace it.
Uncertainty, dissent, failure, and requests for help remain first-class evidence and do not model
agent emotions.

## 7. Production, independent evaluation, reporting, and acceptance

**Question:** How does CrewAI coordinate work without owning deterministic acceptance?

```mermaid
sequenceDiagram
    participant C as CrewAI coordination (RSP-008)
    participant P as Producer agent
    participant A as Result acceptance (RSP-010)
    participant Q as Independent assurance (RSP-009)
    participant E as Independent evaluator
    participant R as Reporter
    participant V as Evidence and events (RSP-015)
    C->>P: execute authorized production task
    P-->>C: structured candidate result and artifact references
    C->>A: submit candidate with execution lineage
    A->>V: persist valid candidate acceptance and event
    V-->>C: release downstream evaluation task
    C->>E: execute independent evaluation
    E-->>Q: evaluation result and evidence
    Q->>A: submit separation-checked evaluation
    alt evaluation rejects
        A->>V: persist rejection and correction eligibility
        V-->>C: release authorized remediation or replanning
    else evaluation accepts
        A->>V: persist evaluation acceptance
        V-->>C: release separate reporting task
        C->>R: produce report from accepted evidence
        R-->>A: structured report
        A->>V: persist report and mission completion if all conditions hold
    end
```

CrewAI completion is always a candidate result. Production, evaluation, reporting, and evidence
audit remain separately attributable.

## 8. Common governed capability invocation

**Question:** How do native tools, skills, engines, Web, Browser, artifacts, and MCP share one
authority boundary without becoming one static taxonomy?

```mermaid
sequenceDiagram
    participant C as CrewAI task
    participant R as Registry resolution (RSP-021/025)
    participant G as Effect gateway (RSP-011)
    participant P as Policy decision (RSP-006)
    participant X as Selected capability adapter
    participant S as Security evidence (RSP-012)
    participant V as Evidence and events (RSP-015)
    C->>R: exact task intent and observed context
    R-->>C: concrete available binding or truthful unavailable state
    C->>G: typed call with actual targets and declared effects
    G->>G: validate binding, schema, scopes, limits, and session ownership
    G->>P: decide exact normalized request
    alt deny or approval absent
        P-->>G: stable refusal
        G->>V: non-secret refusal evidence
        G-->>C: classified failure
    else allow
        P-->>G: exact grant
        G->>X: dispatch outside state transaction
        X-->>G: completed, failed, cancelled, or uncertain
        G->>S: inspect output and effect evidence
        S-->>G: validated, contained, or rejected
        G->>V: terminal attributable record and degradation if any
        G-->>C: validated bounded result or artifact reference
    end
```

Discovery, installation, health, instructions, and credentials never grant authority. Project
commands remain process/Bash inputs, not invented tool identities.

### 8.1 Mission execution-environment decision and generation

**Question:** How does a mission reuse or generate a Dev Container, Podman, or other reproducible
environment without making containerization universal or bypassing ordinary effects?

```mermaid
sequenceDiagram
    participant M as Mission governance (RSP-022)
    participant P as Planning (RSP-005)
    participant X as Context evidence (RSP-004)
    participant E as Environment resolution (RSP-025)
    participant C as CrewAI coordination (RSP-008)
    participant O as Accountable Mission Crew agent
    participant A as Artifacts (RSP-023)
    participant G as Effect gateway (RSP-011)
    participant V as Evidence and events (RSP-015)
    M->>P: accepted Mission Brief and environment intent
    P->>E: request observed candidates for affected contexts and locations
    E->>X: inspect repository, greenfield, machine, worker, and existing definitions
    X-->>E: attributed observations, base revisions, compatibility facts, unknowns
    E-->>P: eligible engines, formats, locations, constraints, and unknowns
    P->>C: bounded environment-planning task with accountable owner
    C->>O: Mission Brief, evidence, alternatives, and required result contract
    O-->>C: proposed outcome, rationale, descriptor semantics, effects, and verification
    C-->>P: candidate MissionEnvironmentPlan plus CrewAI lineage
    P->>P: validate owner, evidence, alternatives, dependencies, and plan contract
    P->>E: resolve requested outcome and bounded descriptor constraints
    alt compatible binding
        E-->>P: exact adapter/location binding and compatibility evidence
        P->>V: persist authorized plan revision and binding
        V-->>C: release accountable generation or verification task
        C->>O: execute accepted environment task
        O->>G: typed edit/build/start/probe/cleanup operations
        G->>A: commit descriptor, logs, and results as artifacts
        A-->>G: immutable artifact identities
        G-->>O: applied/refused effects and settled verification evidence
        O-->>C: candidate task result
        C-->>V: result lineage for normal evaluation and acceptance
    else incompatible or unresolved
        E-->>P: precise incompatibility; no alternative selected silently
        P->>V: record degradation and replan or block only dependent scope
    end
```

One mission can own several environment bindings when repositories, services, platforms, or
workers differ. The agent-authored `MissionEnvironmentPlan` is plan content owned by RSP-005. The
resolved binding is the smallest context-specific unit owned by RSP-025 and records source
evidence, target location, eligible adapter and engine versions, policy lineage, verification
result, and affected plan tasks. Availability informs the proposal but cannot choose it.

`Dev Container` means a descriptor conforming to the selected Development Container
specification and may refer to an image, build input, or a supported multi-container definition.
For Podman, the resolver uses only forms supported by the verified target adapter: ordinarily a
Containerfile or Dockerfile for image construction, and Podman-supported Kubernetes YAML or
Quadlet only when the mission requires those runtime or service semantics. A Compose document is
selected only when the actual Compose-compatible adapter is verified. These are engine inputs, not
MISHKAN runtimes.

Generation settles first as immutable artifacts or a typed change set. Project persistence is a
separate Edit/Patch effect; build, start, readiness, project-command verification, and cleanup are
separate Terminal/Process or specialized-adapter effects executed by accountable CrewAI tasks. An
environment description therefore cannot mark itself ready, and generated project changes follow
the ordinary independent-evaluation path. A context or base-revision change creates a new plan and
binding revision and invalidates only its dependent task bindings.

### 8.2 Environment decision and binding lifecycle

**Question:** Which states prevent a proposal, generated file, running container, and verified
mission dependency from being confused with one another?

```mermaid
stateDiagram-v2
    [*] --> Observing
    Observing --> AwaitingProposal: eligible evidence and unknowns published
    Observing --> Unresolved: required observation unavailable
    AwaitingProposal --> Proposed: CrewAI task returns owned MissionEnvironmentPlan
    Proposed --> Replanning: proposal invalid, incomplete, or consequentially unaccepted
    Proposed --> Resolving: normalized plan revision accepted
    Resolving --> Incompatible: requested outcome cannot be preserved
    Resolving --> BoundUnverified: compatible adapter and location bound
    Incompatible --> Replanning: precise incompatibility returned
    Replanning --> AwaitingProposal: revised evidence or mission decision requested
    BoundUnverified --> Generating: descriptor generation required
    BoundUnverified --> Verifying: existing or host-native binding requires verification
    Generating --> AwaitingMutation: immutable descriptor set accepted
    Generating --> Failed: generation result rejected
    AwaitingMutation --> Verifying: project change applied or artifact-only input selected
    AwaitingMutation --> Replanning: base revision or authority changed
    Verifying --> Verified: location-bound checks settle successfully
    Verifying --> Failed: checks settle unsuccessfully
    Verifying --> Cancelled: cancellation settles without uncertain effect
    Verifying --> Uncertain: build, start, probe, or cleanup cannot be settled
    Verified --> Invalidated: context fingerprint or required evidence changes
    Uncertain --> Verifying: reconciliation proves a safe continuation
    Uncertain --> Replanning: reconciliation requires a different decision
    Invalidated --> AwaitingProposal: affected scope is replanned
    Unresolved --> AwaitingProposal: missing dependency becomes observable
```

`Observed`, `available`, `compatible`, `authorized`, `materialized`, and `verified` remain separate
facts even when one command observes several of them. `BoundUnverified` releases only tasks that do
not require a runnable environment. `Verified` releases exactly the dependent task set recorded in
the accepted plan. `Failed`, `Cancelled`, `Uncertain`, `Incompatible`, and `Unresolved` preserve
their evidence and do not collapse into one generic unavailable state.

Generation is not an authority shortcut. `Generating` produces candidate descriptor artifacts;
`AwaitingMutation` distinguishes an artifact-only environment input from a proposed project change.
Only the latter crosses Edit/Patch with an exact base revision. A successful build without cleanup
settlement may remain `Uncertain`, and a successful descriptor parse cannot skip `Verifying` when
dependent tasks require runtime evidence.

### 8.3 Mission environment records and cardinality

**Question:** What is versioned once per mission, once per context, or once per effect attempt?

```mermaid
classDiagram
    class MissionBrief {
      +mission_id
      +brief_revision
      +environment_intent
      +known_constraints
      +required_evidence
    }
    class MissionEnvironmentPlan {
      +plan_revision
      +owner_identity
      +crewai_lineage
      +requested_outcomes
      +rationale_and_unknowns
    }
    class EnvironmentContext {
      +context_id
      +repository_or_workspace
      +service_group
      +execution_location
      +platform_architecture
      +context_fingerprint
    }
    class EnvironmentBinding {
      +binding_id
      +binding_revision
      +requested_outcome
      +adapter_and_engine
      +compatibility_state
      +dependent_tasks
    }
    class EnvironmentDescriptorSet {
      +descriptor_set_id
      +format_members
      +base_evidence
      +artifact_ids
      +change_set_id
    }
    class EnvironmentAttempt {
      +attempt_id
      +operation
      +effect_call_ids
      +settlement
      +artifact_ids
    }
    class EnvironmentVerification {
      +verification_id
      +location_fingerprint
      +checks_and_coverage
      +settlement
      +limitations
    }
    MissionBrief "1" --> "1..*" MissionEnvironmentPlan : revised through planning
    MissionEnvironmentPlan "1" --> "1..*" EnvironmentContext : decides per material context
    EnvironmentContext "1" --> "1..*" EnvironmentBinding : versioned resolution
    EnvironmentBinding "1" --> "0..*" EnvironmentDescriptorSet : generates or reuses
    EnvironmentBinding "1" --> "0..*" EnvironmentAttempt : materializes and cleans
    EnvironmentBinding "1" --> "0..*" EnvironmentVerification : verifies at locations
```

A `MissionEnvironmentPlan` can cover several contexts but must state one requested outcome per
material context. A binding never spans incompatible repository revisions, platforms,
architectures, service groups, or execution locations. A descriptor set is a typed collection: it
may contain `.devcontainer/devcontainer.json`, referenced Dockerfile or Containerfile inputs,
Compose inputs, Podman Kubernetes YAML, Quadlet units, Nix or toolchain files only when the accepted
semantics require them. It does not require all formats and does not make any filename universal.

Each descriptor member records its specification or engine dialect, logical project path when one
is proposed, immutable artifact identity, input/base evidence, sensitivity, and derivation. A
single binding can have several materialization attempts and verifications; only a verification
whose location and context fingerprints still match can satisfy dependent tasks. Reuse of an
existing project definition creates a descriptor-set reference to observed project evidence rather
than copying or regenerating its bytes.

## 9. PTY, job, browser, and MCP session lifecycle

**Question:** How are long-lived state and uncertain effects contained?

```mermaid
stateDiagram-v2
    [*] --> Requested
    Requested --> Starting: binding, policy, and limits valid
    Requested --> Refused: invalid or unauthorized
    Starting --> Ready: readiness evidence observed
    Starting --> Failed: start failure settles
    Ready --> Active: command, action, or call accepted
    Active --> Ready: operation settles without closing session
    Active --> Cancelling: cancellation requested
    Active --> Uncertain: contact lost after possible effect
    Ready --> Closing: close requested or deadline reached
    Cancelling --> Closing: effect settlement observed
    Cancelling --> Uncertain: settlement cannot be established
    Uncertain --> Ready: reconciliation proves session reusable
    Uncertain --> Closing: reconciliation requires termination
    Closing --> Closed: resources and effects settled
    Refused --> [*]
    Failed --> [*]
    Closed --> [*]
```

Each session has an owner, execution location, scope, cursors, deadlines, resource bounds,
credential references, and lifecycle evidence. Browser authenticated state and MCP/terminal
sessions are never shared implicitly.

## 10. Immutable artifacts and concurrent working references

**Question:** How can large results be durable and collaborative without silent overwrite?

```mermaid
sequenceDiagram
    participant P as Producer
    participant A as Artifact governance (RSP-023)
    participant B as Content-addressed store
    participant M as Metadata transaction
    participant C as Concurrent writer
    P->>A: stream content plus media and provenance contract
    A->>B: write and verify immutable content
    B-->>A: content identity and size
    A->>M: persist manifest and validation state
    P->>A: advance working reference, expected revision r1
    C->>A: advance same reference, expected revision r1
    A->>M: compare r1 and atomically set r2
    M-->>A: success
    A->>M: compare stale r1 with current r2
    M-->>A: conflict; current r2 retained
    A-->>C: deterministic conflict with both immutable revisions
```

Storage success does not equal task acceptance. Missing or corrupt content enters recovery state;
retention and garbage collection preserve protected manifests and evidence.

## 11. Skill and professional-competence evolution

**Question:** How can Hermes-like live improvement remain fluid, reversible, and evidence-based?

```mermaid
stateDiagram-v2
    [*] --> Observed: teaching, miss, correction, or evaluated outcome
    Observed --> Candidate: attributable skill/profile change created
    Candidate --> Rejected: invalid, incompatible, or denied
    Candidate --> Quarantined: configured inspection or trust threshold
    Candidate --> Active: policy allows immediate validated native mutation
    Candidate --> Staged: policy requires later review or approval
    Staged --> Active: required decision accepted
    Quarantined --> Candidate: finding resolved
    Active --> Superseded: newer version activated
    Superseded --> Candidate: restoration requested with prior version lineage
```

Activation policy depends on source, trust, scope, effect, and risk; staging is not universal.
Community discovery remains a candidate. Agent-profile promotion additionally requires scoped
attributable evaluation and cannot alter authority, independence, identity, or failure history.

## 12. External harness request through MISHKAN and CrewAI

**Question:** Where does a machine client request become governed organizational work?

```mermaid
sequenceDiagram
    participant H as External harness
    participant I as HTTP or MCP facade (RSP-024)
    participant M as Mission governance (RSP-022)
    participant P as Planning and policy (RSP-005/006)
    participant C as CrewAI coordination (RSP-008)
    participant V as Evidence and events (RSP-015)
    H->>I: authenticated versioned objective, query, or intervention
    I->>I: validate identity, schema, scope, and transport session
    I->>M: translate to application command
    M->>P: Mission Brief, direct request, or authorized intervention context
    P->>V: persist decision and accepted command
    alt governed work accepted
        V-->>C: authorized CrewAI execution envelope
        C-->>V: candidate results and runtime lineage
        V-->>I: durable state, artifacts, and resumable events
        I-->>H: structured result or cursor
    else refused
        V-->>I: stable refusal evidence
        I-->>H: typed refusal
    end
```

The facade does not expose a runtime selector or a second agent coordinator.

## 13. Resume, scheduling, and worker lease

**Question:** How does asynchronous and distributed execution preserve acceptance semantics?

```mermaid
sequenceDiagram
    participant S as Schedule governance (RSP-016)
    participant V as State and outbox (RSP-015)
    participant C as CrewAI coordination (RSP-008)
    participant D as Worker coordination (RSP-017)
    participant W1 as Worker A
    participant W2 as Worker B
    participant A as Result acceptance (RSP-010)
    S->>V: atomically deduplicate occurrence and create run
    V-->>C: durable eligible task
    C->>D: request eligible remote placement
    D-->>W1: immutable envelope and bounded lease
    Note over W1,D: heartbeat stops; lease expires
    D-->>W2: new attempt envelope and lease
    W2->>A: valid completion
    A->>V: atomically accept first valid completion and release dependencies
    W1->>A: late completion
    A->>V: record stale or duplicate delivery; accepted result unchanged
    Note over C,V: after restart, resume from earliest required result not accepted
```

Scheduling and placement create new delivery and recovery states, never new policy or acceptance
semantics.

## 14. Behavioral conclusions constraining structure

1. One authoritative application domain owns missions, conversations, commands, policy, evidence,
   artifacts, and acceptance for every client.
2. CrewAI remains directly integrated as the only production agent/team runtime.
3. State changes and outbox facts use short consistency boundaries; model execution and external
   effects occur outside them.
4. Mission governance, planning, authorization, effect enforcement, acceptance, artifact storage,
   and projections remain distinct ownership boundaries.
5. Sessions, artifacts, worker leases, attempts, and results have separate identities and
   lifecycles.
6. Optional templates, skills, packs, tools, and engines are contextually resolved inputs, not
   authority or static workflows.
7. Read models and live streams may lag; commands validate against authoritative current state.
8. The accepted structural direction remains a transactional modular monolith with an outbox, not
   full event sourcing or service-first decomposition.
9. Collaboration plans, health evaluations, adaptation decisions, and retrospective metadata are
   authoritative mission records; collaboration history is only a derived projection.
10. Deterministic health signals do not synchronously invoke a model on task completion. Independent
    interpretation occurs at milestones and settles as uncertain when evaluation fails.

These conclusions supersede the version 1.1 behavior amendment and the version 1.2 model detail
under D-035. D-037 accepts the version 1.3 mission-environment detail. D-051 accepts the version 1.4
high-performance Mission Crew amendment. Implementation authority is governed separately by D-036
and the accepted increment gates.
