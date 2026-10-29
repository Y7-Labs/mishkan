# MISHKAN Product Requirements Document

**Status:** Accepted by D-032; amended by D-051 on 2026-10-29
**Version:** 1.5
**Scope:** Product problem and outcomes only

**Prior approvals:** 2026-08-23 by Y4NN777; amendments through 1.3 approved 2026-08-24

Version 1.4 replaces the historical fixed 32-identity and 15-outcome assumptions with an explicit
59-identity professional organization, free-form missions, and optional versioned mission
templates. It also incorporates the validated engineering-context, native-capability, skills,
external-harness, communication, intervention, agent-evolution, and mission-scoped environment
decisions in WD-001–WD-028.

Version 1.5 adds evidence-based Mission Crew formation, epistemic safety, multidimensional team
effectiveness, contextual collaboration history, bounded adaptation, and governed retrospectives.

## 1. Problem

Software engineers increasingly delegate parts of software work to AI assistants, but those
assistants commonly operate as isolated, short-lived sessions. Their behavior, knowledge, quality
controls, and access boundaries vary by tool and disappear when the session ends.

This creates six observable problems:

1. Complex engineering outcomes must repeatedly be decomposed and coordinated by the engineer.
2. Work context, decisions, and lessons are lost or become difficult to recover between sessions.
3. The same actor may generate, evaluate, and summarize its own work, weakening independent
   quality control.
4. Permission boundaries are often expressed as instructions rather than enforceable rules,
   exposing repositories and external systems to unintended state changes.
5. Long-running or multi-project work is difficult to resume, audit, operate unattended, or
   distribute without binding the engineer to one interactive development tool.
6. Engineering recommendations can hide assumptions, omit credible alternatives, or present a
   confident choice without enough project evidence, validation, or explanation for the engineer
   to judge architecture, data, security, scalability, and other cross-disciplinary consequences.

MISHKAN exists to provide a persistent, inspectable, and safety-constrained software engineering
organization that accepts engineering objectives, organizes the work, preserves context, and
returns reviewable results while the engineer retains control over consequential actions.

## 2. Target users

### 2.1 Primary user: human CEO and software engineer

An engineer working alone or in a small team who acts as the organization's human CEO: they state
problems, goals, priorities, and strategic constraints; confer primarily with the permanent PM and
CTO agents; and delegate multi-step work without surrendering durable authority.

### 2.2 Secondary user: engineering lead

A lead who needs consistent role separation, evidence, progress visibility, and reusable knowledge
across projects or contributors.

### 2.3 Operational actor: system operator

The person responsible for configuring execution resources, credentials, schedules, retention,
and remote capacity. This may be the same person as the engineer.

## 3. Main use cases

### UC-01 — Establish the organization for a repository

The engineer can introduce MISHKAN to an existing repository. The system discovers the repository's
actual characteristics, identifies relevant disciplines and constraints, and proposes an initial
work context without assuming that all projects share the same structure.

### UC-02 — Delegate an engineering objective

The engineer can submit an outcome such as investigating a defect, designing a change, implementing
a feature, reviewing architecture, preparing a release, or researching an unknown. The system
turns that outcome into an explicit, reviewable plan appropriate to the repository or prospective
workspace.

Each run is anchored either to one recorded repository revision or, before a greenfield repository
exists, to one versioned prospective workspace context. A single run never receives implicit
authority over multiple repositories. The effective policy decides whether a plan may execute
automatically, requires interactive approval, or must be rejected. Plans may adapt during execution
inside pre-authorized boundaries; material changes outside those boundaries pause for a policy
decision or engineer approval.

### UC-03 — Coordinate specialized work

The system assigns production, evaluation, reporting, research, documentation, and coordination
responsibilities to distinct organizational roles. Relevant roles collaborate and exchange
artifacts without requiring the engineer to manually coordinate every handoff.

### UC-04 — Enforce human authority

The engineer can define what the organization may read, write, execute, or access. Capabilities are
explicit, versioned, inspectable, and configurable by scope. The same mechanism can deny an action,
allow it unattended, or require approval according to its context and risk.

Within an authorized scope, the organization may edit files and perform state-changing operations,
including routine source-control work, when policy grants those capabilities. Every such operation
must remain attributable and reviewable; agents cannot grant themselves additional authority.

### UC-05 — Review evidence and progress

The engineer can observe current work, decisions, failures, security findings, resource health,
and completed outputs. The system provides enough evidence to understand what happened and why.

### UC-06 — Survive interruption

Work can continue after a process, machine, service, or network interruption without discarding
already accepted results or silently accepting the same completion more than once.

### UC-07 — Preserve knowledge and grow reusable skills

The organization can recall recent context, retrieve project knowledge, understand repository
structure, and discover portable procedural skills without loading irrelevant instructions. It can
learn skill improvements from explicit teaching, repeated misses, and reviewed execution evidence.
Creation, activation, sharing, mutation, archival, and restoration remain visible, attributable,
recoverable, and governed by effective policy.

### UC-08 — Operate headlessly

The engineer can run approved work immediately or on a schedule without keeping an interactive IDE
session open. Headless operation obeys the same safety and review boundaries as interactive work.

### UC-09 — Use available execution capacity

The operator can run the organization on one machine or distribute eligible work across approved
machines while preserving identity, authorization, revision consistency, and accepted-result
semantics.

### UC-10 — Extend controlled capabilities

The operator can register, inspect, group, enable, disable, and update atomic capabilities supplied
by MISHKAN or configured external sources. The engineer can see exactly which capability versions
a role and task may use. Availability never implies authorization, and a newly discovered
capability never silently expands an accepted plan.

### UC-11 — Make an evidence-based engineering decision

The engineer can ask the organization to clarify a consequential engineering choice. The system
uses the actual repository, requirements, constraints, preferences, and current authoritative
sources to identify credible alternatives; compare their relevant trade-offs; distinguish evidence
from assumptions and unknowns; and return a recommendation or an explicit inconclusive result.

The result identifies risks, confidence limits, validation evidence, and reversal or migration
consequences in enough detail for the engineer to judge the choice. A recommendation does not
silently become project authority merely because an AI actor produced it.

### UC-12 — Govern an adaptive mission

The CEO, PM, CTO, or authorized organizational evidence can originate a mission for an existing
repository, greenfield product, multi-repository system, research question, incident,
modernization, platform capability, or operational change. PM and CTO jointly clarify the desired
outcome, produce a Mission Brief, and compose only the temporary Mission Crew required by the
project, risks, evidence, competence, availability, and independence needs. Optional versioned
templates may guide recurring mission classes but never restrict admissible objectives or embed a
universal task chain. Planning also makes an explicit execution-environment decision for the
mission: reuse what the project already establishes, use an authorized native location, generate
or propose the smallest reproducible environment description, or expose why no honest environment
can yet be provided.

### UC-13 — Converse, inspect, and intervene

The CEO can converse durably with PM and CTO, enter any authorized mission or branch channel, and
drill down from organization-wide status to plans, tasks, agents, decisions, evidence, artifacts,
risks, failures, costs, schedules, and events. The CEO can rarely comment, answer an escalation,
accept or reject a proposal, suspend, resume, request or confirm reassignment, stop work, or accept
a risk. Every intervention uses the same configured authorization and evidence rules regardless of
client.

### UC-14 — Use MISHKAN from an external harness

Codex, Claude, and other compatible harnesses can inspect authorized organization metadata and
submit an organizational objective, targeted task, direct-agent request, skill operation, or
intervention through versioned machine interfaces. MISHKAN converts work requests into governed
CrewAI runs and never exposes raw internal agents, policy state, credentials, or a bypass around
accepted plans and effects.

### UC-15 — Evolve professional capability

Persistent agents can improve project knowledge, demonstrated competence, tool mastery, memory,
and procedural skills from attributable execution and evaluation evidence. Corrections may be
applied live and reversibly when policy allows; broader durable promotion depends on evidence,
scope, trust, and risk. An agent cannot expand its own authority, change its independence, erase
failure evidence, or self-certify critical mastery.

### UC-16 — Form and improve an effective Mission Crew

PM and CTO can choose the smallest production shape sufficient for a mission, explain why
collaboration is needed, and establish explicit responsibilities, decision rights, interaction
paths, budgets, escalation conditions, and stopping conditions. During the mission, the
organization exposes outcome, flow, stability, coordination, learning, and resource evidence
without reducing effectiveness to one score.

Agents can report uncertainty, disagreement, contradictory evidence, a need for help, or failure
without that report automatically damaging their professional profile. The organization can make
bounded corrections inside the accepted mission and can propose larger crew or process changes
through the existing PM, CTO, and CEO governance paths. Comparable collaboration history may
inform a later mission, but current mission fit and evidence remain decisive.

## 4. Out of scope

MISHKAN will not:

1. Replace the engineer's IDE, source-control host, deployment platform, or incident-management
   system.
2. Bypass the configured authorization policy or the protections of an external source-control,
   deployment, release, or data platform.
3. Become a hosted software-as-a-service product or require a vendor-hosted control plane.
4. Provide a web dashboard.
5. Operate a hosted marketplace or treat third-party skills as trusted merely because they are
   publicly available.
6. Guarantee correctness solely because an AI actor declared its own output correct.
7. Hide failed, degraded, retried, rejected, or security-relevant actions from the engineer.
8. Treat every repository as having the same teams, technologies, paths, or task sequence.
9. Target Windows in the initial product scope.
10. Limit missions to a mandatory finite catalogue or treat a mission template as a static
    workflow.
11. Replace CrewAI with an interchangeable internal agent runtime or permit an external harness to
    become that runtime.
12. Treat a known, installed, healthy, or recommended engine as authorized or usable without a
    concrete adapter and an applicable policy decision.

## 5. Product principles

### PP-01 — Engineer authority

The system may produce plans, analysis, source changes, tests, documentation, review artifacts, and
authorized state changes. The engineer controls the policy that grants, scopes, or approval-gates
consequential operations.

### PP-02 — Independent evaluation

Production and evaluation are different responsibilities. Reporting is also independent from the
actor making orchestration decisions.

### PP-03 — Execution-context-specific planning

A free-form objective or optional mission template may be reused, but its execution plan adapts to
the repository or prospective workspace, requested result, available capabilities, and approved
policy.

### PP-04 — Inspectable operation

Plans, decisions, events, results, failures, approvals, and provenance are available for review.

### PP-05 — Durable progress

Accepted work and decisions survive interruption. Resumption is explicit and reproducible.

### PP-06 — Local ownership

The product can provide its core value without requiring a paid hosted control plane.

### PP-07 — Enforced boundaries

Safety rules are enforced at system boundaries and cannot depend solely on instructions given to an
AI actor.

### PP-08 — Progressive knowledge

The organization uses the least expensive adequate source of context and promotes reusable
knowledge only through visible provenance and approval.

### PP-09 — Skills are procedural memory

A skill is a portable, inspectable procedure that helps an organizational role perform a class of
work. Skills enrich authorized CrewAI work; they do not replace the organization, the
execution-context-specific plan, the coordination runtime, or deterministic capability enforcement.

### PP-10 — Tools are typed capabilities

A tool is one inspectable atomic capability with declared inputs, outputs, effects, dependencies,
and provenance. Tools are exposed to organizational roles only through the effective organization,
plan, and policy; their implementation source does not grant additional authority.

### PP-11 — Evidence before recommendation

The organization derives decision criteria from the objective and project context rather than a
hidden universal scorecard. It explains alternatives, evidence, assumptions, uncertainty,
trade-offs, and validation before asking the engineer to accept a consequential choice.

### PP-12 — Permanent organization, temporary crews

Professional identities, responsibility homes, competence, and learning persist. Mission Crews
are temporary contextual compositions and activate only the identities needed for the concrete
mission.

### PP-13 — One authoritative application state

CLI, SDK, chat, TUI, HTTP/SSE, MCP, schedules, and external harnesses operate on the same durable
missions, conversations, commands, policy decisions, and evidence. No client owns a separate truth
or stronger authority.

### PP-14 — Native power with explicit effects

MISHKAN preserves practical native file, editing, shell, browser, Web, artifact, MCP, Git, and
engineering-tool behavior. Rich execution never creates ambient authority: actual paths,
destinations, credentials, resources, and effects are resolved and governed at execution time.

### PP-15 — Truthful capability and degradation

Inventoried, detected, installed, executable, authenticated, healthy, project-used, eligible, and
authorized are distinct facts. A compatible fallback declares lost precision or evidence; an
incompatible fallback blocks only the affected operation rather than fabricating success.

### PP-16 — Mission-scoped environments

Development and execution environments are contextual mission inputs and outputs, not universal
project templates. Native, isolated, containerized, declarative, or other reproducible
representations are selected from actual project, platform, isolation, and verification needs.
Existing project configuration is preferred when compatible; generated descriptions remain
attributable, reviewable, testable, and governed like other project changes.

### PP-17 — Evidence-based team effectiveness

MISHKAN prefers the smallest sufficient production shape and measures team effectiveness across
several contextual dimensions. It preserves dissent, uncertainty, failure, and contradictory
evidence as useful mission evidence; it does not infer agent emotions, create a universal team
score, or treat activity volume as productivity. Collaboration history informs but never silently
determines crew composition or professional advancement.

## 6. Success criteria

The product succeeds when all of the following outcomes can be demonstrated:

### SC-01 — Repository adaptation

Given materially different repositories, initialization produces materially different relevant
work plans while preserving the same organizational safety rules.

### SC-02 — End-to-end delegation

An engineer can submit a multi-discipline engineering objective and receive production artifacts,
an independent evaluation, and a structured report without manually coordinating each handoff.

### SC-03 — Human-control enforcement

In an adversarial acceptance suite, each consequential operation is denied, approval-gated, or
executed exactly as the effective policy specifies. No actor can exceed its granted capability, and
every decision and resulting state change is visible to the engineer.

### SC-04 — Recovery

After interruption at every supported work boundary, execution resumes without losing accepted
results and without accepting a duplicate result.

### SC-05 — Degraded usefulness

Loss of optional context or knowledge services does not prevent safe work that can proceed without
them; the loss and resulting limitations are visible.

### SC-06 — Auditability

For any completed or failed run, the engineer can identify the accepted plan, participating roles,
inputs, outputs, evaluations, policy decisions, retries, failures, approvals, and final status.

### SC-07 — Headless continuity

An approved scheduled objective can start and complete without an open interactive development
session, and the engineer can inspect the result afterward.

### SC-08 — Distributed recovery

Eligible work can execute on more than one approved machine and recover from the loss of one machine
without accepting a stale or duplicate completion.

This is a post-core product milestone. It is not required to declare the first local product slice
usable.

### SC-09 — Local operation

The core organizational workflow can complete on one supported local machine without requiring a
paid external inference or hosted data service.

### SC-10 — Skill learning loop

After an explicit teaching request or repeated recorded inability to perform a task class, the
organization can produce a staged skill proposal, obtain the policy-required review, activate the
accepted version with provenance, use it through progressive disclosure, improve it from reviewed
evidence, and restore a prior version after a rejected update.

### SC-11 — Controlled tool extension

After a configured tool source is added or changed, the operator can inspect the discovered
catalogue and differences, compose an exact toolset, and make it available to an eligible task.
Calls outside the resolved set or policy scope are refused, while an accepted call has validated
input, attributable effects, a validated result, and durable non-secret evidence.

### SC-12 — Decision-quality assistance

For a representative structural engineering choice with multiple credible alternatives, the
organization produces a reviewable comparison tied to repository and requirement evidence,
identifies assumptions and unknowns, recommends one option or declares the evidence insufficient,
states confidence and relevant consequences, proposes a validation method, and receives an
independent challenge before the choice can become accepted authority.

### SC-13 — Adaptive mission operation

For materially different greenfield and existing-project objectives, PM and CTO produce different
Mission Briefs, Crew compositions, plans, tools, and evidence requirements while preserving the
same organization, authority, separation, and acceptance rules. Each mission records whether its
execution environment is reused, generated, proposed, native, or unresolved; when generation is
needed, the resulting descriptor set is specific to the mission and is verified on its intended
execution location before dependent work is accepted.

### SC-14 — Executive transparency and intervention

After a client disconnect and control-process restart, the CEO can recover the Executive
conversation, inspect an active mission down to its evidence, answer an escalation, and issue an
authorized pause or resume whose state and attribution are identical through TUI and API.

### SC-15 — Native engineering execution

The organization can inspect, modify, test, diagnose, document, and operate representative
projects through general native capabilities and project-discovered commands without creating one
synthetic tool per command or silently claiming a missing engine succeeded.

### SC-16 — External harness integration

A compatible harness can create or inspect governed work through HTTP or MCP, resume its events,
and receive structured results without bypassing CrewAI, policy, identity separation, or accepted
result semantics.

### SC-17 — Artifact integrity

Large and rich outputs remain inspectable as immutable artifacts with provenance. Concurrent
updates to one scoped working reference detect conflict rather than silently overwriting a newer
revision, and interrupted storage can be reconciled without inventing content or provenance.

### SC-18 — Evidence-based agent evolution

An evaluated mission can produce a scoped reversible improvement to an existing skill or agent
profile, use it on a later safe action when policy permits, promote it only to the justified scope,
and restore the prior version while preserving failure and evaluation evidence.

### SC-19 — Adaptive Mission Crew effectiveness

Representative mission fixtures demonstrate that a single owner is selected when collaboration
adds no justified value, while cross-disciplinary or assurance-sensitive work receives an explicit
collaboration plan. During execution, broken handoffs, duplicate work, context drift, blocked flow,
and unacknowledged risk become visible; bounded correction preserves authority; material crew
changes follow executive governance; and the retrospective produces attributable proposals rather
than self-promoting organizational changes.

## 7. Product boundaries requiring later specification

The PRD intentionally does not decide:

- How plans are represented or executed.
- Which components own orchestration, persistence, security, or scheduling.
- Which libraries, databases, protocols, or process model are used.
- Exact performance thresholds.
- Exact machine-readable organization, mission-template, conversation, intervention, capability,
  and professional-profile schemas.
- Exact configuration, event, report, skill, artifact, or worker schemas.

Those decisions follow only after this product problem and scope are approved.

## 8. Product decision baseline accepted by D-032 and amended by D-051

This list combines retained prior decisions with the amendments accepted in versions 1.4 and 1.5.
The combined baseline is authoritative under D-032 and D-051.

1. One run binds one execution context: one repository and immutable base revision, or one
   prospective workspace before a greenfield repository exists. Authorized establishment and later
   revisions retain lineage; multi-repository missions coordinate multiple context-bound runs.
2. Plan execution and revision are risk- and policy-bound. Matching work may proceed unattended;
   only boundary crossings or policy-designated actions require interactive approval.
3. Stateful capabilities, including commit and push, are configurable rather than universally
   forbidden. When granted, they are narrowly scoped, attributable, and reviewable.
4. Distributed execution follows proof of local operation and is not a first-slice acceptance gate.
5. Organization version 1 contains the explicitly approved 59 persistent identities. PM and CTO
   are permanent agents; `Mission_Lead` is a temporary responsibility assigned to an existing
   identity. Evolution requires a new versioned organization decision and never occurs silently
   inside a running mission.
6. MISHKAN provides a first-class, Hermes-inspired skills system for portable procedural memory,
   progressive disclosure, learning from experience, reviewed evolution, provenance, and
   recoverable lifecycle management. It does not operate a hosted marketplace or treat skills as a
   competing plugin runtime.
7. MISHKAN provides a first-class tool system for native and externally supplied atomic
   capabilities. Its registry, toolsets, discovery, lifecycle, schemas, and runtime availability
   are versioned and inspectable; effective access remains plan- and policy-bound.
8. MISHKAN provides evidence-based assistance for consequential engineering choices. It derives
   relevant criteria from project context, compares credible alternatives, exposes evidence,
   assumptions, unknowns, trade-offs, risks, confidence, validation, and reversibility, and keeps
   durable acceptance under engineer-controlled authority.
9. Missions accept free-form objectives. Optional configured templates supply reusable intent,
   constraints, evidence, and completion guidance but never a mandatory catalogue or task graph.
10. Codex, Claude, and compatible harnesses are governed clients of MISHKAN. CrewAI 1.x remains
    the sole internal production runtime for agents and teams.
11. The operational TUI is a transparent client of the same application commands and may issue
    authorized interventions; it does not own policy, coordination, or authoritative state.
12. Skills use concrete `SKILL.md` packages and may be corrected or maintained unattended when
    effective policy allows. Newly discovered community extensions remain proposals until their
    lifecycle action is explicitly authorized.
13. Mission Crews use the smallest sufficient production shape, explicit collaboration plans,
    multidimensional effectiveness evidence, epistemic-safety guarantees, independently evaluated
    health, bounded adaptation, and governed retrospectives. No universal team size, composite
    score, leaderboard, or automatic individual-competence penalty is permitted.
