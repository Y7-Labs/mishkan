# MISHKAN System Contract and Invariants

**Status:** Accepted by D-033; amended by D-051 on 2026-10-29
**Version:** 1.5
**Sequence:** SWE-BASICS-BEFORE-CODE 03
**Derived from:** PRD 1.5 and SRS 1.7 accepted by D-051

## 1. Contract boundary

MISHKAN accepts engineering objectives and bound execution-context evidence, coordinates the bound
versioned organization through CrewAI, and returns validated engineering results with durable
evidence. The contract applies equally to interactive, headless, scheduled, and—when
enabled—distributed work.

The contract says what remains true regardless of storage, transport, process topology, provider,
or user interface. It does not assign the guarantees to components; that occurs in Sequence 04.

## 2. Operational policy values are not invariants

MISHKAN has two deliberately different kinds of rules:

| Kind | Meaning | Change mechanism |
|---|---|---|
| Integrity invariant | A law required for MISHKAN to be trustworthy | A new approved system-contract version |
| Operational policy | A visible decision about which actor may perform which capability in which scope | A versioned policy change by its declared authority |

Commit, push, merge, deployment, release, migration, secret rotation, network access, and future
stateful capabilities are operational capabilities. They are not universally forbidden and are not
encoded in a private deny-list. Policy may allow, approval-gate, or deny them for precise contexts.

The integrity law is that no capability executes without a valid decision and no actor can expand
its own authority.

## 3. Actors and interactions

| Actor | Accepted interaction | Contractual result |
|---|---|---|
| Human CEO / engineer | Converse with PM and CTO; submit objectives; inspect or intervene in work; manage authorized policy | A durable message, command, decision, plan, result, or stable refusal |
| Engineering lead | Inspect organizational evidence and outcomes | A consistent read model backed by durable evidence |
| Operator | Configure resources, schedules, retention, identity, and capacity | A versioned effective configuration or an atomic refusal |
| External harness | Submit or inspect governed work through a versioned application contract | The same authorized command, result, event, or refusal available to other clients |
| External scheduler | Submit an idempotent trigger | At most one accepted run for one trigger occurrence |
| Remote worker | Enroll, advertise capability, claim a task, renew a lease, return a result | A bounded identity and task authority or a stable refusal |
| External service | Supply inference or optional context | An attributed result, a retryable failure, or declared degraded operation |

An organizational agent is a system-controlled identity, not an independent source of authority.
Its role, tools, scopes, and delegation rights come from the organization, plan, and policy records.

## 4. Accepted input classes

The system contract recognizes these input classes:

1. effective configuration and its provenance;
2. repository identity and base revision, or prospective-workspace identity and discovery revision,
   plus discovery evidence and declared project policy;
3. objective, mission brief, optional template, constraints, and completion conditions;
4. organization, professional profile, crew, conversation, plan, task, output, report, event,
   policy, tool, toolset, engine, pack, and registry definitions;
5. policy authorization or interactive approval decisions;
6. task results, artifacts, diffs, external state references, and evaluations;
7. schedule definitions and idempotent trigger occurrences;
8. knowledge, structural, episodic, literal, skill, skill-provenance, tool-registry, tool-call, and
   lifecycle records;
9. worker identity, capability, lease, heartbeat, and completion records;
10. cancellation, retry, resume, retention, hold, and revocation requests;
11. messages, commands, decisions, escalations, interventions, artifact manifests, immutable
    revisions, working-reference comparisons, and external-client identities.

An input is accepted only after its identity, schema version, provenance, and context-specific
preconditions are validated. Refused input does not partially mutate accepted state.

## 5. Guaranteed output classes

For an accepted interaction, MISHKAN produces one or more of:

- a versioned Mission Brief, contextual Mission Crew, and execution-context-specific plan history;
- an authorization decision with matched policy evidence;
- a typed run or task state transition;
- a validated task result, independent evaluation, or structured report;
- an attributable workspace or external state change with review evidence;
- a versioned event, snapshot, security decision, or degraded-mode notice;
- a durable conversation, intervention, artifact, working-reference, schedule, worker, knowledge,
  skill, professional-profile, engine, tool-registry, tool-call, or retention record;
- a stable machine-readable refusal or failure.

The system never represents a rejected, incomplete, unpersisted, or incompatible result as
accepted or complete.

## 6. System promises

### CTR-001 — Explicit operating context

Every run is evaluated using identifiable configuration, organization, bound repository/base
revision or prospective-workspace/discovery revision, plan version, and policy version records.
Their provenance is inspectable without disclosing credentials.

### CTR-002 — Execution-context-specific coordination

The same objective may produce a different task graph and role participation for different
repository or prospective-workspace evidence. MISHKAN does not substitute a universal hardcoded
task chain for planning.

### CTR-003 — Policy-governed autonomy

Routine work may proceed without interaction when policy allows it. Work requiring approval pauses;
denied work is refused. The decision is made before the effect and is durable and attributable.

### CTR-004 — Auditable plan evolution

A running plan may adapt to validated evidence. Semantic change creates a new version and
fingerprint, preserves compatible accepted work, records the reason and difference, and obtains a
new policy decision before introduced work executes.

### CTR-005 — Validated acceptance

A task result becomes accepted only after envelope, output-contract, role-separation, revision,
authorization, and attempt checks pass.

### CTR-006 — Durable progress

Accepted results survive supported interruption. Dependent work is released only from durable
accepted state, and resumption starts from the earliest still-required task without an accepted
result.

### CTR-007 — Inspectable effects

Every state-changing operation produces evidence connecting intent, acting identity, capability,
scope, authorization, result, and relevant diff or external state reference.

### CTR-008 — Independent evaluation

Workspace-changing production artifacts receive evaluation by a different identity before the run
can succeed. The identity making routing or acceptance decisions does not report that same work.

### CTR-009 — Observable operation

State transitions and security-relevant decisions produce versioned events. A consumer can rebuild
current run state from durable evidence and detect a gap in live delivery.

### CTR-010 — Safe degraded usefulness

Loss of an optional context source does not fail otherwise safe work. MISHKAN identifies the absent
source and limitation. Loss of a required coordination or inference dependency blocks only affected
work with a retryable failure.

### CTR-011 — Interface equivalence

Interactive and programmatic paths apply the same validation, authorization, and evidence rules.
Headless execution does not receive hidden additional authority.

### CTR-012 — Distributed preservation

When distributed mode is enabled, worker loss, retry, or duplicate delivery does not change task
acceptance semantics. A remote worker receives only bounded coordinator-issued authority.

### CTR-013 — Inspectable skill learning loop

MISHKAN can discover and progressively load portable procedural skills, record whether their use
succeeded, learn attributable improvements from teaching or reviewed experience, and activate or
restore versions through visible policy and provenance. A skill never grants authority or replaces
the execution-context-specific plan, CrewAI coordination, or deterministic effect enforcement.

### CTR-014 — Controlled tool extension and invocation

MISHKAN can discover, inspect, compose, and lifecycle-manage typed atomic tools from configured
sources without silently expanding authority. CrewAI agents receive only exact tools bound to the
accepted plan, and every call crosses validation and policy enforcement before producing an
attributable validated result or stable refusal. General-purpose tools remain general: their
concrete command, path, destination, and effect inputs are constrained at that same boundary rather
than being re-described as a new tool for every operation.

### CTR-015 — Governed adaptive missions

PM and CTO turn a free-form objective into a versioned Mission Brief and compose a temporary crew
from the persistent 59-identity organization through CrewAI-governed organizational turns. Mission
Crew agents author candidate plans and technical decisions; deterministic MISHKAN responsibilities
validate, normalize, authorize, persist, resolve capabilities, and accept or reject them. Optional
templates provide guidance only; neither a template nor a discovered capability fixes the task
graph, crew, authority, engineering outcome, or acceptance path.

### CTR-016 — Durable executive communication and intervention

The CEO can recover conversations, distinguish messages from commands and decisions, inspect
mission evidence, and issue authorized interventions through any supported client. Disagreement
pauses only affected work and produces an actionable escalation when PM and CTO cannot resolve it.

### CTR-017 — Immutable artifact integrity

Large or rich results are stored as attributable immutable artifact revisions. Mutable working
references advance only through an expected-current comparison, and storage interruption or
concurrent update produces visible recovery or conflict state rather than silent overwrite.

### CTR-018 — Evidence-based professional evolution

Agent knowledge, skill association, and demonstrated competence evolve only from attributable,
scoped evidence. A learning operation cannot change authority, independence, or accepted history,
and broader promotion preserves contradictory and failure evidence.

### CTR-019 — Governed harness and MCP mediation

HTTP, MCP, CLI, SDK, chat, TUI, and scheduled clients address the same application commands and
state. An external harness or MCP peer cannot invoke raw agents, bypass CrewAI, hold authoritative
state, inherit a server's authority, or dispatch an effect outside policy.

### CTR-020 — Truthful contextual engineering capability

Native capabilities, skills, adapters, engines, environments, and packs are selected from observed
project and execution-location facts. Inventory, installation, health, eligibility, and authority
remain distinct, and only compatible fallbacks may run with an explicit degradation record. Every
environment-dependent mission carries a versioned decision that either binds verified existing
state, selects authorized native execution, produces governed reproducible descriptors, proposes a
project change, or identifies the unresolved dependency without fabricating readiness.

### CTR-021 — Evidence-based Mission Crew effectiveness

MISHKAN forms the smallest sufficient production shape, records an explicit collaboration plan,
preserves epistemically safe reporting, derives bounded deterministic health signals, and uses
independent milestone evaluation and governed retrospectives. Collaboration history is cited and
contextual rather than authoritative. Bounded adaptations cannot enlarge crew, independence,
scope, authority, or accepted risk without the applicable PM/CTO or CEO decision.

## 7. Integrity invariants

These laws are not user-configurable operational policy.

### INV-001 — Identifiable state

Every persisted domain record has a globally unique UUID identity, schema version, and unambiguous
UTC creation time. A record that changes meaning creates a new version or transition rather than
silently replacing history.

### INV-002 — Decision before effect

No capability crosses its enforcement boundary before a valid allow decision or required approval
exists for the exact acting identity, capability, target, and scope.

### INV-003 — No self-authorization

No agent, worker, task, tool, or external service may grant itself capabilities, enlarge its scopes,
approve its own plan, or alter the policy used to authorize its current action.

### INV-004 — Public policy semantics

Every operational restriction or grant is expressed through the inspectable versioned policy
model. Product policy is not implemented as an undisclosed hardcoded action list. No matching allow
or approval rule means no authority to act.

### INV-005 — Deterministic policy decision

For identical normalized policy, request, identity, and context, authorization produces the same
decision. A conflict at equal precedence resolves to deny with an explicit conflict error; it never
selects a rule nondeterministically.

### INV-006 — Policy decision lineage

Every authorization identifies the policy version and matched rule. Policy activation, retirement,
and revocation are versioned events with explicit effective scope; they never rewrite old evidence.

### INV-007 — Plan identity and lineage

Every semantic plan revision has a different fingerprint, a predecessor except for the first
version, a reason, a difference record, and an authorization decision. A resume never substitutes
an unrecorded plan.

### INV-008 — Repository and greenfield lineage

Every run has one execution context: one repository and immutable base revision, or one versioned
prospective workspace before repository establishment. Any established repository and revision
produced by authorized work identifies its predecessor context, responsible task, and run. Context
or revision mismatch blocks affected execution or acceptance until reconciled.

### INV-009 — Dependency eligibility

A task becomes eligible only when all declared dependencies have durable accepted results. A
barrier opens only when all required branches are durably accepted.

### INV-010 — Bounded iteration

Every cycle has a measurable exit condition and finite approved bound. Retry or replanning never
creates an unbounded execution path.

### INV-011 — Valid result before acceptance

No task result is accepted unless its result envelope and task-specific contract validate and its
producer is permitted by the active plan and role-separation rules.

### INV-012 — Durable acceptance before release

Acceptance, its result, and required evidence are durable before dependent work becomes eligible or
the accepted state is exposed as complete.

### INV-013 — Single accepted completion

At most one completion is accepted for a task attempt. Duplicate delivery may add evidence but can
never replace the accepted result or repeat its downstream effects.

### INV-014 — Role separation

The producer of an artifact cannot evaluate it for acceptance. The identity deciding routing or
acceptance for a task cannot report that same task.

### INV-015 — Resolved-scope enforcement

Filesystem authority applies to the fully resolved target, including symbolic links. Network,
repository, remote, branch, environment, credential, and resource authority applies to the actual
effect target, not merely user-supplied text.

### INV-016 — Secret containment

Resolved credential values never cross into configuration output, unrelated prompts, persisted
results, artifacts, events, logs, snapshots, reports, or diffs. A blocked secret is represented by
non-secret evidence.

### INV-017 — Deterministic enforcement boundary

Integrity and policy enforcement occurs at a deterministic system boundary before the effect. A
prompt instruction or an AI actor's assertion is never enforcement evidence.

### INV-018 — Cancellation monotonicity

After cancellation is accepted, no new task becomes eligible. Already accepted results remain
durable, executing work receives a cancellation request, and the run reaches a terminal audited
state.

### INV-019 — Event reconstruction

Every material state transition has a versioned typed event with enough identity and ordering data
to reconstruct state within a run. Transport loss may delay delivery but cannot erase the durable
transition silently.

### INV-020 — Schema compatibility

Persisted contracts declare their schema version. Unsupported versions cause explicit refusal and
are never mutated automatically.

### INV-021 — Knowledge provenance

Knowledge used as evidence identifies source, scope, retrieval time, bound execution-context
revision, applicable repository revision, and staleness. Knowledge does not become cross-project
guidance without a recorded policy decision and provenance.

### INV-022 — Schedule uniqueness

One trigger occurrence creates at most one accepted run. Concurrent runs with the same resolved
overlap key occur only under an explicit matching allow decision; the key never requires a fixed
outcome catalogue.

### INV-023 — Remote envelope and lease integrity

A remote assignment is immutable after issuance and bound to worker identity, task attempt,
execution-context revision, applicable repository revision, plan and policy fingerprints,
capabilities, resources, deadline, and lease. Invalid or expired authority cannot produce an
accepted completion.

### INV-024 — Progressive skill disclosure

Skill discovery exposes catalogue metadata without loading instructions. Selection loads the
complete identified `SKILL.md`; supporting content loads only when referenced and needed. Every
loaded layer remains attributable to the consuming task and exact content fingerprint.

### INV-025 — Skill provenance and trust

No acquired or changed skill becomes active without a durable source and content lock, completed
inspection, compatibility result, trust state, and policy decision. Source precedence, inspection
policy, and quarantine thresholds are public versioned configuration, not private hardcoded lists.

### INV-026 — Atomic and recoverable skill evolution

A skill mutation that policy stages remains durable until resolved; policy-authorized immediate
mutation does not require that intermediate state. Activation still changes to one complete
validated version atomically. Failure preserves the prior active version. Supersession, rejection,
archival, or deletion never erases accepted lineage, and eligible prior versions can re-enter the
same validation and policy path for restoration.

### INV-027 — Immutable tool resolution

Every accepted task binds exact tool identities, versions, schemas, source fingerprints, and
registry snapshot. Shorthand and toolsets are fully resolved before acceptance. Later discovery,
enablement, update, collision, or drift never changes that binding silently.

### INV-028 — Validated authorized dispatch

No tool is dispatched until its call envelope and input validate, the exact tool is bound to the
task and role, required runtime constraints are enforceable, and policy allows or has approved the
actual capability, targets, and scopes.

### INV-029 — Secret-safe attributable result

Credential values are resolved only after authorization and never enter model-visible arguments or
persisted evidence. Every call reaches a recorded completed, failed, cancelled, refused, or
uncertain state, and output enters task context only after envelope and result-schema validation.

### INV-030 — Stateful retry integrity

An uncertain state-changing effect is never repeated automatically. Retry requires accepted finite
bounds plus declared idempotency, a deduplication key, or an authorized compensation rule, and it
never erases the prior attempt or external-state evidence.

### INV-031 — Concrete tool availability

No tool enters an accepted task binding unless a concrete invocable adapter is registered and its
declared availability conditions hold at the intended execution location. A contract, protocol,
adapter interface, or configured name alone is not evidence of availability.

### INV-032 — Mission authority lineage

Every mission state and task traces to one versioned Mission Brief, organization version, crew
revision, accountable owner, and applicable PM/CTO confirmation or CEO decision. A template,
message, event, recommendation, or discovered capability grants no mission authority.

### INV-033 — Communication type separation

Messages, events, commands, decisions, and escalations remain distinct records. Natural language
changes authoritative state only through a validated authorized command whose result is durable.

### INV-034 — Scoped pause and escalation

A disagreement or missing decision blocks only dependent work. Unaffected eligible work may
continue, while the disputed scope, evidence, options, consequences, and required authority remain
durably inspectable.

### INV-035 — Professional identity integrity

Profile evolution never creates an implicit identity, changes authority or independence, erases
failure evidence, or lets an identity certify its own critical competence.

### INV-036 — Immutable artifact revision

An artifact revision is content-addressed and immutable, identifies its media contract, size,
provenance, producer, task, and retention state, and never becomes accepted merely because storage
succeeded.

### INV-037 — Compare-and-swap working reference

A working reference changes only when its expected current revision matches the durable current
revision. Mismatch produces a conflict and retains both referenced immutable revisions.

### INV-038 — Session isolation

Terminal, PTY, job, browser, and MCP sessions have explicit owners, scopes, cursors, deadlines, and
lifecycle. No task or client inherits another session, authenticated state, environment, or
authority implicitly.

### INV-039 — Compatible fallback only

A fallback executes only when its declared contract satisfies the operation's required semantics,
evidence, safety, and acceptance conditions. Lost fidelity or sources are recorded; incompatibility
blocks the affected operation instead of fabricating success.

### INV-040 — MCP mediation boundary

Every MCP request terminates in the MISHKAN application domain before planning, authorization, or
effect dispatch. Transport identity, advertised availability, instructions, and discovery never
confer application authority.

### INV-041 — External-client non-bypass

No external harness or client can select a production runtime, address an internal agent outside an
authorized mission or direct request, mutate authoritative projections, or bypass CrewAI,
acceptance, policy, and effect enforcement.

### INV-042 — Environment description is not readiness

A Dev Container file, Containerfile, Dockerfile, Compose document, Kubernetes YAML, Quadlet, Nix
definition, or other environment description is an attributable candidate input. Its existence or
successful generation proves neither adapter compatibility, authorization, successful
materialization, runtime readiness, project acceptance, nor task completion. Only location-bound
verification evidence can satisfy the corresponding mission dependency.

### INV-043 — Smallest sufficient crew

Multi-agent production requires a durable collaboration rationale and PM/CTO-confirmed CrewAI
proposal after deterministic eligibility gates. No universal team size, workflow, role/tool matrix,
or hidden numeric ranking decides composition.

### INV-044 — Epistemic-safety preservation

Uncertainty, dissent, contradictory evidence, help requests, and failure reports remain attributable
mission evidence. Their truthful reporting cannot by itself reduce professional-profile competence,
and no producer or manager may replace them with a more favorable account before evaluation.

### INV-045 — Multidimensional health integrity

Team health preserves its dimensions, source interval, evidence cursor, evaluator, confidence, and
limitations. No composite score, leaderboard, quota, activity proxy, or evaluator failure may be
presented as authoritative effectiveness; failure settles as `uncertain`.

### INV-046 — Bounded adaptation authority

Automatic adaptation remains within accepted members, plan, scope, independence, authority, and
risk. A material boundary change uses the existing executive decision path and creates new crew and
collaboration-plan revisions before dependent work becomes eligible.

### INV-047 — Retrospective non-promotion

A retrospective and collaboration-history projection are immutable evidence and proposals, not a
second organization registry or promotion authority. Knowledge, skills, templates, and professional
competence change only through their existing governed lifecycle.

## 8. Policy decision contract

### 8.1 Capability request

Every request evaluated by policy contains at least:

- acting identity and functional role;
- namespaced capability identifier and declared effect class;
- project, bound execution-context, applicable repository, and run identity;
- concrete targets and typed scopes applicable to that capability;
- plan, organization, and policy fingerprints;
- requested credentials, resources, and time bounds without credential values;
- parent task and delegation lineage;
- whether the request is interactive, headless, scheduled, or remote.

Capability identifiers and their scope schemas are registered through the public versioned model.
Adding a new capability does not require adding it to a private source-code deny-list.

### 8.2 Decision values

The only policy decisions are:

| Decision | Meaning |
|---|---|
| `allow` | The exact request may execute without interactive approval |
| `require_approval` | Execution pauses until an authorized approval matching the exact request exists |
| `deny` | Execution is refused before effect |

No match is equivalent to `deny` because absence of a grant is absence of authority, not because
the capability appears on a hardcoded forbidden list.

### 8.3 Matching and precedence

1. Only rules whose typed selectors all match are candidates.
2. A more specific scope takes precedence over a less specific scope.
3. An explicit priority resolves candidates of different declared priority.
4. Conflicting decisions at equal specificity and priority produce `deny` with `ERR-POL-002`.
5. An approval is valid only for the request fingerprint and expiry or use bound it declares.
6. The full candidate set, winning rule, and decision reason are recorded without secrets.

The exact normalization and specificity algorithm becomes a versioned policy schema in later design;
it must satisfy these semantics.

### 8.4 Policy change during work

A policy version has an activation record defining when and where it applies. New capability
requests use the effective version at their enforcement boundary. Previously completed effects are
never rewritten. Revocation may cancel or block outstanding work only through an explicit scoped
revocation record, and the consequence is auditable.

## 9. Skill contract

### 9.1 Package and disclosure

A skill package has one versioned `SKILL.md` identity and may reference scripts, references,
templates, examples, or tests. Catalogue metadata is Level 0, the complete manifest and operating
instructions are Level 1, and specifically required supporting content is Level 2. A selected
skill's exact version and loaded content are part of task evidence.

### 9.2 Sources, selection, and composition

Bundled, project, external, community, URL, source-control, or hub sources exist only when the
effective configuration declares them. The same public configuration defines precedence and stack
bounds. An unresolved identity conflict is refused. Explicit invocation and policy-permitted
automatic selection use the same compatibility, provenance, and evidence rules. A bundle is an
ordered procedural overlay for a task, never a hidden universal plan.

### 9.3 Learning and mutation

An explicit teaching request, correction, miss rule, or reviewed execution result may produce a
new immutable skill version. Create, patch, edit, delete, archive, restore, install, update, and
reset are typed stateful capabilities decided by policy. `allow` may validate, activate, and use a
native correction immediately and reversibly; `require_approval` pauses it; and `deny` refuses it.
Policy MAY require staging according to source, trust, scope, and risk, but staging is not a
universal precondition. A newly discovered community extension remains only a candidate until its
acquisition and activation are authorized.

### 9.4 Trust and lifecycle

Acquisition or mutation creates a provenance lock and runs the configured content inspections.
Unresolved findings at or above the configured threshold quarantine the candidate. Usage outcomes,
pinning, and configured staleness rules may propose recoverable archival; they never silently
destroy history. MISHKAN does not require or operate a hosted marketplace, and every external
source follows the same validation path.

## 10. Tool contract

### 10.1 Registry and discovery

A registry snapshot contains versioned tool contracts from configured native or external sources.
Each contract declares identity, schemas, effect class, availability, scopes, credentials by
reference, timeout, idempotency, and provenance. Minimal metadata may be searched before full
schemas are loaded. Collisions, dependency failure, and external schema drift remain explicit and
cannot mutate a bound snapshot. Availability includes a concrete registered adapter and its
location-specific readiness; abstract adapter types remain catalogue metadata, not bindable tools.

### 10.2 Toolsets and assignment

Toolsets are configured named compositions, not authority records. They resolve recursively within
configured bounds to exact tools before plan acceptance. The organization constrains role
eligibility, the plan selects the task set, policy decides effects, and the execution location must
advertise support. No source, server, credential, toolset, or availability state grants access by
itself.

The registry does not require a universal cross-project capability taxonomy. Execution-context
evidence plus objective and optional-template constraints guide the planner, while role or project
toolsets narrow eligible visibility. Only the exact resolved tools recorded in the accepted plan
become task bindings.

### 10.3 CrewAI binding and dispatch

Production tools are represented through supported CrewAI tool interfaces. A MISHKAN enforcement
wrapper validates the typed call envelope and arguments, resolves actual targets, applies the
authorization and runtime limits, resolves only required credentials, dispatches the configured
adapter, and validates the result. This boundary does not implement another agent tool-calling
loop.

File, terminal/process, web, and browser implementations may be general-purpose tools. For a
terminal/process call, the executable or command pattern, arguments, working directory,
environment, network access, resource limits, and declared effect are normalized call inputs and
policy selectors. A discovered `pytest`, `npm`, `go`, or equivalent command does not require a new
tool identity, and its discovery does not authorize its execution.

### 10.4 External tools and lifecycle

Configured external protocol sessions expose only negotiated, filtered, namespaced capabilities.
Connection state and schema drift are observable. Adding, enabling, disabling, updating, removing,
or reprioritizing sources and toolsets is an atomic policy-governed capability that creates a new
registry snapshot and preserves previous evidence.

## 11. Mission and organization contract

A mission begins with a free-form objective and a versioned Mission Brief jointly confirmed by PM
and CTO. Crew composition is contextual and drawn from the exact persistent organization version;
`Mission_Lead` is a temporary responsibility, not a new identity. Optional templates contribute
guidance only. Assignments identify one accountable owner, contributors, evidence, authority, and
handoff. Production, evaluation, reporting, and evidence audit remain independently attributable.

The Mission Brief records environment intent and known constraints. A bounded CrewAI planning turn
produces a candidate `MissionEnvironmentPlan` with an accountable professional identity, rationale,
evidence, requested outcome, descriptor semantics or selection constraints, affected tasks, and
verification criteria. RSP-005 validates and owns the normalized proposal as plan content. RSP-025
does not choose the engineering outcome: it resolves that proposal for each distinct repository,
greenfield workspace, service group, or execution location into a compatible `EnvironmentBinding`
or returns a precise incompatibility for replanning.

The accepted task plan references exact verified existing bindings or attributable generated
descriptor artifacts and change sets. A project mutation remains a separate Edit/Patch effect;
building, starting, probing, and cleaning an environment remain separate Terminal/Process or
adapter effects executed by an accountable CrewAI task. A materially different fallback is a plan
revision, never a silent resolver substitution. A mission revision preserves prior decisions and
invalidates only dependent bindings whose context changed.

The durable communication domain distinguishes channel, message, command, decision, escalation,
and intervention records. PM and CTO resolve ordinary disagreement within their declared authority;
otherwise only dependent work pauses and the CEO receives an actionable escalation. Every accepted
intervention records scope, reason, authority, effect, and resulting mission state.

Before multi-agent production, mission governance records the smallest sufficient production shape
and a collaboration plan. CrewAI authors the attributable composition proposal; deterministic gates
validate competence, availability, authority, conflicts, and independence; PM and CTO confirm the
selection. Health signals remain cheap and continuous, while independent model-backed evaluation
occurs only at milestones. Collaboration history is a projection over accepted mission evidence,
not another registry. Adaptations within already accepted membership and authority may be bounded;
material boundary changes use the existing executive decision path.

## 12. Execution-session contract

Direct processes, Bash shells, PTYs, managed jobs, browser contexts, and long-running MCP calls use
typed lifecycle records. Creation binds owner, execution location, working scope, environment,
network and credential references, resource bounds, deadline, and policy decision. Observation uses
bounded output or cursors. Cancellation is a request followed by settlement evidence; loss of
contact leaves a state-changing effect `uncertain` until reconciled. Sessions never become ambient
shared context.

## 13. Artifact contract

An artifact manifest refers to immutable content, media type, size, producer, task, provenance,
integrity identity, validation state, and retention state. Large outputs may stream to bounded
artifact storage without entering prompts or events in full. A working reference is a scoped name
pointing to one immutable revision and advances only by compare-and-swap. Garbage collection,
retention, legal hold, recovery, and missing-content reconciliation preserve manifests and never
invent content or acceptance evidence.

## 14. MCP and external-harness contract

MISHKAN acts as an MCP client for configured external capabilities and as an MCP server facade for
governed application operations. STDIO and Streamable HTTP are transport choices, not authorities.
Client and server identities, negotiated schemas, session state, cancellation, progress, reconnect,
and uncertain effects remain explicit. An external harness receives only versioned application
resources, tools, prompts, commands, events, and results that its identity and policy allow; all
production agent/team coordination remains within CrewAI.

## 15. Plan-revision contract

A plan revision is acceptable only when:

1. it cites validated evidence or an explicit engineer request as its reason;
2. it retains the run, objective, bound execution context and revision, and organization lineage;
3. it describes added, removed, changed, and preserved tasks;
4. preserved results still satisfy unchanged inputs, contracts, revision requirements, and policy;
5. invalidated results remain in history and are never presented as current acceptance evidence;
6. every introduced or changed capability passes policy evaluation;
7. the new plan version is durable before introduced work becomes eligible.

The planner may adapt task shape autonomously inside policy. Immutability applies to each recorded
plan version and issued task envelope, not to the run's ability to evolve.

## 16. Strict refusals

These are integrity failures, not a hardcoded list of business operations:

| ID | Refusal condition | Required outcome |
|---|---|---|
| REF-001 | Required configuration or provenance is absent or incompatible | No run or partial configuration mutation |
| REF-002 | No valid policy decision exists at an effect boundary | No effect; record the decision failure |
| REF-003 | An actor attempts to authorize itself or enlarge its scope | No effect; emit security evidence |
| REF-004 | Policy rules conflict without deterministic precedence | Deny with `ERR-POL-002` |
| REF-005 | Plan, role, dependency, loop, or output contract is invalid | Reject the plan or result with every detected violation |
| REF-006 | Repository revision or produced lineage cannot be established | Block affected execution or acceptance |
| REF-007 | A resolved path or actual external target exceeds authorized scope | Refuse before access or effect |
| REF-008 | Secret-like content reaches a prohibited boundary | Block the boundary and retain only non-secret evidence |
| REF-009 | Result schema, task contract, producer identity, or evaluation separation fails | Do not accept the result |
| REF-010 | A completion would duplicate an already accepted task attempt | Preserve the first result; record and ignore the duplicate |
| REF-011 | Cancellation is active | Do not make new tasks eligible |
| REF-012 | Persisted schema is unsupported | Refuse automatic mutation; identify the required operator action |
| REF-013 | Worker identity, envelope, revision, capability, deadline, or lease is invalid | Reject claim or completion |
| REF-014 | A skill package, reference, dependency, composition, or mutation contract is invalid | Keep the candidate inactive and report all detected failures |
| REF-015 | Skill provenance, inspection, trust, quarantine, or activation evidence is incomplete | Keep the candidate inactive or preserve the prior active version |
| REF-016 | Tool definition, schema, adapter, composition, namespace, or registry snapshot is invalid | Exclude the invalid entry and do not bind it to a task |
| REF-017 | A tool is absent from the exact role, plan, registry, location, or policy scope | Do not expose or dispatch it |
| REF-018 | A tool call has invalid arguments, unresolved targets, unavailable required constraints, or missing authorization | Produce no tool effect and record the refusal |
| REF-019 | Tool output is invalid or a state-changing effect is uncertain | Contain the output, block dependent acceptance, and require contract-safe retry or reconciliation |
| REF-020 | Mission Brief, executive confirmation, crew identity, assignment, or intervention authority is invalid | Refuse the transition and leave unaffected eligible work unchanged |
| REF-021 | A message, recommendation, projection, or event is presented as an authoritative command | Refuse the mutation and identify the required command contract |
| REF-022 | A session owner, scope, cursor, lifecycle, or settlement cannot be established | Refuse reuse or acceptance; preserve the session as failed, lost, or uncertain |
| REF-023 | Artifact content, manifest, provenance, integrity, or retention state is invalid | Keep it unaccepted and preserve recoverable evidence |
| REF-024 | A working-reference expected revision does not match | Preserve both revisions and return a deterministic conflict |
| REF-025 | MCP or harness identity, schema, mediation, session, or requested authority is invalid | Refuse the request before CrewAI binding or effect dispatch |
| REF-026 | An engine, environment descriptor, adapter, verification, or fallback lacks required observed compatibility or availability | Block only affected work and record truthful degraded, failed, uncertain, or unavailable state |

## 17. Permitted exceptions

The following are valid behavior and do not weaken the invariants:

- a policy-authorized stateful operation, including commit, push, deployment, release, migration,
  or secret rotation;
- a plan revision that satisfies the plan-revision contract;
- a retry inside finite policy and plan bounds;
- degraded work that does not depend on the unavailable optional source and exposes the limitation;
- duplicate event delivery when consumers can identify it and reconstruct the same state;
- worker reassignment after lease expiry when only one completion can be accepted;
- configured schedule overlap when an exact policy rule allows it.
- policy-authorized unattended skill creation, update, installation, archival, or restoration after
  all integrity preconditions hold.
- policy-authorized unattended tool-source or toolset lifecycle changes that create a new registry
  snapshot without changing accepted task bindings.

## 18. Technical constraints carried into design

These constraints shape later responsibilities but do not alter the contract:

1. CrewAI 1.x is the sole production coordination runtime for agents, tasks, teams, crews, and
   flows; deterministic runtime doubles are test-only.
2. Core operation supports Linux and macOS and does not require a paid hosted service.
3. Product surfaces are Python CLI/SDK, one headless control daemon, remote workers, and an
   operational terminal client; there is no version 1 web dashboard. Every client issues the same
   governed application commands and owns no authoritative policy or state.
4. Persisted schemas are explicitly versioned and migrations are initiated through policy-governed
   operations rather than automatic mutation.
5. Distributed execution is post-core and must preserve local acceptance semantics.
6. Core support targets Linux and macOS; Windows is not a version 1 target.
7. Configuration validation and readiness reporting complete within 10 seconds on the reference
   local environment, excluding an explicitly requested model download or repository scan.
8. The local control plane accepts at least 100 valid events per second for 60 seconds without an
   undetected loss.
9. Synchronous pre-operation advisory returns within 500 milliseconds at the 95th percentile on
   the reference environment or applies its defined safe fallback and records a timeout event.
10. Live monitoring uses bounded buffers and remains responsive above display capacity.
11. Configuration, coordination, planning, execution, recovery, authorization, and safety modules
    maintain at least 80 percent branch coverage.
12. Release acceptance includes repeatable interruption and secret-containment tests required by
    NFR-009 and NFR-010.
13. Skills use a portable `SKILL.md` package compatible in direction with the Agent Skills
    convention; selected skill instructions enrich authorized CrewAI task context and do not form a
    competing runtime.
14. Native and configured external protocol tools use supported CrewAI tool interfaces. Enforcement
    wrappers may narrow and validate those calls but do not replace CrewAI's production tool-calling
    runtime.
15. The application exposes versioned HTTP/OpenAPI and resumable SSE contracts plus an MCP facade;
    a local STDIO MCP bridge is non-authoritative and stateless beyond transport lifecycle.
16. Local artifacts use content-addressed filesystem storage; distributed artifacts use an
    S3-compatible blob store. Metadata remains in the selected transactional application store.

## 19. Abstract dependencies

Dependencies describe required capabilities, not component ownership or topology.

| ID | Abstract capability | Criticality | Contract when unavailable | SRS source |
|---|---|---|---|---|
| DEP-001 | Production agent coordination | Core required | Affected run cannot start or advance; emit retryable dependency failure | ORG-001–016, MSN-001–008, RUN-001–012, TC-001–003 |
| DEP-002 | Model inference | Required per selected task | Block affected task; attempt only policy-authorized routes; expose failure | SYS-003, RUN-007 |
| DEP-003 | Workspace, repository, and source-control access | Core required | Work requiring unavailable evidence is blocked, not guessed | PRJ-001–010, SAF-001–008 |
| DEP-004 | Durable metadata and evidence persistence | Core required | Do not report acceptance or completion | RUN-008, OBS-003, NFR-007 |
| DEP-005 | Identity and credential resolution | Required per protected action | Refuse action without revealing or persisting credentials | SAF-003, SAF-009, DST-002–003 |
| DEP-006 | Policy decision and capability enforcement | Core required | No effect may occur; produce a stable policy failure | PLN-005–008, SAF-003–006 |
| DEP-007 | Isolated command execution | Required for untrusted commands | Refuse command execution when accepted isolation cannot be provided | SAF-011, SAF-013 |
| DEP-008 | Clock, timezone, and unique identity | Core required | Refuse creation when identity or time cannot be established | SYS-004–005, AUT-003 |
| DEP-009 | Durable event recording | Core required for material transitions | Do not expose an unrecorded transition as accepted or complete | OBS-001–003 |
| DEP-010 | Live event delivery | Optional for task correctness | Buffer within bounds, expose gaps, preserve durable work | OBS-004–006, NFR-006 |
| DEP-011 | Episodic, semantic, and structural context | Optional | Continue safe work in declared degraded mode | KNW-001–005 |
| DEP-012 | Literal scoped repository reading | Required when no other source can establish a needed fact | Block affected reasoning instead of inventing evidence | PRJ-004–005, KNW-002 |
| DEP-013 | Skill catalogue, provenance, and lifecycle store | Optional for ordinary task execution; required for skill use or learning | Record miss and continue without the skill, or preserve any unresolved mutation until available | SKL-001–025 |
| DEP-014 | Persistent scheduling | Required only for scheduled mode | Interactive and direct headless runs remain available | AUT-001–007 |
| DEP-015 | Shared worker coordination | Post-core distributed only | Local mode remains valid; distributed tasks block or recover by lease | DST-001–010 |
| DEP-016 | Current-state monitoring projection | Optional for execution | Execution continues; monitor reconnects from durable state | RUN-012, OBS-004–006 |
| DEP-017 | External effect surface | Required only by a requested capability | Apply the policy decision; never bypass external protection | SAF-003–008 |
| DEP-018 | Tool registry, discovery, and adapters | Required per tool-using task | Do not bind or dispatch the unavailable tool; expose configured fallback eligibility | TOL-001–027 |
| DEP-019 | Mission, organization, and conversation persistence | Core required | Do not launch or mutate a mission whose Brief, crew, command, or decision cannot be made durable | ORG-001–016, MSN-001–016 |
| DEP-020 | Artifact and working-reference storage | Required per artifact-producing task | Do not accept missing content; retain recovery and conflict evidence | ART-001–008 |
| DEP-021 | Native execution and session adapters | Required per selected capability | Refuse unavailable modes and preserve uncertain effects for reconciliation | FIL-001–007, EDT-001–008, EXE-001–008, BRW-001–008 |
| DEP-022 | Web retrieval surfaces | Optional unless required by the accepted task | Use only a contract-compatible configured fallback and expose provenance loss | WEB-001–007 |
| DEP-023 | MCP mediation and external-client identity | Required only for MCP or harness use | Other clients remain available; affected request receives stable refusal | MCP-001–009 |
| DEP-024 | Engineering engine and mission-environment resolution | Required per selected technical action | Do not claim readiness or execute without a concrete verified adapter and settled environment decision at the location | CTX-004–008, ENG-001–013, MSN-016 |

Every plan identifies its required dependencies. Required dependencies cannot silently become
optional; optional failures remain visible. Concrete products, protocols, processes, and topology
are assigned only after the responsibility and architecture stages.

## 20. SRS traceability

| Contract area | Principal SRS source |
|---|---|
| Operating context and identity | SYS-001–007, PRJ-001–010, CTX-001–008 |
| Adaptive planning and policy | PRJ-007–010, PLN-001–021, SAF-003–006 |
| Organization and separation | ORG-001–019 |
| Mission governance and communication | MSN-001–022 |
| Execution and recovery | RUN-001–013 |
| Enforcement and evidence | SAF-001–013, OBS-001–008 |
| Knowledge | KNW-001–006 |
| Skills and procedural memory | SKL-001–025 |
| Tools and atomic capabilities | TOL-001–027 |
| Files and non-mutating search | FIL-001–007 |
| Edits and patches | EDT-001–008 |
| Terminal, Bash, PTY, and jobs | EXE-001–008 |
| Web retrieval | WEB-001–007 |
| Browser sessions | BRW-001–008 |
| Artifacts and working references | ART-001–008 |
| MCP and external harnesses | MCP-001–009 |
| Engineering engines and environments | ENG-001–013 |
| Headless scheduling | AUT-001–007 |
| Distributed preservation | DST-001–010 |
| Performance and compatibility | NFR-001–010 |
| Stable refusals | SRS §26 |
| Production runtime and clients | TC-001–009 |
