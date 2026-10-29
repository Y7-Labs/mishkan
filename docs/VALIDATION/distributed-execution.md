# I09 Distributed Execution — Validation Evidence

**Status:** Passed — D-050
**Observed:** 2026-10-29
**Branch:** `feat/i09-distributed-execution`
**Accepted checkpoint:** `00f8ce6`
**GitHub Actions:** [run 40803467661](https://github.com/Y7-Labs/mishkan/actions/runs/40803467661)

## Gate result

I09 is accepted on its topic branch. The final GitHub Actions run passed all eight jobs:

- Python 3.11, 3.12, and 3.13 on Linux;
- Python 3.11, 3.12, and 3.13 on macOS;
- the retained Linux gate for real governed Docker, Compose, Dev Container, and Podman adapters;
- the I09 PostgreSQL, S3-compatible artifact, and two-worker recovery gate.

The local deterministic suite passed without a functional failure. A first branch-coverage run
passed all 862 collected tests but correctly refused the quality gate at 79.99 percent. The gate
was not rounded and its threshold was not weakened. Tests were added for the real
`mishkan-worker` entrypoint, enrollment-secret refusal, rotation, `once` and `run` behavior,
pre-model input refusal, and expired task deadlines. The resulting coverage union passed at 80.22
percent overall and 83.49 percent across `mishkan.workers`; the final remote matrix reran the
complete tree from the accepted checkpoint. Ruff, formatting, strict mypy, deterministic schema
export, source/wheel builds, and `git diff --check` also pass.

## Distributed authority and storage

The accepted implementation proves the DST-001–010 boundary:

- distributed mode is explicit configuration schema 1.8 and refuses SQLite metadata or local-only
  artifact bodies;
- PostgreSQL owns shared authoritative metadata while S3-compatible object storage owns immutable
  artifact bodies; local modes retain SQLite/WAL and filesystem CAS;
- `mishkand` remains the sole planning, policy, placement, validation, and durable-acceptance
  authority;
- workers enroll through short-lived single-use credentials, own their private keys, and receive
  individually renewable and revocable mTLS certificates;
- the daemon's pinned HTTPS protocol adapter exposes only TLS-verified peer certificates to worker
  routes; enrollment may be anonymous at TLS level but still requires its one-time credential;
- workers advertise bounded capabilities, resources, active leases, and monotone heartbeats;
- placement matches only compatible workers and produces immutable envelopes containing the task,
  role, plan, policy, repository/context revisions, inputs, scopes, resources, and deadline;
- PostgreSQL claim serialization allows only one winner during a real concurrent race;
- bounded renewable leases make lost work recoverable without making a stale completion valid;
- completion delivery may repeat, but durable task-result acceptance is unique and atomically
  recorded with the idempotent application command and event;
- worker availability is a bounded projection (`never_seen`, online, stale, lost, or revoked), not
  an inference from identity existence.

CrewAI remains the sole internal production agent/task runtime. A remote worker executes one
already accepted task through a bounded CrewAI crew with no planning, delegation, acceptance, or
tool-authority expansion. MISHKAN validates the returned structured result and requires a separate
independent review before the coordinator accepts it.

## Results, effects, and recovery

The worker runtime journal persists claim identity, lease secret, structured execution, uploaded
artifact mapping, and completion submission with private file permissions. Tests prove that a lost
claim, upload, or completion response is retried with the same durable identity without rerunning
CrewAI after a result exists.

Generated output, unified patch, and effect-evidence bodies are uploaded through the authoritative
Artifact service and bound to the active lease. The daemon verifies size, digest, provenance,
worker, envelope, repository revision, execution-context revision, and artifact ownership before a
completion can be delivered. Duplicate or stale completion records remain observable but cannot
replace the one accepted result.

Workers do not commit, push, deploy, or migrate. A stateful effect requires an exact
coordinator-issued capability and public policy decision; the shipped worker executor exposes no
such stateful capability and therefore refuses such envelopes. Uncertain effects are reconciled by
the coordinator before any retry rather than being silently replayed.

## Gate scenarios

The tests cover:

- empty, local, and distributed database configuration plus explicit Alembic migrations;
- PostgreSQL-backed command/event idempotence and S3-compatible artifact bodies;
- single-use enrollment, expiry, rotation recovery, individual revocation, weak key refusal, and
  verified peer-certificate transport;
- heartbeat ordering, bounded availability, capability matching, concurrency limits, and
  incompatible placement refusal;
- immutable envelope fingerprints, revision mismatch, deadline/timeout bounds, and input digest
  verification;
- concurrent claims, renewal, lease expiry, worker loss, task recovery, stale completion, duplicate
  delivery, and exactly-once accepted result semantics;
- interrupted artifact upload and completion delivery without duplicate CrewAI execution;
- daemon, SDK, CLI, schema, snapshot, and public-policy surfaces for worker administration,
  placement, status, and completion acceptance.

No hidden worker authority, private operational deny-list, alternative agent runtime, distributed
SQLite mode, automatic migration, static workflow, or I10 TUI scope was introduced.

## Decision

D-050 accepts I09 at `00f8ce6` based on the complete local and eight-job remote evidence above.
I09 may be promoted from its topic branch to `develop`. I10 remains unauthorized until an explicit
engineer decision after the I09 integration gate.
