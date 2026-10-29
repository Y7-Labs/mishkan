# MISHKAN Software Requirements Specification

**Status:** Accepted by D-032; amended by D-051 on 2026-10-29
**Version:** 1.7
**Derived from:** PRD 1.5 accepted by D-051
**Normative vocabulary:** MUST, MUST NOT, SHOULD, MAY follow RFC 2119 meanings

Version 1.6 replaces the historical 32-identity/15-outcome assumptions and promotes the validated
mission, engineering-context, native-capability, artifact, MCP, harness, skills, communication,
intervention, professional-evolution, and mission-scoped environment behavior in WD-001–WD-028.
Version 1.5 decision-quality assistance and the 1.4 general-tool model remain incorporated.
Version 1.7 adds smallest-sufficient crew formation, explicit collaboration plans, epistemic safety,
multidimensional health evidence, bounded adaptation, retrospectives, and collaboration lineage.

## 1. Purpose and scope

This SRS defines the observable behavior and constraints of MISHKAN product version 1. It does not
assign responsibilities to components or prescribe an implementation except where an explicit
engineer-selected technical constraint is recorded in §27.

Core release requirements cover local, interactive, and headless operation on one machine.
Distributed requirements are identified as post-core and do not block acceptance of the core
release.

## 2. Actors

| Actor | Description |
|---|---|
| Human CEO / engineer | Submits objectives, controls durable authority, converses with PM and CTO, intervenes where necessary, and reviews work |
| Engineering lead | Reviews organization-wide progress, evidence, policy compliance, and outcomes |
| Operator | Configures execution resources, credentials, schedules, retention, and approved remote capacity |
| External harness | Uses versioned HTTP or MCP application contracts as a governed MISHKAN client |
| External scheduler | Requests an already defined run through an idempotent interface |
| Remote worker | Executes an assigned immutable task under coordinator-issued identity and policy |
| External service | Provides optional inference, context, knowledge, or structure capabilities |

## 3. General system behavior

### SYS-001 — Explicit configuration

The system MUST load one versioned effective configuration before accepting a run.

**Verification:** Starting without a resolvable configuration produces a configuration error and no
run record.

### SYS-002 — Configuration provenance

The system MUST expose the source and precedence of every effective configuration layer without
revealing resolved credentials.

**Verification:** A configuration inspection reports each contributing layer and masks secrets.

### SYS-003 — No hidden operational routing

The system MUST NOT select an inference provider, remote service, repository, worker, or execution
mode that is absent from the effective configuration or accepted plan.

**Verification:** Removing each value causes validation failure rather than an undocumented default.

### SYS-004 — Stable identifiers

Every persisted domain record MUST have a globally unique UUID identifier. This includes projects,
execution contexts and revisions, missions, Briefs, crew revisions, conversations, messages,
commands, decisions, escalations, interventions, plans, runs, tasks, approvals, events, artifacts,
skills, professional-profile revisions, schedules, sessions, and workers.

### SYS-005 — Time representation

Persisted timestamps MUST represent an unambiguous instant in UTC. User-facing rendering MUST
identify the applied configured IANA timezone.

### SYS-006 — Interface parity

Every state-changing capability exposed through an interactive interface MUST also be available
through a documented programmatic interface with the same validation and authorization behavior.

### SYS-007 — Error classification

Expected user, policy, dependency, and system failures MUST use stable machine-readable error codes
and MUST NOT be reported as successful runs.

## 4. Execution context and repository establishment

### PRJ-001 — Repository-bound run identity

A repository-bound run MUST target exactly one repository and record one immutable base revision
identifier. It MUST NOT bind another repository implicitly. Authorized later revisions MUST retain
lineage to that base and the responsible task.

### PRJ-002 — Revision validation

The system MUST verify the bound repository revision before planning and before accepting each
externally executed task result for a repository-bound run.

### PRJ-003 — Repository discovery

Initialization MUST discover repository characteristics relevant to planning, including languages,
frameworks, test surfaces, deployment surfaces, data-change surfaces, and declared project policy.

### PRJ-004 — Evidence-based discovery

Every discovered characteristic used to select work MUST include evidence identifying its source.

### PRJ-005 — Unknown characteristic handling

The system MUST represent an undiscovered or ambiguous repository characteristic as unknown rather
than infer a definitive value without evidence.

### PRJ-006 — Reinitialization

Reinitializing an existing project MUST preserve accepted run history and MUST produce a new
discovery revision rather than overwriting prior discovery evidence.

### PRJ-007 — Context-specific behavior

Two repositories or prospective workspaces with materially different discovered characteristics
MUST be permitted to produce different plans for the same objective or optional template guidance.

### PRJ-008 — Multi-repository mission binding

A mission MAY coordinate multiple repository- or prospective-workspace-bound runs. Each run MUST
satisfy PRJ-001–002 or PRJ-009–010 as applicable and MUST record the mission-level dependency,
context, revision where applicable, result, and acceptance relationship without granting one
context's paths or authority to another implicitly.

### PRJ-009 — Greenfield execution context

Before a greenfield repository exists, a run MUST bind exactly one versioned prospective workspace
and discovery revision and MUST record the repository base as not yet established. A run that is
neither repository-bound nor prospective-workspace-bound MUST be refused.

### PRJ-010 — Greenfield validation and establishment lineage

The system MUST verify the prospective workspace and discovery revision before planning and before
accepting each externally executed task result. Establishing a repository MUST create an explicit
lineage transition to a repository identity and base revision rather than silently replacing the
prospective context.

## 5. Planning and approval

### PLN-001 — Explicit repository-bound objective

Every repository-bound plan MUST identify the engineer-provided objective, repository revision,
requested result, optional mission-template reference, and applicable policy revision.

### PLN-002 — Structured plan

Every plan MUST declare its tasks, assigned organizational roles, dependencies, allowed parallelism,
inputs, expected outputs, validation contracts, tool permissions, path scopes, time limits, and
completion conditions.

### PLN-003 — Plan validation

The system MUST reject a plan that references an unknown role, violates role separation, exceeds an
organizational cap, contains an unbounded cycle, lacks an output contract, or requests authority not
granted by policy.

### PLN-004 — Plan identity

The accepted normalized contents of a plan MUST determine a stable plan fingerprint. Any semantic
change MUST produce a different fingerprint.

### PLN-005 — Policy authorization

A plan MUST NOT begin execution until its exact fingerprint is evaluated against the effective
versioned policy. The decision MUST be one of allow, require approval, or deny.

### PLN-006 — Policy pre-authorization

A plan MAY begin without interactive approval when a versioned policy matches its objective class,
bound execution context, optional mission-template identity, role set, capability authority, path
and external-resource scopes, resource limits, and plan constraints.

### PLN-007 — Pre-authorization mismatch

Any plan property outside an allow rule MUST be re-evaluated. The system MUST pause for interactive
approval when the matching policy requires approval and MUST reject the revision when policy denies
it.

### PLN-008 — Approval evidence

The system MUST persist the authorization decision, deciding identity or policy rule, decision
time, plan fingerprint, policy revision, matched scope, and any interactive approval evidence.

### PLN-009 — Versioned plan evolution

An executing run MAY revise its plan in response to validated evidence. Every semantic revision
MUST create a new plan version and fingerprint, record its reason and difference from the preceding
version, and pass policy authorization before newly introduced work executes.

### PLN-010 — No silent replanning on resume

Resuming an interrupted run MUST use the latest authorized plan version and preserve accepted task
results that remain valid. It MUST NOT hide replanning, discard compatible accepted work, or expand
authority without a recorded policy decision.

### PLN-011 — Reviewability

The engineer MUST be able to inspect every normalized plan version, its authorization decision,
its difference from the preceding version, and the reasons each role, task, dependency, capability,
and completion condition was selected.

### PLN-012 — Decision context

A task that recommends a durable product, system, architecture, data, security, scalability,
operational, or other technical choice MUST identify the exact decision question, applicable
requirements, repository evidence, constraints, declared preferences, risks, and material unknowns.

**Verification:** A decision fixture with missing context is rejected as incomplete rather than
receiving an unqualified recommendation.

### PLN-013 — Evidence classification

A decision result MUST distinguish verified evidence and its source from assumptions, engineer
preferences, inferences, and unresolved unknowns. It MUST NOT present an unsupported inference as a
verified fact.

**Verification:** Injected unsupported claims remain labelled as assumptions or cause an
inconclusive result.

### PLN-014 — Credible alternatives

When more than one credible option exists, the decision result MUST compare at least two options
against the same declared criteria. When the available evidence supports only one credible option,
the result MUST state how alternatives were sought and why the rejected candidates were not
credible; it MUST NOT fabricate an alternative to satisfy a fixed count.

### PLN-015 — Context-derived criteria

Decision criteria and any weighting MUST be inspectable and traceable to the objective,
requirements, discovered repository evidence, declared preferences, or effective policy. The
system MUST NOT rely on an undisclosed universal technology scorecard.

### PLN-016 — Recommendation contract

A decision result MUST either recommend an option or explicitly declare the evidence
insufficient. A recommendation MUST state its rationale, relevant trade-offs and risks, confidence
and its basis, unresolved questions, expected consequences, and reversal or migration implications.

### PLN-017 — Validation and independent challenge

A consequential recommendation MUST identify proportionate validation evidence, such as a focused
prototype, benchmark, contract test, threat analysis, schema exercise, or authoritative-source
check. An identity that produced the recommendation MUST NOT perform its acceptance evaluation.
Failed or missing required validation MUST leave the recommendation unaccepted.

### PLN-018 — Staged durable decisions

A recommendation MUST NOT silently modify accepted product, system, architecture, data, policy, or
delivery authority. A choice that changes durable authority MUST be staged with its context,
alternatives, rationale, consequences, evidence, validation result, deciding identity, and lineage,
then pass the applicable policy or engineer-approval boundary before becoming effective.

### PLN-019 — Configurable explanation support

The system MUST allow the engineer to request an explanation depth and focus areas for decision
support. Adapting the explanation MUST NOT remove technical evidence, uncertainty, risks, or
consequences required by PLN-012–018.

### PLN-020 — Explicit greenfield plan context

Every prospective-workspace plan MUST identify the engineer-provided objective, prospective
workspace and discovery revision, requested result, optional mission-template reference, and
applicable policy revision. It MUST NOT invent a repository identity or base revision.

### PLN-021 — Agent-authored mission environment plan

For every context identified by MSN-016, the accepted plan MUST contain a Mission Crew proposal
produced through CrewAI with one accountable owner, the requested outcome from ENG-009, rationale,
source evidence, affected tasks and locations, required descriptor semantics or bounded selection
constraints, expected effects, verification and cleanup criteria, and material alternatives or
unknowns. Engine availability, a mission template, or an organizational role MUST NOT select the
outcome by itself. A consequential environment choice MUST also satisfy PLN-012–018.

## 6. Organization and coordination

### ORG-001 — Versioned organization

The system MUST load one versioned organization definition for a run and persist its identity with
the accepted plan.

### ORG-002 — Version 1 roster

Organization version 1 MUST contain exactly the 59 persistent identities listed in Appendix A.
`Mission_Lead` MUST be a temporary responsibility assigned to one of those identities and MUST NOT
be represented as a sixtieth persistent identity.

### ORG-003 — Stable roles, adaptive participation

The organization roster MUST remain stable within version 1, while a plan MUST include only roles
relevant to the mission, repository or greenfield context, risks, evidence, demonstrated
competence, availability, conflicts of interest, and independence requirements.

### ORG-004 — Persistent professional profiles

Every identity MUST declare one stable professional responsibility, branch, independence class,
authority limits, and profile version. Languages, tools, project knowledge, and demonstrated
competencies MUST evolve as attributable profile evidence rather than new implicit identities.

### ORG-005 — Production and evaluation separation

An identity that produces a task artifact MUST NOT evaluate that artifact for acceptance.

### ORG-006 — Orchestration and reporting separation

An identity that makes the acceptance or routing decision for a task MUST NOT be the Reporter for
that same task.

### ORG-007 — Independent evaluation

Every accepted production artifact that can change the approved workspace MUST receive an
independent evaluation before its run can be declared successful.

### ORG-008 — Structured reporting

Every completed multi-task run MUST produce a report conforming to a versioned report contract.

### ORG-009 — Delegation authority

Delegation and reassignment MUST identify the accountable owner, bounded result, context, evidence,
and authority. The Mission Lead MAY make an in-plan local assignment change; the PM MUST confirm
formal composition or reassignment after the CTO confirms required technical, security, and
quality coverage. A change outside the accepted plan MUST trigger replanning.

### ORG-010 — Explicit tool authority

Every participating identity MUST receive an exact resolved set of available tool identities and
versions. A configured shorthand or toolset MAY expand before plan acceptance, but the expansion
MUST be recorded in the plan fingerprint and MUST NOT include tools discovered later.

### ORG-011 — Free-form objectives and optional templates

The system MUST accept a free-form mission objective without requiring membership in a finite
outcome catalogue. It MAY load a versioned optional mission template from configured sources.

### ORG-012 — Adaptive mission templates

A mission template MUST define reusable intent, constraints, evidence, and completion guidance. It
MUST NOT define a mandatory repository-independent task list, fixed crew, implicit authority, or
static role/tool matrix. The accepted plan MUST remain specific to the objective and context.

### ORG-013 — PM and CTO executive roles

PM and CTO MUST be permanent organization agents. PM MUST own product purpose, functional
acceptance, priorities, and formal crew composition. CTO MUST own technical direction, feasibility,
risk coverage, and technical readiness. Neither identity MAY replace independent evaluation or
reporting.

### ORG-014 — Permanent branches and explicit pools

The organization definition MUST preserve the Product and Experience; Architecture and System
Design; Software Engineering; Data and AI; Platform, Delivery, and Reliability; Security and
Supply Chain; Independent Assurance; Research and Decision Support; and Documentation and
Organizational Learning branches and the explicit evaluation, reporting, documentation, and
engineering pools listed in Appendix A.

### ORG-015 — Evidence-based professional evolution

An agent profile MAY gain project knowledge, tool mastery, skill associations, or demonstrated
competence only from attributable evidence identifying scope, source, evaluation, time, and
freshness. An identity MUST NOT self-certify critical mastery, change its own authority or
independence, or erase failure evidence.

### ORG-016 — Scoped learning promotion

Learned information MUST remain at the narrowest justified mission, project, agent, branch, or
organization scope. Promotion to a broader scope MUST use a durable policy decision and preserve
the supporting and contradictory evidence.

### ORG-017 — Multidimensional team-effectiveness evidence

The system MUST preserve independently attributable mission evidence for outcome and user value,
quality and stability, flow and delivery, coordination efficiency, epistemic safety and common
ground, adaptability and learning, and resource efficiency. It MUST NOT collapse these dimensions
into a universal composite score, leaderboard, quota, or automatic professional-profile penalty.

### ORG-018 — Contextual collaboration history

The system MUST derive collaboration history only from accepted missions and immutable team
evaluations. It MAY prefer a proven combination when mission context is directly comparable and
evidence is recent and strong; MUST use partially comparable evidence only as a tie-breaker; and
MUST ignore stale, conflicting, or materially different evidence. Every use MUST cite the project,
mission, task, and evaluation evidence, and current mission fit MUST be able to displace history.

### ORG-019 — Team evidence does not determine individual competence

Aggregate team health, mission outcome, collaboration history, or retrospective findings MUST NOT
automatically promote, demote, reward, penalize, or change the authority or independence of an
individual profile. Any proposed professional learning MUST use the attributable evidence and
governed promotion process in ORG-015–016.

## 7. Engineering context and reconnaissance

### CTX-001 — Layered engineering context

The system MUST distinguish a confirmed portable engineer profile, cited project/repository
evidence, and machine- or run-local observations. It MUST NOT persist an inferred preference or
weakness as a confirmed profile fact without attributable confirmation.

### CTX-002 — Privacy-safe inspection

Default reconnaissance MUST inspect bounded non-secret metadata such as manifests, declared
scripts, CI configuration, documentation, services, and tool configuration. Reading credential
values, shell history, or unrelated private content MUST require separate explicit authority.

### CTX-003 — Safe probes

When inspection policy permits, the system MAY execute read-only version, help, capability, and
health probes. It MUST record the exact command or request, execution location, time, and result and
MUST NOT represent a failed probe as availability.

### CTX-004 — Independent observation states

For an engine, service, skill, or extension, the system MUST represent inventoried, detected,
installed, executable, authenticated, healthy, project-used, eligible, and authorized as
independent states. No earlier state MUST imply a later state.

### CTX-005 — Local observation provenance

Machine paths, versions, health, and credential-reference presence MUST remain machine-scoped and
carry observation time, freshness, source, and sensitivity. Portable export MUST exclude them
unless explicitly selected and safe.

### CTX-006 — Contextual recommendation

The system MUST reject candidates that fail mandatory compatibility, policy, trust, or execution
constraints before comparing remaining candidates using explicit project-relevant criteria. The
result MUST expose evidence, uncertainty, material alternatives, and criterion-level reasoning.

### CTX-007 — Community catalogue

Community discovery MUST use versioned configured catalogues or cited official documentation,
registries, maintained repositories, and hubs. A candidate record MUST include source, resolved
version or revision, observation time, compatibility, prerequisites, maintenance and trust
evidence, and overlap with existing capabilities.

### CTX-008 — Recommendation is not activation

Discovering or recommending an external tool, MCP server, plugin, skill, or pack MUST create only
an inspectable candidate. Installation, trust, enablement, binding, and execution MUST remain
separate policy-governed actions. Existing native skill maintenance follows SKL requirements and
MUST NOT inherit a universal human-review requirement from this rule.

## 8. Mission governance and communication

### MSN-001 — Mission origins

A mission MUST be able to originate from the CEO, PM, CTO, an incident, project evidence, an
independent finding, a dependency, a maintenance need, or an authorized organizational proposal.
It MUST support existing repositories, greenfield work, multi-repository systems, research,
incidents, modernization, platform work, and operations.

### MSN-002 — Mission Brief

Before mission planning, PM and CTO MUST jointly produce a versioned Mission Brief identifying the
problem, desired outcome, scope, exclusions, acceptance criteria, constraints, risks, authority,
proposed crew, evidence requirements, and escalation conditions.

### MSN-003 — Executive agreement and launch

Within an already authorized strategic, budget, project, and policy envelope, PM and CTO MAY launch
a non-strategic mission without a new CEO decision. The launch MUST record PM composition
confirmation and CTO technical, security, and quality coverage confirmation.

### MSN-004 — Disagreement behavior

When PM and CTO cannot agree, the system MUST record the options, evidence, uncertainty, risks,
consequences, and recommendations; pause only the disputed work; continue independent eligible
work; and create an actionable CEO escalation when the decision exceeds their authority or remains
unresolved.

### MSN-005 — Mission lifecycle

A mission MUST expose at least `proposed`, `clarifying`, `planned`, `active`, `paused`, `blocked`,
`evaluating`, `remediating`, `completed`, `failed`, and `cancelled`. Every transition MUST identify
its actor or cause, reason, applicable decision, and evidence.

### MSN-006 — Contextual crew composition

The Mission Crew MUST contain only the persistent identities needed for the concrete mission plus
independent evaluators and a reporter where required. Composition MUST record the project,
competence, availability, conflict, risk, and independence evidence used.

### MSN-007 — Accountable task assignment

Every mission task MUST identify one accountable owner, optional contributors, expected result,
completion criteria, dependencies, authority, exact tools, paths, limits, and required evidence.

### MSN-008 — Collaboration primitives

The system MUST support bounded delegation, collaboration, consultation, handoff, review,
evidence-based challenge, and escalation without encoding those interactions as one fixed workflow.
Agents MAY communicate directly when policy and mission scope permit.

### MSN-009 — Durable channel classes

The system MUST maintain persistent Executive, Mission, Branch, and authorized Direct channels.
The Executive channel MUST preserve the CEO conversation with PM and CTO across restart, client
change, and disconnected periods.

### MSN-010 — Message and command separation

Messages, events, commands, decisions, and escalations MUST have distinct versioned contracts. A
recommendation or natural-language statement MUST NOT change mission state until translated into
an explicit authorized command.

### MSN-011 — Escalation contract

An escalation MUST identify what is blocked, why a decision is required, available options,
consequences, risks, PM/CTO recommendations, deadline where applicable, and independent work that
continues while awaiting the decision.

### MSN-012 — CEO intervention

The CEO MUST be able, subject to effective policy, to comment, answer an escalation, accept or
reject a proposal, suspend or resume work, request or confirm reassignment, stop a task or mission,
and explicitly accept a risk. Every intervention MUST record actor, reason, scope, confirmation,
effect, and resulting state.

### MSN-013 — Complete drill-down transparency

An authorized client MUST expose drill-down from organization and branch status to missions,
crews, agents, conversations, decisions, plans, tasks, artifacts, evidence, risks, failures, costs,
schedules, and events without treating a projection as authoritative state.

### MSN-014 — Notification severity

Notifications MUST distinguish information, attention, action-required, and urgent conditions and
apply configured delivery or silence rules without hiding durable events or pending decisions.

### MSN-015 — Client-equivalent communication

`mishkan chat`, TUI, CLI/SDK commands, HTTP/SSE, and MCP MUST use the same durable conversation,
command, permission, error, and state contracts. No client MUST receive stronger authority because
of its transport.

### MSN-016 — Mission execution-environment intent

The Mission Brief MUST record known execution locations, platform and architecture constraints,
project-established environment definitions, isolation and network needs, credential references,
resource constraints, and required environment evidence. Before environment-dependent tasks become
eligible, the mission plan MUST contain the agent-authored proposal required by PLN-021 and
reference its compatible versioned binding resolved under ENG-009–013, or record the exact
unresolved dependency and affected scope.

### MSN-017 — Smallest sufficient production shape

Before multi-agent production work becomes eligible, PM and CTO MUST record why collaboration is
needed based on distinct expertise, independent authority, parallelizable work, context separation,
or required assurance. The accepted production shape MUST be `single_owner`, `consultative`, or
`multi_producer` and MUST be the smallest shape justified by the Mission Brief and current
evidence. CrewAI MUST produce an attributable composition proposal and alternatives; PM and CTO
MUST confirm the selected composition after deterministic competence, availability, authority,
conflict, and independence gates pass.

### MSN-018 — Versioned collaboration plan

Every accepted Mission Crew MUST have a versioned collaboration plan tied to the Mission Brief and
crew revisions. The plan MUST identify shared outcome and user impact, responsibilities, decision
rights, typed interaction edges, bounded context and Artifact references, communication and
resource budgets, escalation rules, permitted adaptations, and stop conditions. It MUST NOT encode
a universal team size, workflow, or static role/tool matrix.

### MSN-019 — Epistemic safety and complete handoffs

The system MUST preserve uncertainty, dissent, contradictory evidence, requests for help, and
failure reports without automatically penalizing an agent profile. A handoff MUST identify the
completed result and Artifacts, decisions and changed assumptions, uncertainty and failures,
unresolved questions, required next action, and acceptance conditions. A manager or producer MUST
NOT rewrite or suppress negative evidence before independent evaluation.

### MSN-020 — Continuous signals and independent health evaluation

The system MUST collect bounded deterministic signals for blocked and queue time, repeated
rejection or rework, duplicate or overlapping work, missing or repaired handoffs, dependency and
context drift, workload concentration, unacknowledged risk, and coordination/model/tool cost.
Independent milestone evaluation MUST interpret those signals across the dimensions in ORG-017
and MUST return `healthy`, `attention`, `blocked`, or `uncertain` with evidence, limitations, and
confidence. Task completion MUST NOT synchronously depend on a model-backed health evaluation, and
evaluator failure MUST produce `uncertain` rather than a fabricated status.

### MSN-021 — Bounded crew adaptation

Within an accepted plan and policy, the system MAY repair missing context, request consultation or
challenge, split or combine tasks, reduce harmful parallelism, reassign work among already approved
crew members, or pause only dependent work. Adding a member, changing an independence boundary,
altering material scope, expanding authority, or accepting consequential risk MUST follow the
existing PM/CTO or CEO decision path. Every adaptation MUST preserve its detected condition,
alternatives, expected benefit, affected tasks and assignments, authority basis, and before/after
crew and plan revisions.

### MSN-022 — Governed mission retrospective

The system MUST create an immutable retrospective at mission closure, major failure, or explicit
request. It MUST compare expected and observed outcomes, preserve successful and failed
collaboration patterns, contradictions, rework, coordination cost, improvement proposals, and
explicitly non-promoted findings. A retrospective MUST NOT promote knowledge, skills, mission
templates, or professional competence by itself.

## 9. Run execution and recovery

### RUN-001 — Run lifecycle

A run MUST expose at least: awaiting approval, queued, running, blocked, failed, cancelled, and
completed states.

### RUN-002 — Task lifecycle

A task MUST expose at least: pending, eligible, leased or executing, validating, accepted, rejected,
failed, and cancelled states.

### RUN-003 — Dependency enforcement

A task MUST NOT become eligible until all dependencies declared by the accepted plan have accepted
results.

### RUN-004 — Parallel barriers

A downstream barrier task MUST NOT begin until every required upstream parallel task has reached an
accepted terminal result.

### RUN-005 — Bounded iteration

Every iterative plan section MUST declare a measurable completion condition and maximum iteration
count before approval.

### RUN-006 — Structured task result

Every task result MUST conform to a versioned result envelope and any task-specific output contract
before acceptance.

### RUN-007 — Validation retry

A rejected structured result MAY be retried only within the accepted retry policy. Exhaustion MUST
fail the task with the rejection evidence preserved.

### RUN-008 — Durable task acceptance

An accepted task result MUST be durably persisted before dependent work becomes eligible.

### RUN-009 — Resume boundary

After interruption, execution MUST resume from the earliest task that lacks an accepted result.

### RUN-010 — Duplicate completion

The system MUST accept at most one result for a task attempt. Later duplicate completions MUST be
recorded and ignored.

### RUN-011 — Cancellation

Cancelling a run MUST prevent new task eligibility, request cancellation of executing work, preserve
accepted results, and produce a terminal audit record.

### RUN-012 — Progress

The engineer MUST be able to observe run and task state changes as they occur and query the latest
state after reconnecting.

### RUN-013 — Collaboration execution lineage

Every CrewAI delegation, consultation, challenge, handoff, evaluation, and accepted adaptation MUST
record the collaboration-plan revision, participating identities, task, causal command or event,
and immutable evidence references. Local and distributed execution MUST produce the same
authoritative lineage contract.

## 10. Workspace and safety

### SAF-001 — Approved workspace

Every filesystem operation MUST resolve inside the workspace scope accepted with the plan.

### SAF-002 — Resolved-path enforcement

Path authorization MUST evaluate the resolved target, including symbolic links, before access.

### SAF-003 — Least authority

The system MUST deny a tool, path, network destination, credential, or operation unless it is
explicitly authorized by the accepted plan and effective policy.

### SAF-004 — Visible policy

Every policy rule affecting a run MUST come from an inspectable, versioned policy source and be
included in the effective policy fingerprint.

### SAF-005 — Explicit policy evolution

Project or run configuration MUST NOT silently change the effective authorization boundary. Every
policy change MUST create a new version, identify its source and adoption authority, and be applied
only to the scopes it declares. Non-configurable system integrity invariants MUST be specified in
the system contract rather than concealed in a private deny-list.

### SAF-006 — Policy-controlled state changes

Every state-changing capability MUST be declared in the public policy model and configurable as
deny, allow, or require approval with applicable identity, repository, remote, branch, path,
environment, credential, time, and resource scopes. The implementation MUST NOT enforce a hidden
hardcoded action list as product policy.

### SAF-007 — Workspace edits

An agent MAY modify files only inside an approved writable scope and only through an authorized
operation.

### SAF-008 — Reviewable diff

Every accepted state-changing task MUST produce reviewable evidence identifying the intended
change, effective authorization, observed result, and relevant diff or external state reference.

### SAF-009 — Secret handling

Resolved credentials MUST NOT be written to configuration, prompts not requiring them, results,
artifacts, events, logs, snapshots, or diffs.

### SAF-010 — Output inspection

All content MUST pass configured credential and policy inspection before persistence or downstream
use.

### SAF-011 — Isolated execution

Untrusted generated commands MUST execute within the resource, filesystem, process, and network
limits accepted by the plan.

### SAF-012 — Security evidence

Every refused security-relevant action MUST produce a typed audit event containing the responsible
identity, attempted capability, policy decision, and non-secret reason.

### SAF-013 — No prompt-only enforcement

A safety invariant MUST be enforced by a deterministic boundary. An instruction to an AI actor is
not sufficient enforcement.

## 11. Knowledge

### KNW-001 — Context classes

The system MUST distinguish recent episodic context, semantic project knowledge, structural
repository knowledge, and literal repository content.

### KNW-002 — Appropriate retrieval

A context request MUST identify the class of question and MAY query only sources applicable to that
class before falling back to literal content.

### KNW-003 — Attribution

Every returned knowledge item MUST identify its source, scope, retrieval time, and confidence or
ranking basis where available.

### KNW-004 — Staleness

Knowledge whose validity depends on repository state MUST identify the source revision or expose
that it may be stale.

### KNW-005 — Degraded operation

Unavailable optional knowledge sources MUST NOT fail work that can safely proceed without them. The
system MUST expose the unavailable source and resulting limitation.

### KNW-006 — Promotion approval

Knowledge MUST NOT become permanent cross-project guidance without engineer approval and provenance.

## 12. Skills and procedural memory

### SKL-001 — Skill contract

A skill MUST be a versioned portable package with one `SKILL.md` containing machine-readable
identity and discovery metadata plus human-readable operating instructions. A skill MAY include
scripts, references, templates, examples, and tests addressed from that manifest.

### SKL-002 — Distinct semantics

The system MUST distinguish a skill from a tool, prompt, organizational role, mission template,
and execution-context-specific task plan. Selecting a skill MUST NOT grant capabilities or replace
CrewAI coordination, plan authorization, or deterministic enforcement.

### SKL-003 — Level 0 catalogue

The system MUST be able to list, search, and inspect applicable skill identity, summary, version,
source, trust state, and activation status without loading the skill instructions or supporting
content.

### SKL-004 — Level 1 instructions

After selecting a skill, the system MUST load its `SKILL.md` completely before applying the skill.
Selection and load MUST be recorded against the consuming task.

### SKL-005 — Level 2 on-demand content

Supporting skill content MUST be loaded only when referenced by the selected instructions and
needed for the current task. The record MUST identify each loaded item and its content fingerprint.

### SKL-006 — Configured sources

The system MUST discover skills from versioned configured sources that MAY include bundled,
project, operator-managed external, community, URL, or source-control locations. Source locations
and enablement MUST NOT depend on private hardcoded filesystem paths.

### SKL-007 — Deterministic precedence

Skill source precedence MUST be public, versioned, and configurable. Two applicable skills with
the same identity and unresolved equal precedence MUST cause an explicit ambiguity failure rather
than nondeterministic selection.

### SKL-008 — Compatibility and dependencies

Before selection, the system MUST evaluate declared platform, required tools, fallback tools,
environment constraints, organization version, and other declared dependencies. An unmet required
condition MUST prevent activation for the task and identify the condition.

### SKL-009 — Explicit and automatic invocation

The engineer MUST be able to invoke an active skill explicitly through a documented slash form and
an equivalent programmatic interface. The system MAY select an active skill automatically only
when its declared applicability matches the task and the selection is visible in the plan or task
evidence.

### SKL-010 — Composable bundles

The system MUST support versioned named bundles using configured `all` or contextual `select`
behavior and MUST validate compatibility and conflicts before use. A bundle MUST refer to existing
skills and MUST NOT encode task dependencies, delegation, retries, or workflow state.

### SKL-011 — Usage outcomes

Every attempted skill use MUST emit a typed `hit`, `partial`, or `miss` outcome with skill version,
task class, consuming identity, and non-secret reason.

### SKL-012 — Durable miss evidence

The system MUST retain policy-scoped miss and partial-use evidence across restarts and MUST expose
aggregates by task class without silently turning a configured threshold into an activation grant.

### SKL-013 — Explicit learning request

The engineer MUST be able to request learning through a documented `/learn <source>` interaction
and an equivalent programmatic interface using supplied text, files, URLs, repository evidence, or
execution evidence. The request MUST execute as governed CrewAI work, update an applicable existing
package before creating a duplicate, and return an attributable mutation, staged proposal, or
refusal according to effective policy. New or changed executable material MUST obtain independent
execution authority before use.

### SKL-014 — Evidence-triggered proposal

When a configured miss or correction rule is satisfied, the system MAY initiate an authorized
Research-team proposal using the canonical research roles. The triggering evidence and resulting
proposal lineage MUST be recorded.

### SKL-015 — Knowledge-base skills

When supplied source material cannot be safely or usefully embedded in a skill package, the system
MUST allow the skill to retain attributed retrieval instructions and references instead of copying
the entire source.

### SKL-016 — Governed mutations

Create, patch, edit, delete, archive, restore, and supporting-file changes MUST execute as typed
stateful capabilities under effective policy. Policy MUST be able to allow a routine mutation
directly, allow it with later review, stage it pending approval, or deny it. A staged mutation MUST
remain durable across restart until resolved.

### SKL-017 — Coherent package evolution

An update MUST read and identify its base package first. It SHOULD use a focused patch when that
preserves a coherent capability and MAY use a full edit when required. Both paths MUST preserve an
inspectable diff, base and result hashes, provenance, validation, and recoverable lineage.

### SKL-018 — Atomic activation

A skill version and all content it references MUST become active atomically after required
validation, inspection, and policy decision. When policy authorizes immediate use, the next safe
action MAY use the refreshed complete package without process restart; an already dispatched model
or process MUST retain its original snapshot. Failure MUST leave the previously active version
unchanged.

### SKL-019 — Provenance lock

Every installed or active skill version MUST expose source, resolved revision where applicable,
content fingerprints, dependency fingerprints, author claims, acquisition time, trust state, scan
result, and activation decision in a durable provenance record.

### SKL-020 — Mandatory inspection

Before activation, acquired or changed skill content MUST be inspected for configured security,
privacy, Unicode, credential, prompt-injection, and destructive-action findings. Inspection rules
and severity policy MUST be versioned and inspectable rather than hidden in a private list.

### SKL-021 — Quarantine

A skill with an unresolved finding at or above the configured quarantine threshold MUST remain
inactive. Quarantine, override eligibility, and any authorized override MUST be explicit policy
decisions with non-secret evidence.

### SKL-022 — Update and reset

The system MUST detect available updates for configured sources without activating them
automatically, show their provenance and difference, and support a policy-governed reset to a known
source version.

### SKL-023 — Restoration

Rejecting, archiving, deleting, or superseding a skill MUST NOT erase its accepted history. The
engineer MUST be able to restore an eligible prior version through the same validation and policy
path as activation.

### SKL-024 — Lifecycle curation

The system MUST maintain last-use and usage-outcome evidence, MAY propose archival of stale skills
under configured rules, MUST protect pinned skills from automated archival, and MUST NOT
automatically destroy archived skill history.

### SKL-025 — No hosted marketplace dependency

Core skill discovery, creation, review, activation, use, and restoration MUST operate without a
MISHKAN-hosted marketplace. Community acquisition MAY use configured repositories, URLs, or hubs
but MUST pass the same provenance, inspection, and policy path as every other external source.

## 13. Tools and atomic capabilities

### TOL-001 — Distinct semantics

The system MUST distinguish a tool from a skill, prompt, organizational role, mission template,
and task plan. A tool is one typed atomic capability; registering or selecting it MUST NOT grant
authority or create a competing agent execution loop.

### TOL-002 — Versioned tool contract

Every tool MUST declare a namespaced identity, version, summary, input schema, result schema,
effect class, source, availability conditions, timeout behavior, idempotency semantics, applicable
target scopes, credential references without values, and provenance fingerprint.

### TOL-003 — Configured sources

The registry MUST discover tools only from versioned configured sources that MAY include bundled
implementations, project definitions, operator-managed adapters, and external protocol servers.
Source locations, enablement, and transports MUST NOT depend on private hardcoded values.

### TOL-004 — Namespacing and collision

Tool identities from different sources MUST remain unambiguous. An unresolved identity collision
MUST prevent the conflicting tools from entering an accepted registry snapshot and MUST expose all
claiming sources.

### TOL-005 — Toolsets

The system MUST support versioned named toolsets that compose exact tools or other toolsets.
Toolset ordering, inclusion, exclusion, availability rules, and nesting bounds MUST be public
configuration, and resolution cycles MUST be rejected. A toolset MAY constrain discovery or role,
project, platform, session, or task eligibility, but MUST NOT grant authority by itself.

### TOL-006 — Deferred discovery

The system MUST support listing and searching minimal tool metadata without loading every full
schema into task context. The exact input and result schemas MUST be loaded before a tool is bound
to a task or invoked.

### TOL-007 — Availability

The system MUST verify that a concrete invocable adapter is registered at the intended execution
location and evaluate the tool's configured runtime, platform, credential-reference, service,
resource, health, and dependency conditions before task binding. An abstract interface, adapter
type, or contract without a runnable registered implementation MUST be unavailable. Unavailability
MUST be visible and MUST NOT be represented as authorization denial or successful execution.

### TOL-008 — Registry snapshot

Every accepted plan MUST identify the immutable tool-registry snapshot used to resolve its tools.
Registry changes MUST create a new snapshot and MUST NOT silently alter an accepted task.

### TOL-009 — Task binding

A task MAY use only exact tools present in its accepted plan, permitted for the assigned role,
available in the bound registry snapshot, and supported by the assigned execution location.
Execution-context evidence, objective and optional-template constraints, role eligibility, and
toolsets MAY guide selection but MUST resolve to those exact tool identities before plan acceptance.

### TOL-010 — No implicit authority expansion

Enabling a source, tool, toolset, adapter, credential, or external server MUST NOT grant its tools to
an existing role, plan, task, run, or worker. Expansion MUST pass normal plan and policy decisions.

### TOL-011 — CrewAI tool binding

Every production tool selected for an agent MUST be exposed through a currently supported CrewAI
tool interface. MISHKAN MUST NOT implement a competing production model tool-calling loop.

### TOL-012 — Invocation envelope

Every invocation MUST identify the run, task attempt, acting identity, tool and contract version,
registry and plan fingerprints, normalized arguments, declared targets, applicable authorization,
deadline, and unique call identifier.

### TOL-013 — Input validation

Arguments MUST validate against the exact bound input schema before credentials are resolved or the
tool is dispatched. Invalid arguments MUST produce no tool effect.

### TOL-014 — Authorization before dispatch

The system MUST confirm exact task binding and an effective allow decision or required approval for
the tool, effect class, targets, and scopes before dispatch. Tool source or availability MUST NOT
serve as authorization evidence.

### TOL-015 — Late credential resolution

Credential values MUST be resolved only after validation and authorization, supplied only to the
selected adapter fields that require them, and excluded from model-visible arguments and persisted
evidence.

### TOL-016 — Resolved targets

Filesystem, network, repository, environment, remote, and external-resource scopes MUST be checked
against the actual resolved effect targets at the deterministic invocation boundary.

### TOL-017 — Execution constraints

Each call MUST apply the plan- and policy-authorized timeout, resource, isolation, network, and
concurrency constraints for its effect class. If a required constraint cannot be enforced, the call
MUST be refused before dispatch.

### TOL-018 — Result envelope

Every completed, failed, cancelled, or uncertain tool call MUST return a versioned envelope with
call identity, status, timing, non-secret output or artifact references, actual external-state
references where applicable, retryability, and attributable adapter evidence.

### TOL-019 — Result validation

The result envelope and declared result schema MUST validate before tool output enters task context
or accepted evidence. Invalid output MUST be contained and reported as a tool-contract failure.

### TOL-020 — Retry and idempotency

Automatic retry MUST occur only within accepted bounds and when the tool contract establishes
idempotency, a deduplication key, or an authorized compensation rule. An uncertain state-changing
effect MUST NOT be repeated automatically.

### TOL-021 — Timeout and cancellation

A timeout or cancellation MUST stop local work where possible and record whether the external
effect is absent, completed, or uncertain. Uncertainty MUST block dependent acceptance until
reconciled.

### TOL-022 — Invocation evidence

Selection, binding, authorization, start, completion, refusal, retry, timeout, schema failure, and
uncertain effect MUST produce typed non-secret evidence attributable to the exact tool version and
task attempt.

### TOL-023 — External protocol discovery

For each configured external tool server, the system MUST discover only capabilities supported by
the negotiated session, namespace them by server identity, apply configured filtering, and adapt
their schemas through the supported CrewAI integration boundary.

### TOL-024 — External lifecycle and drift

Connection start, readiness, reconnect, shutdown, authentication change, and schema drift for an
external tool source MUST be observable. Drift from a bound registry snapshot MUST block new calls
through that binding until it is reconciled or replanned.

### TOL-025 — Policy-governed lifecycle

Adding, enabling, disabling, updating, removing, or changing the precedence of a tool source,
adapter, tool, or toolset MUST be a typed stateful capability. Changes MUST be validated and become
effective atomically under policy without rewriting prior registry snapshots or call evidence.

### TOL-026 — General-purpose execution tools

A general-purpose terminal or process tool MAY accept a repository-discovered executable,
task-runner target, arguments, working directory, and environment as validated inputs; the system
MUST NOT require a distinct tool contract for every command. The accepted plan and effective
policy MUST constrain the actual executable or command pattern, arguments, working directory,
environment, network access, resources, and effect class at the invocation boundary. Discovery of
a command MUST NOT grant authority to run it.

### TOL-027 — Repository-controlled extensions

A repository-controlled tool, toolset, source, server, or plugin definition MAY contribute a
candidate only through a configured source and declared trust path. Repository content MUST NOT
activate its own extension, approve its own lifecycle change, expand an existing task binding, or
modify effective policy without the normal validation and authorization decisions.

## 14. File, read, and search capabilities

### FIL-001 — Non-mutating family

File, Read, and Search operations MUST NOT modify project or repository state, refresh an index,
or perform an implicit Git network operation. Mutation MUST use a separate authorized capability.

### FIL-002 — Safe object resolution

Every filesystem operation MUST resolve its root, lexical path, actual opened object, link chain,
and scope before content access. Traversal, symlink escape, cycles, unsafe special files, and
detectable path-replacement races MUST produce a refusal or explicit partial evidence.

### FIL-003 — Bounded metadata, listing, and reads

The system MUST provide bounded metadata inspection, deterministic paginated listing, and byte,
text, line, range, head, and tail reads with explicit encoding, binary, link, ignore, truncation,
continuation, and changed-during-read behavior.

### FIL-004 — Distinct search semantics

The system MUST expose distinct file, literal/regular-expression text, structural, symbol/reference,
and repository-history search operations. A result MUST NOT claim semantic coverage when only text
or syntax was examined.

### FIL-005 — Search provenance and partial coverage

Every read or search result MUST record workspace and revision identity, dirty-state evidence,
scope, engine and version, normalized query, freshness, limits, omissions, failures, truncation,
and continuation. A partial result MUST identify both successful and unexamined coverage.

### FIL-006 — Mutation base evidence

A read intended to precede mutation MUST be able to produce a project- and content-bound base
revision token. A later edit MUST reject or explicitly reconcile a stale token.

### FIL-007 — Contextual adapters and native commands

Filesystem primitives, ripgrep, Git, AST engines, language servers, and configured MCP extensions
MAY satisfy these contracts when concretely available. Bash and native commands MUST remain usable
under the same path, policy, event, and artifact boundaries.

## 15. Edit and patch capabilities

### EDT-001 — Structured change set

The system MUST support typed create, write, replace, patch, rewrite, move, copy, delete, and
directory-creation operations in one versioned change-set contract. A structured change set MUST
NOT imply mandatory human approval.

### EDT-002 — Explicit base preconditions

Every affected path MUST declare a non-existence, digest, revision-token, Git-blob, or metadata
precondition. Concurrent mismatch MUST produce conflict, replanning, explicit merge, human
resolution, or separately authorized force behavior rather than silent overwrite.

### EDT-003 — Precise replacement and patching

Targeted replacement MUST declare match semantics and expected occurrence count. Unified patches
MUST validate paths, base files, hunks, context, encoding, permissions, and result. Offset, fuzzy,
three-way, or interactive interpretation MUST be explicitly selected and reported.

### EDT-004 — Structural rewrite truthfulness

A structural rewrite MUST record engine, version, rule, language, scope, matches, parse failures,
ignored files, formatting, and limits. It MUST NOT claim semantic preservation beyond the selected
engine's evidence.

### EDT-005 — Recoverable application

Single-file replacement SHOULD use same-filesystem atomic replacement where supported. Multi-file
application MUST use a durable recovery journal, report its actual atomicity, preserve applied and
unapplied operations, and support restart reconciliation.

### EDT-006 — Verification and conditional rollback

Applied changes MUST be verified against expected content, paths, permissions, scopes, and selected
validation. Rollback MUST have its own preconditions and MUST NOT overwrite later unrelated work.

### EDT-007 — Command-driven mutation

Bash, codemods, generators, formatters, package managers, and project scripts MAY perform authorized
mutations through Terminal/Process. MISHKAN MUST capture the base, actual changed paths, resulting
diff, scope deviations, validation, events, and artifacts.

### EDT-008 — Separate Git effects

Stage, commit, push, force-with-lease, and force-push MUST be distinct typed configurable effects.
When authorized they MUST verify identity, repository, remote, branch, target, and applicable
validation. MISHKAN's delivery sequence remains topic to `develop` to `main`.

## 16. Terminal and process capabilities

### EXE-001 — Unified execution contract

The system MUST define one versioned ExecutionRequest/ExecutionResult contract with direct process,
full shell, interactive PTY, and managed-job modes and expose precise operations for each mode.

### EXE-002 — Direct process semantics

Direct process mode MUST execute one executable and argument vector without shell expansion,
redirection, globbing, substitution, separators, or quoting interpretation.

### EXE-003 — Full shell semantics

Shell mode MUST preserve the selected Bash or configured POSIX semantics, including pipelines,
redirections, substitutions, globbing, arrays where supported, functions, conditions, loops,
traps, and bounded concurrency. Interpreter, startup files, environment, and options MUST be
versioned profile values and personal startup files MUST NOT be inherited silently.

### EXE-004 — PTY lifecycle

Interactive sessions MUST provide bounded open, send, cursor-read, resize, signal, and close
operations. A session MUST be owned by one run or task, have an expiry and process group, preserve
bounded transcript evidence, and settle rather than disappear when its handle closes.

### EXE-005 — Managed-job lifecycle

Managed jobs MUST support start, readiness, status, cursor-based logs, signal, stop, and settlement.
Readiness MUST remain distinct from liveness, and graceful-stop escalation MUST follow the selected
execution profile.

### EXE-006 — No implicit shared shell state

Shell, PTY, and job state MUST NOT be shared globally among agents or projects. Reuse MUST occur
only through an explicitly identified authorized session.

### EXE-007 — Result and output evidence

Every settled execution MUST identify status, timing, exit or signal, execution location, bounded
stdout/stderr previews, complete-output artifacts when retained, produced artifacts, observed
effects, error, and retryability.

### EXE-008 — Cancellation and uncertain effects

Cancellation and timeout MUST act on the process group where possible, preserve output, and record
whether an external effect is absent, completed, or uncertain. An uncertain state-changing command
MUST NOT be retried blindly.

## 17. Web capabilities

### WEB-001 — Typed Web surface

The system MUST expose distinct search, fetch, HTTP-request, extract, map, and crawl operations.
Search discovery MUST remain distinguishable from retrieval, extraction, and generated synthesis.

### WEB-002 — Truthful component roles

Direct search sources, metasearch brokers, composite gateways, HTTP transports, extractors, and
crawlers MUST declare different roles and guarantees. A broker MUST NOT be represented as an
index-owning direct source, and its upstream list MUST be reported as unknown when unavailable.

### WEB-003 — Search selection and provenance

Search MUST support explicit direct, aggregate, verification, and automatic strategies. It MUST
honor an executable explicit source pin, preserve every route and upstream origin available, avoid
hidden duplicate querying, and never compare source-specific scores as universal values.

### WEB-004 — Bounded retrieval

Fetch and HTTP request MUST represent method, normalized URL, redirect policy, credential
references, body or artifact, network profile, accepted media, timeout, status, size, decompression,
and cache constraints. Stateful methods MUST use policy rather than a global prohibition.

### WEB-005 — Network safety

Every request and redirect MUST validate scheme, normalized host, DNS answer, actual connected
address, credential origin, and configured public/private network policy. SSRF, DNS-rebinding,
redirect, decompression, concurrency, depth, and retained-content controls MUST execute outside
prompt instructions.

### WEB-006 — Extraction and citation evidence

Extraction MUST record input hash, engine/version, configuration, output hash, quality warnings,
canonical links, and source spans. A citation MUST bind a claim or passage to URL, retrieval time,
content hash, and exact span; a search snippet alone MUST NOT prove a page's claim.

### WEB-007 — Map, crawl, cache, and degradation

Map and crawl MUST be bounded by scope, depth, count, concurrency, delay, robots profile, render
mode, and stop conditions. Cached or stale results MUST expose freshness. A fallback MUST preserve
the required semantics and disclose lost coverage or block the affected operation.

## 18. Browser capabilities

### BRW-001 — Distinct stateful family

Browser operations MUST remain distinct from content-oriented Web operations. Escalation from Web
to an authenticated or stateful browser MUST be explicit and authorized.

### BRW-002 — Stable capability surface

The system MUST expose session management, observation, action, runtime inspection, and evidence
capture with only the selected operation schema loaded into task context.

### BRW-003 — Profile sensitivity

Isolated, project-persistent, and attached-existing browser profiles MUST have distinct scope,
sensitivity, retention, and policy. An attached user browser MUST require explicit selection and
MUST NOT become an implicit shared session.

### BRW-004 — Observation-bound targets

UI actions MUST identify session, page, and the observation on which a target is based. A stale
reference MUST require re-observation rather than approximate selection. Coordinate or pixel-based
actions MUST be explicit fallbacks with compatible visual capability evidence.

### BRW-005 — Resolved interaction effects

Authorization MUST evaluate the resolved effect of an interaction rather than the generic click,
fill, or press verb. Navigation, submissions, uploads, downloads, permissions, JavaScript,
interception, and persistence MUST be separately governable.

### BRW-006 — Authentication and sensitive state

Credentials MUST be resolved late for an authorized origin. Cookies, tokens, sensitive fields,
storage state, profiles, traces, HAR, downloads, screenshots, and video MUST follow typed artifact,
redaction, retention, and cross-origin controls.

### BRW-007 — Runtime diagnosis and evidence limits

Console, network, performance, storage, and service-worker evidence MUST be bounded, filtered,
cursor-based, attributable to engine/browser versions, and secret-safe. A screenshot MUST NOT be
treated as proof of backend, authorization, or performance behavior it cannot establish.

### BRW-008 — Session failure and uncertainty

Browser operations MUST expose page/session crash, adapter loss, timeout, cancellation, stale
target, and uncertain state. Loss after a potentially non-idempotent submission MUST require safe
observation or reconciliation before retry.

## 19. Artifact capabilities

### ART-001 — Immutable content identity

Committed artifact content MUST be immutable and identified by UUID, digest, size, media evidence,
creation time, producer lineage, sensitivity, retention, validation, and internal storage reference.
A transformation MUST create a new artifact with derivation provenance.

### ART-002 — Scoped working references

A mutable working reference MUST identify its scope, logical name, revision, current artifact, and
prior revision. Updating it MUST use compare-and-swap and MUST refuse a stale expected revision.

### ART-003 — Collections and safe logical paths

Multi-file outputs MUST be representable as immutable collection manifests with member identities,
ordering where relevant, and logical paths that cannot escape the collection namespace.

### ART-004 — Streaming and atomic visibility

Artifact put/get MUST support bounded streaming and backpressure. Incomplete or unverified content
MUST NOT become available. Artifact commit and working-reference update MUST have separate durable
identities even when one local transaction can settle both.

### ART-005 — Validation, trust, and authority separation

Integrity, detected type, security scan, schema validity, rendering, trust, sensitivity,
availability, and authorization MUST remain distinct facts. Invalid or quarantined artifacts MAY
remain inspectable to authorized actors but MUST NOT be ordinary trusted inputs.

### ART-006 — Storage profiles

Local mode MUST support relational metadata and filesystem content-addressed blobs. Distributed
mode MUST support PostgreSQL metadata and an S3-compatible blob-store contract. Workers MUST
receive only short-lived artifact-scoped transfer authority rather than permanent listing access.

### ART-007 — Lifecycle and retention

Lifecycle MUST distinguish staging, validating, available, quarantined, rejected, expired,
tombstoned, deleted, missing, and corrupt. Retention, holds, pins, references, collections, run
evidence, and provenance roots MUST govern resumable reachability-based garbage collection.

### ART-008 — Derived previews and recovery

Previews and conversions MUST be isolated derived artifacts with engine/configuration provenance
and declared loss. Recovery MUST detect missing blobs, orphan blobs, digest mismatch, invalid
references, and incomplete collections and MUST produce an inspectable reconciliation plan rather
than silently delete or invent data.

## 20. MCP connectivity

### MCP-001 — Bidirectional application boundary

MISHKAN MUST act as an MCP client for configured external capabilities and as an MCP server exposing
approved application operations. MCP MUST NOT be an internal agent runtime or expose raw CrewAI,
database, credential, policy, or unrestricted shell objects.

### MCP-002 — Versioned compatibility

Each connection MUST use an explicit pinned, compatible-set, or isolated legacy protocol strategy.
Wire revisions MUST be normalized through versioned codecs and MUST NOT silently change a bound
MISHKAN capability contract.

### MCP-003 — Connection identity and transport

Every connection MUST identify direction, server, transport, protocol strategy, trust, credential
reference, exposure profile, policy, health, and lifecycle. STDIO child processes and Streamable
HTTP connections MUST use bounded process/network/credential controls.

### MCP-004 — Discovery and normalization

Only negotiated primitives and extensions MAY be discovered. Server names, descriptions,
annotations, prompts, resources, and schemas MUST remain untrusted claims. Normalized catalogue
records MUST preserve server, protocol, schema hashes, effects, sensitivity, freshness, health, and
provenance.

### MCP-005 — Independent authority states

Discovered, configured, trusted, eligible, authorized, approved, executed, validated, and accepted
MCP states MUST remain independent. Caller, harness, project, task, agent, connection, and
credential principal identities MUST NOT collapse into one ambient identity.

### MCP-006 — Bounded invocation and long work

Outbound calls MUST validate schema, effects, deadlines, idempotency, and authority before dispatch
and validate returned envelopes afterward. Multi-round input and long-running Tasks MUST persist in
MISHKAN state and survive daemon restart without relying on a transport session.

### MCP-007 — Controlled mediation

An inbound harness request that uses an outbound MCP capability MUST settle as two distinct
authorized operations. Client tokens, third-party credentials, prompts, context, and results MUST
NOT pass transparently between security domains.

### MCP-008 — Indeterminate completion and drift

Protocol incompatibility, schema drift, credential change, connection loss, unsupported
cancellation, and unknown remote completion MUST remain visible. Non-idempotent indeterminate work
MUST be reconciled through remote identity or task evidence before retry.

### MCP-009 — External harness requests

An external harness MAY submit an organizational objective, targeted task, direct-agent request,
skill operation, or authorized intervention. Work requests MUST become governed CrewAI runs or
mini-runs with plan, tool, policy, evidence, validation, and acceptance identity; a harness MUST NOT
receive a raw internal agent object.

## 21. Engineering tools and environments

### ENG-001 — Composed engineering surface

Engineering Tools MUST compose File/Read/Search, Edit/Patch, Terminal/Process, Web, Browser,
Artifact, MCP, and governed Git effects rather than create a competing execution or policy system.

### ENG-002 — Adapter justification

A specialized adapter MUST provide a concrete typed, safety, session, effect-analysis, or evidence
benefit over a general process invocation. Otherwise the project command MUST remain a governed
Terminal/Process input.

### ENG-003 — Contextual engine resolution

Engine resolution MUST consider explicit configuration, project declarations, verified execution
environment, active packs, authorized isolated materialization, external API/MCP capability,
compatible fallback, and visible degradation in that order unless configured policy selects
another explicit order.

### ENG-004 — Versioned technical packs

The system MUST support discoverable, inspectable, enableable, disableable, and upgradeable
versioned technical packs containing guidance and concrete adapters. Pack membership MUST NOT prove
installation, health, relevance, trust, or authority.

### ENG-005 — Priority ecosystem coverage

Acceptance fixtures MUST cover at least Go, TypeScript/JavaScript, Java/Kotlin, Android, Python,
Rust, C, and Swift. This priority controls initial depth and MUST NOT prohibit other configured
languages or platforms.

### ENG-006 — Reproducible environment materialization

When required tooling is absent, the system MUST be able to propose or create, under policy, the
smallest compatible reproducible local, container, development-container, Nix, dependency, build,
test, or CI environment without silently replacing an established project toolchain.

### ENG-007 — Evidence formats and signals

Engineering results MUST preserve applicable unified diff, JUnit, SARIF, coverage, SBOM, benchmark,
structured-log, metric, trace, profile, error, screenshot, video, or platform-test artifacts and
their engine/version provenance. Metrics, logs, traces, profiles, and application errors MUST
remain semantically distinct.

### ENG-008 — Honest fallback

When an ideal engine is unavailable, the system MUST identify the missing engine, selected
fallback, lost precision/coverage/evidence, and reliable remaining work. It MUST block only work
whose required meaning cannot be preserved and MUST NOT infer success from absent evidence.

### ENG-009 — Mission-scoped environment decision

For each materially distinct repository, greenfield workspace, service group, or execution
location used by a mission, the system MUST validate the PLN-021 proposal and resolve its requested
`reuse_existing`, `host_native`, `generate`, `propose_project_change`, or `unresolved` outcome
against project evidence, target platform, required engines, policy, and verification needs. It
MUST produce a versioned compatible binding or a precise incompatibility requiring replanning; it
MUST NOT silently choose a materially different outcome or engineering design.

### ENG-010 — Truthful descriptor selection

When PLN-021 requests `generate` or `propose_project_change`, it MUST identify the required
environment semantics and either the proposed descriptor formats or explicit bounded selection
constraints. Resolution MUST honor those constraints, select only formats supported by a verified
adapter at the target location, and identify each format, specification or engine version, inputs,
and intended lifecycle. Eligible outputs MAY include Dev Container metadata; OCI Containerfile or
Dockerfile build inputs usable by the selected Podman or Docker adapter; a verified Compose
definition; or Podman-supported Kubernetes YAML or Quadlet when the mission actually requires
those semantics. No descriptor family MUST be generated merely because it is available.

### ENG-011 — Environment descriptor evidence

Every generated descriptor set MUST preserve its mission and context identity, source evidence,
base revisions, target platform and architecture, base image identities, build context, declared
features or packages, workspace mounts, user model, network and resource profiles, credential
references, lifecycle commands, expected artifacts, and known compatibility limits when those
fields apply. Secret values MUST NOT be embedded, and an omitted or unknown required fact MUST
remain visible rather than be invented.

### ENG-012 — Governed descriptor mutation

Generated environment descriptions MUST first exist as attributable immutable artifacts or typed
change sets. Persisting them into a project MUST use the Edit/Patch base-revision, preview,
authorization, verification, recovery, and rollback contracts. Generation availability MUST NOT
overwrite, replace, or promote existing project configuration without an explicit compatible
decision and the authority required for the concrete mutation.

### ENG-013 — Environment verification and settlement

Before an environment decision can satisfy dependent mission tasks, the selected adapter MUST
verify applicable parse or schema validity, build or acquisition, startup and readiness, workspace
and dependency access, required project commands, output artifacts, cleanup, and reproducibility
on the intended location. The result MUST record engine and version, resolved inputs and image
identities, timing, observed effects, logs or artifact references, limitations, and final
`verified`, `failed`, `cancelled`, or `uncertain` settlement; a description alone MUST NOT prove a
runnable environment.

## 22. Events, evidence, and retention

### OBS-001 — Typed events

Every state transition, approval, rejection, retry, security decision, knowledge promotion, worker
lease, and schedule trigger MUST produce a versioned typed event.

### OBS-002 — Event identity and order

Every event MUST include an identifier, source, timestamp, relevant entity identifiers, and an order
that permits reconstruction within one run.

### OBS-003 — Durable evidence

Accepted plans, approvals, task results, reports, diffs, and terminal run states MUST be durably
stored before being reported as accepted or complete.

### OBS-004 — Live subscription

The system MUST provide a resumable live event subscription and a bounded current-state snapshot.

### OBS-005 — Filtering

The engineer MUST be able to filter persisted and live events by type, run, task, identity, team,
time range, and security relevance.

### OBS-006 — Event transport loss

Temporary loss of the live event transport MUST NOT invalidate an otherwise safe task. Events MUST
be buffered within a configured bound and gaps MUST be detectable.

### OBS-007 — Retention

Retention policy MUST be versioned, inspectable, and applied without removing evidence protected by
an active hold or incomplete run.

### OBS-008 — Secret-safe observability

Event and snapshot serialization MUST apply the same secret-handling guarantees as other persisted
outputs.

## 23. Headless operation and scheduling

### AUT-001 — Headless equivalence

Headless execution MUST apply the same planning, approval, policy, validation, and evidence rules as
interactive execution.

### AUT-002 — Persistent schedule

A schedule MUST survive control-process restart and identify its project or prospective workspace,
mission request, optional template reference, input, timezone, trigger, overlap key,
pre-authorization policy, and status.

### AUT-003 — Timezone

A recurring schedule MUST use an identified timezone and MUST reject an ambiguous or invalid
timezone.

### AUT-004 — Overlap prevention

The system MUST prevent concurrent active runs with the same configured overlap key unless an
explicit policy permits overlap. The key MUST be derived from the resolved mission target and MUST
NOT depend on membership in a fixed outcome catalogue.

### AUT-005 — Idempotent trigger

Repeating the same schedule trigger request MUST NOT create more than one accepted run for that
trigger occurrence.

### AUT-006 — Schedule control

The operator MUST be able to create, inspect, trigger immediately, pause, resume, and remove a
schedule, and inspect its run history.

### AUT-007 — External scheduler compatibility

An external scheduler MUST be able to invoke the same idempotent run interface used internally.

## 24. Distributed execution — post-core

### DST-001 — Explicit distributed mode

Remote execution MUST occur only when the operator explicitly enables distributed mode and
configures its shared coordination store.

### DST-002 — Worker identity

Every worker MUST have an individually issued, renewable, and revocable identity.

### DST-003 — Bounded enrollment

Worker enrollment authority MUST be single-use, expire within a configured short interval, and be
invalid after successful use or revocation.

### DST-004 — Capability advertisement

A worker MUST advertise its supported capabilities and resource limits. It MUST receive only tasks
whose requirements it satisfies.

### DST-005 — Versioned task envelope

A remote task assignment MUST be immutable after issuance and include the execution-context
revision, repository revision when established, plan fingerprint, task contract, role-definition
fingerprint, inputs, policy fingerprint, path scope, resources, and deadline.

### DST-006 — Lease

A remote task claim MUST have a renewable bounded lease. Expiry MUST make the task eligible for
recovery without accepting more than one completion.

### DST-007 — Heartbeat and loss

Worker availability MUST be derived from bounded heartbeats. Worker loss MUST be visible and MUST
trigger lease-based recovery.

### DST-008 — Revision mismatch

A worker MUST refuse execution or completion when its execution-context revision or applicable
repository revision differs from the task envelope.

### DST-009 — Worker authority

A worker MUST NOT expand its task authority or authorize its own plan. It MAY perform a stateful
operation only when that exact capability and scope are present in its coordinator-issued envelope
and the effective policy decision permits it.

### DST-010 — Exactly-once acceptance

The coordinator MUST accept at most one valid completion for a task attempt despite retries,
duplicate delivery, worker loss, or partial network failure.

## 25. Non-functional requirements

### NFR-001 — Supported core platforms

The core release MUST run on supported Linux and macOS versions. Windows is not a version 1 target.

### NFR-002 — Local independence

The core local acceptance workflow MUST complete without a paid hosted service.

### NFR-003 — Startup

On the reference local environment, configuration validation and readiness reporting MUST complete
within 10 seconds, excluding an explicitly requested model download or repository scan.

### NFR-004 — Event capacity

The local control plane MUST accept at least 100 valid events per second for 60 seconds without an
undetected loss.

### NFR-005 — Advisory latency

Any synchronous pre-operation advisory MUST return a decision within 500 milliseconds at the 95th
percentile on the reference local environment or permit the safe fallback and emit a timeout event.

### NFR-006 — Bounded memory

Live monitoring clients MUST maintain configured bounded event buffers and remain responsive when
the event production rate exceeds display capacity.

### NFR-007 — Compatibility

Every persisted contract MUST declare a schema version. An unsupported version MUST fail with an
explicit compatibility error and MUST NOT be migrated automatically.

### NFR-008 — Test coverage

Configuration, coordination, planning, workflow execution, recovery, approval, and safety modules
MUST maintain at least 80 percent branch coverage.

### NFR-009 — Fault testing

Release acceptance MUST include repeatable interruption tests for process loss, optional-service
loss, event-transport loss, and—when distributed mode is released—worker and network loss.

### NFR-010 — Secret-free diagnostics

Automated tests MUST verify that representative credentials cannot enter configuration output,
events, logs, snapshots, artifacts, reports, or diffs.

## 26. Error behavior

| Code | Condition | Required result |
|---|---|---|
| ERR-CFG-001 | Effective configuration is absent, malformed, or incompatible | Refuse the requested operation; identify the invalid field or version |
| ERR-PRJ-001 | Repository/base revision or prospective-workspace/discovery revision cannot be established | Do not plan or run; report evidence failure |
| ERR-PLN-001 | Plan violates schema, organization, or policy | Reject plan with every detected violation |
| ERR-PLN-002 | Plan has no valid authorization decision, or required approval is absent | Remain awaiting decision or approval; do not execute |
| ERR-DEC-001 | Decision context or evidence is insufficient for a justified recommendation | Return an inconclusive result with assumptions and unknowns; do not change durable authority |
| ERR-DEC-002 | Required validation, independent challenge, or durable decision record is absent or failed | Keep the recommendation staged or rejected; preserve evidence and prior authority |
| ERR-POL-001 | Requested authority is not granted | Refuse before action and emit audit evidence |
| ERR-POL-002 | Equally specific and prioritized policy rules conflict | Deny without selecting a rule; expose the conflict |
| ERR-ROL-001 | Production/evaluation or orchestration/reporting conflict | Reject task assignment |
| ERR-OUT-001 | Result fails its output contract | Reject, retry within policy, then fail with evidence |
| ERR-REV-001 | Execution-context or applicable repository revision differs from the accepted plan | Block execution or completion and require reconciliation |
| ERR-RUN-001 | Run is interrupted | Preserve accepted results and expose resumable state |
| ERR-RUN-002 | Duplicate result is received | Preserve first accepted result; record and ignore duplicate |
| ERR-DEP-001 | Optional context service is unavailable | Continue only permitted degraded work and expose limitation |
| ERR-DEP-002 | Required inference or coordination dependency is unavailable | Do not start affected work; expose retryable failure |
| ERR-SEC-001 | Secret-like content reaches a persistence boundary | Block persistence and emit non-secret security evidence |
| ERR-SKL-001 | Skill package, manifest, reference, dependency, or mutation contract is invalid | Keep the skill inactive; report every detected validation failure |
| ERR-SKL-002 | Skill provenance, trust, inspection, quarantine, or activation decision is insufficient | Keep the skill inactive or preserve the prior active version; emit non-secret evidence |
| ERR-SKL-003 | Skill selection is ambiguous or incompatible with the task context | Do not apply the skill; identify the conflicting source or unmet condition |
| ERR-TOL-001 | Tool definition, schema, adapter, toolset, or registry entry is invalid | Exclude it from the accepted registry snapshot and report every detected failure |
| ERR-TOL-002 | A required tool or source is unavailable | Do not bind or dispatch it; expose the missing condition and permitted fallback if any |
| ERR-TOL-003 | Tool arguments, result envelope, or result schema is invalid | Produce no effect for invalid input; contain invalid output and fail the call contract |
| ERR-TOL-004 | Tool call failed, timed out, was cancelled, or has uncertain effect | Preserve attributable evidence; retry only when the contract and policy permit it |
| ERR-TOL-005 | Tool identity collides or a bound external schema drifted | Block the conflicting or stale binding until registry reconciliation or replanning |
| ERR-CTX-001 | Required context cannot be established without forbidden inspection or unsupported inference | Preserve unknown state; block only reasoning that requires the missing fact |
| ERR-MSN-001 | Mission Brief, executive agreement, composition, or intervention is invalid or unauthorized | Preserve current mission state; expose the missing decision, coverage, or authority |
| ERR-FIL-001 | A read/search target is outside scope, stale, unsafe, unsupported, or only partially covered | Produce no unauthorized read; return bounded attributable failure or partial evidence |
| ERR-EDT-001 | A change has a stale base, ambiguous target, scope escape, partial application, or recovery conflict | Do not claim verified completion; preserve journal, diff, and recovery evidence |
| ERR-EXE-001 | Execution cannot start, settle, cancel, satisfy isolation, or establish its external effect | Preserve output and state; report failed, lost, cancelled, timed out, or uncertain |
| ERR-WEB-001 | Web routing, network policy, retrieval, extraction, crawl, or citation evidence is invalid | Produce no unauthorized request and expose route, limitation, and safe fallback eligibility |
| ERR-BRW-001 | Browser session, target, origin, credential, action, or diagnostic evidence is invalid or stale | Refuse or re-observe; preserve uncertainty after a possible external effect |
| ERR-ART-001 | Artifact integrity, storage, validation, reference revision, retention, or recovery is invalid | Keep incomplete content unavailable and return conflict, quarantine, missing, or corrupt evidence |
| ERR-MCP-001 | MCP protocol, identity, discovery, schema, authority, transport, or completion is incompatible | Block the affected binding or call; preserve drift or indeterminate evidence |
| ERR-ENG-001 | Required engine or environment cannot be truthfully materialized or replaced compatibly | Declare degradation and block only the unsatisfied operation |
| ERR-SCH-001 | Schedule time or trigger is invalid | Reject schedule without partial creation |
| ERR-WRK-001 | Worker identity, capability, revision, or lease is invalid | Reject claim or completion and preserve evidence |
| ERR-VER-001 | Persisted schema version is unsupported | Refuse automatic mutation and identify required operator action |

## 27. Retained and proposed technical constraints

This section records implementation constraints separately from behavioral requirements. It does
not assign component responsibilities.

### TC-001 — Mandatory coordination runtime

CrewAI is the mandatory production runtime for constructing and coordinating agents, tasks, teams,
crews, and flows. MISHKAN MUST NOT ship a competing production agent-coordination runtime.

### TC-002 — Supported CrewAI generation

The implementation will use a currently supported CrewAI 1.x release. The obsolete 0.x lock is not
carried forward.

### TC-003 — Test substitution

Tests MAY substitute deterministic doubles at a runtime boundary. Such doubles MUST NOT be
selectable as a production execution mode.

### TC-004 — Interface scope

The planned product interfaces are a Python command-line and programmatic interface, one headless
control daemon, remote workers, durable chat, a separate terminal monitor, versioned HTTP/SSE, and
an MCP facade. The terminal monitor MAY issue the same policy-governed intervention commands as
other clients but MUST NOT own authoritative state or policy. No web dashboard is planned for
version 1.

### TC-005 — Local and distributed metadata

The retained D-015 direction is an embedded relational store for non-distributed operation and
PostgreSQL for distributed operation behind equivalent repository and transaction semantics.

### TC-006 — Policy-controlled migrations

The system may prepare, verify, or apply schema migrations according to explicit environment-scoped
capability policy. Migration application MUST support approval gates and MUST emit durable evidence;
it is not universally forbidden or universally enabled.

### TC-007 — CrewAI and external tool integration

Native tools use CrewAI's supported tool contract, and configured external protocol tools use its
supported integration boundary. MISHKAN may wrap these interfaces to enforce product contracts but
MUST NOT replace CrewAI's production agent tool-calling runtime.

### TC-008 — Harness and MCP transport direction

The canonical remote application surface uses versioned HTTP/OpenAPI commands and resumable SSE.
A thin MCP facade exposes the same application semantics; a local STDIO bridge delegates to the
daemon and remote MCP uses Streamable HTTP. Transport choice MUST NOT change authority.

### TC-009 — Artifact persistence direction

Local artifact metadata uses the embedded relational profile with content-addressed filesystem
blobs. Distributed artifact metadata uses PostgreSQL and a configured S3-compatible blob-store
adapter. Concrete vendors remain deployment decisions.

## Appendix A — Organization version 1 roster

The human CEO is external to the 59-identity roster. Inside the organization, PM heads Product and
Experience; CTO heads Architecture and System Design, Software Engineering, Data and AI,
Platform/Delivery/Reliability, and Security/Supply Chain. Independent Assurance, Research and
Decision Support, and Documentation and Organizational Learning remain separate responsibility
homes so mission composition cannot erase their independence.

### Executive agents

`PM`, `CTO`

### Product and Experience

`Product_Analyst`, `UX_Researcher`, `Product_Designer`, `Accessibility_Specialist`

### Architecture and System Design

`System_Architect`, `Software_Architect`, `Integration_Architect`

### Software Engineering

`Software_EngineeringLead`

#### Software Engineering Pool

`Web_Application_Engineer`, `Android_Engineer`,
`Apple_Platform_Engineer`, `Backend_Service_Engineer`, `Desktop_TUI_Engineer`,
`Systems_Software_Engineer`, `Integration_SDK_Engineer`

### Data and AI Engineering

`Data_Lead`, `Database_Engineer`, `Data_Engineer`, `AI_Engineer`

### Platform, Delivery, and Reliability

`Platform_Lead`, `Platform_Engineer`, `Delivery_Engineer`, `Reliability_Engineer`

### Security and Supply Chain

`Security_Lead`, `Product_Security_Engineer`, `Platform_Security_Engineer`,
`SupplyChain_Security_Engineer`

### Independent Assurance

`Quality_Lead`

#### Quality Evaluation Pool

`Product_Functional_Evaluator`, `Software_Technical_Evaluator`,
`Mobile_Application_Evaluator`, `Accessibility_Evaluator`,
`Performance_Resilience_Evaluator`, `Data_AI_Evaluator`, `Platform_Release_Evaluator`,

#### Security Evaluation Pool

`Application_Security_Evaluator`, `Platform_Infrastructure_Security_Evaluator`,
`Identity_Access_Security_Evaluator`, `SupplyChain_Security_Evaluator`,
`Data_AI_Security_Evaluator`

#### Mission Reporting Pool

`Product_Delivery_Reporter`, `Technical_Change_Reporter`, `Incident_Operations_Reporter`

#### Evidence Audit

`Evidence_Auditor`

### Research and Decision Support

`Research_Clarificator`, `Research_Formulator`, `Research_Investigator`,
`Research_Synthesizer`, `Research_Evaluator`, `Research_Reporter`

### Documentation and Organizational Learning

#### Documentation Pool

`Product_Documentation_Specialist`, `Developer_Documentation_Specialist`,
`Architecture_Documentation_Specialist`, `Operations_Documentation_Specialist`,
`Security_Documentation_Specialist`

#### Curation

`Knowledge_Curator`, `Skill_Curator`

The five named pools have exactly the memberships shown above. Pool membership narrows a reusable
responsibility home; it does not grant tools or authority and does not create a static mission team.
The roster contains exactly 59 persistent identities.

## Appendix B — Optional mission-template contract

A template MAY provide:

- qualified identity, version, source, and provenance;
- applicability signals and exclusions;
- reusable objective, constraint, risk, evidence, and completion guidance;
- compatible organization versions and required responsibility classes;
- declared configuration, skills, tools, or execution conditions without granting them;
- validation and reporting expectations.

A template MUST NOT contain a mandatory task graph, fixed Mission Crew, implicit approval,
repository-independent path or command assumption, or static role/tool assignment. Absence of a
matching template MUST NOT prevent a valid free-form mission.

## Appendix C — PRD traceability

| PRD item | Principal SRS coverage |
|---|---|
| UC-01 Establish organization | PRJ-001–010, ORG-001–003 |
| UC-02 Delegate objective | PLN-001–011, PLN-020–021, RUN-001–013 |
| UC-03 Coordinate work | ORG-004–019, MSN-001–008, MSN-016–022, RUN-003–007, RUN-013 |
| UC-04 Enforce authority | SAF-001–013 |
| UC-05 Review evidence | MSN-009–015, OBS-001–008, RUN-012 |
| UC-06 Survive interruption | RUN-008–011, OBS-003 |
| UC-07 Preserve knowledge and grow skills | KNW-001–006, SKL-001–025 |
| UC-08 Operate headlessly | AUT-001–007 |
| UC-09 Use remote capacity | DST-001–010 |
| UC-10 Extend controlled capabilities | CTX-004, TOL-001–027, ENG-001–013 |
| UC-11 Make an evidence-based engineering decision | PLN-012–019, ORG-005–007 |
| UC-12 Govern adaptive mission | PRJ-008–010, PLN-020–021, ORG-011–014, MSN-001–008, MSN-016, ENG-009–013 |
| UC-13 Converse, inspect, intervene | MSN-009–015, SYS-006 |
| UC-14 Use external harness | MCP-001–009, SYS-006, TC-008 |
| UC-15 Evolve professional capability | ORG-015–016, SKL-013–024 |
| UC-16 Form and improve an effective Mission Crew | ORG-017–019, MSN-017–022, RUN-013 |
| SC-01 Repository adaptation | CTX-001–008, PRJ-003–010, ORG-012 |
| SC-02 End-to-end delegation | PLN-001–021, ORG-001–019, MSN-001–008, MSN-016–022, RUN-001–013 |
| SC-03 Human control | SAF-003–013 |
| SC-04 Recovery | RUN-008–010 |
| SC-05 Degraded usefulness | KNW-005, WEB-007, ENG-008, ERR-DEP-001 |
| SC-06 Auditability | PLN-008–011, MSN-009–015, OBS-001–008 |
| SC-07 Headless continuity | AUT-001–007 |
| SC-08 Distributed recovery | DST-001–010, post-core |
| SC-09 Local operation | NFR-001–003 |
| SC-10 Skill learning loop | SKL-011–024 |
| SC-11 Controlled tool extension | TOL-003–027 |
| SC-12 Decision-quality assistance | PLN-012–019, ERR-DEC-001–002 |
| SC-13 Adaptive mission operation | PLN-021, ORG-001–016, MSN-001–008, MSN-016, ENG-009–013 |
| SC-14 Executive transparency and intervention | MSN-009–015, OBS-001–008 |
| SC-15 Native engineering execution | FIL-001–007, EDT-001–008, EXE-001–008, ENG-001–013 |
| SC-16 External harness integration | MCP-001–009, SYS-006, TC-008 |
| SC-17 Artifact integrity | ART-001–008 |
| SC-18 Evidence-based agent evolution | ORG-015–016, SKL-013–024, ART-001–008 |
| SC-19 Adaptive Mission Crew effectiveness | ORG-017–019, MSN-017–022, RUN-013 |
