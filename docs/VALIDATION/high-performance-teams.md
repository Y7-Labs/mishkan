# I06-HPT High-Performance Mission Crews — Validation Evidence

**Status:** Passed — D-052
**Observed:** 2026-10-29
**Branch:** `feat/i06-high-performance-teams`
**Accepted checkpoint:** `53286ec`
**GitHub Actions:** [run 40827168804](https://github.com/Y7-Labs/mishkan/actions/runs/40827168804)

## Gate result

I06-HPT is accepted on its topic branch. The deterministic local suite passed 888 tests with 14
explicitly external gates deselected. A final focused regression passed 64 tests, followed by the
execution-location invariant. Ruff, formatting, strict mypy across 255 source files, exported
schemas, builds, and `git diff --check` passed.

The branch-aware focused gate reports 89 percent for the CrewAI evaluator, 87 percent for the team
contracts, and 81 percent for the durable repository, for 86 percent across the extension's three
core modules. The remote matrix passes Python 3.11–3.13 on Linux and macOS and retains the real I05
environment-adapter and I09 PostgreSQL/S3 two-worker recovery jobs.

A real local CrewAI/Ollama acceptance used the tracked 16K-context Modelfile for
`qwen2.5-coder:1.5b`. CrewAI interpreted attributable mission evidence without a composite score.
MISHKAN validated the model output, stamped authoritative evidence references, normalized any
omitted dimension only as `uncertain`, and retained an explicit limitation. Invalid model output
still settles as an uncertain evaluation rather than fabricated health.

## Crew formation and collaboration

The accepted implementation proves:

- simple production work retains one accountable owner plus downstream independent assurance;
- consultative and multi-producer shapes require explicit reasons, interaction edges, ownership,
  and visible communication budgets;
- current competence, availability, authority, conflict, and independence gates remain
  deterministic, while CrewAI supplies attributable composition proposals and alternatives;
- direct recent collaboration history may be preferred, partial history is only a tie-breaker, and
  stale, conflicting, materially different, or unproven history is ignored;
- shared Context Pack and handoff contracts preserve results, decisions, changed assumptions,
  uncertainty, failures, unresolved questions, next actions, and acceptance conditions.

No universal crew size, workflow, role/tool matrix, hidden ranking, or composite performance score
was added. The canonical organization remains exactly 59 persistent professional identities;
Mission Crews remain temporary and contextual.

## Health, epistemic safety, and adaptation

Cheap deterministic detectors cover queue and blocked time, rejections, rework, duplicate work,
missing handoffs, context drift, and workload concentration. They do not call a model in the task
completion path. Independent milestone evaluation covers outcome, stability, flow, coordination,
epistemic safety, learning, and resource efficiency.

Tests preserve contradictory evidence, uncertainty, failure, and limitations without converting
them into profile penalties. Model failure produces `uncertain`. Identical authoritative events
produce identical team evidence whether execution metadata names the local daemon or a remote
worker; execution location does not become a second source of organizational truth.

Bounded adaptations may repair context, request consultation or challenge, reshape already
authorized tasks, reduce parallelism, reassign within the accepted crew, or pause dependent work.
Adding members, changing independence, altering material scope, expanding authority, or accepting
consequential risk requires an accepted PM, CTO, or CEO decision. A merely accepted decision by a
non-executive identity is rejected.

## Retrospectives and professional learning

Collaboration plans, health evaluations, signals, handoffs, adaptation decisions, and
retrospectives are durable and evented. Retrospective authors must hold independent reporting or
audit responsibility. Their improvement entries remain proposals and cannot activate themselves.

Professional evidence now distinguishes an individually attributable result from a team aggregate.
Aggregate team health can inform a proposal but cannot promote an individual's competence or
expand its effective scope. Existing I05–I07 promotion authorities remain unchanged.

## Public authority and decision

CLI, SDK, HTTP queries, application commands, snapshots, and SSE project the same daemon-owned
state. All mutations retain the existing idempotent command boundary and public policy. CrewAI
remains the sole internal production coordination runtime; MISHKAN remains authoritative for
eligibility, policy, evidence, state, adaptation boundaries, and acceptance.

D-052 accepts I06-HPT at `53286ec`. The extension may be promoted topic-to-`develop`. I10 remains
unauthorized until an explicit engineer decision after promotion.
