# I08 Persistent Headless Scheduling — Validation Evidence

**Status:** Passed — D-048
**Observed:** 2026-10-28
**Branch:** `feat/i08-persistent-scheduling`
**Accepted checkpoint:** `39f04c3`
**GitHub Actions:** [run 40771146216](https://github.com/Y7-Labs/mishkan/actions/runs/40771146216)

## Gate result

I08 is accepted on its topic branch. The final GitHub Actions run passed all seven jobs:

- Python 3.11, 3.12, and 3.13 on Linux;
- Python 3.11, 3.12, and 3.13 on macOS;
- the retained Linux gate for real governed Docker, Compose, Dev Container, and Podman adapters.

The final macOS/Python 3.13 job passed 764 tests, skipped 11 unavailable external fixtures, reached
80.22 percent branch-aware coverage, and passed Ruff, formatting, strict mypy, deterministic schema
export, and source/wheel builds. The local complete gate passed 760 tests, skipped eight unavailable
fixtures, and reached 80.27 percent branch-aware coverage. Focused scheduling, daemon, recovery,
security, and export tests also passed after the final corrections. The accepted implementation
retains the existing daemon-startup and sustained event-ingestion performance gates.

One first macOS/Python 3.13 attempt ran for 20 minutes on a saturated runner and missed the event
rate gate by 3.6 events per second while a local `git rev-parse` exceeded a fixed ten-second bound.
Both exact tests passed together under local Python 3.13, and an isolated GitHub rerun passed the
complete 764-test job. A later macOS job reproduced the Git timeout and proved it was a real
configuration-boundary defect rather than only runner contention. Checkpoint `39f04c3` makes every
Git identity, state, effect, and evidence probe use the request's public policy-governed timeout.
The final seven-job matrix passed without weakening the durability or performance thresholds.

## Durable scheduling authority

Tests and runtime evidence prove the AUT-001–007 boundary:

- schedules are versioned durable records owned by `mishkand`, not APScheduler job-store state;
- one-shot instants and five-field cron expressions require an IANA timezone and preserve their
  exact UTC occurrence identity;
- APScheduler is only the daemon-local wake-up clock over SQLite-authoritative state;
- every occurrence has a deterministic command identity, durable attempt history, and idempotent
  run submission through the existing application command authority;
- create, update, pause, resume, remove, run-now, trigger, and history use the same authenticated,
  revision-checked command surface as the SDK and CLI;
- due occurrences are coalesced within public catch-up bounds, misfires use the configured grace
  policy, and transient failures use bounded exponential retry;
- overlap decisions are made from the durable schedule target and public policy; the daemon-local
  check-and-claim boundary is serialized so concurrent dispatches cannot both pass before either
  claim becomes visible;
- restart reconciliation resumes pending occurrences, reconciles interrupted dispatches from the
  durable command receipt, and never blindly repeats an uncertain effect;
- bounded history is enforced by the public scheduling configuration across repository queries,
  HTTP queries, snapshots, SDK, and CLI.

CrewAI remains the sole internal production agent coordinator. A schedule selects an accepted
mission template or explicit mission input and asks the same MISHKAN authority to create a run; it
does not introduce a static workflow or an alternative runtime.

## OS scheduler exports

The CLI exports cron, systemd, and launchd definitions that only wake the local control plane by
calling `mishkan schedule trigger`. They do not invoke a mission or provider directly, and therefore
retain authentication, idempotence, policy, overlap, misfire, and event semantics.

Cron exports normalize one-shot instants to UTC. Systemd carries the declared timezone when its
calendar format supports it. Launchd uses a bounded wake interval because its calendar dictionary
cannot represent a per-job IANA timezone; `mishkand` remains responsible for deciding whether the
persisted schedule is due. Exported definitions contain token-file references rather than bearer
token values.

## Gaps found and closed by the gate

The final review found and closed these implementation or proof gaps:

- pending occurrences created before a daemon interruption were durable but were not enqueued on
  restart; reconciliation now resumes them and has a regression test;
- two concurrent local dispatch tasks could both observe no active overlap before either durable
  claim; a daemon-local atomic check-and-claim boundary and concurrency regression close the race;
- one-shot cron and launchd exports could lose the declared offset semantics; cron now uses UTC and
  launchd delegates the due decision to the daemon through periodic governed wake-ups;
- `scheduling.history_page_limit` existed publicly but did not bound every daemon history surface;
  oversized requests are now refused and all projections apply the configured maximum;
- Git effect preconditions and evidence probes used private ten- and thirty-second subprocess
  bounds instead of the request's public governed timeout; every Git subprocess now uses that
  declared budget, with a regression proving propagation through stage and commit;
- driver recovery, malformed accepted results, retry settlement, compatibility checks, trigger
  identity, and export boundaries lacked complete negative coverage; focused tests now exercise
  those branches and the scheduling driver, contracts, repository, and service each exceed the
  required 80 percent branch coverage.

No universal workflow, private operational deny-list, direct OS mission execution, scheduled
knowledge mutation outside accepted policy, distributed worker authority, TUI scope, or competing
agent runtime was introduced.

## Decision

D-048 accepts I08 at `39f04c3` based on the complete local and seven-job remote evidence above. I08
may be promoted from its topic branch to `develop`. I09 distributed execution remains unauthorized
until an explicit engineer decision after the I08 integration gate.
