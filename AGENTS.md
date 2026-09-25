# MISHKAN Planning Governance

These instructions apply to the entire repository.

## Current stage

MISHKAN has completed the framework's final pre-code stage, Sequence 05. Increments I00 and I01
have passed their local and remote acceptance gates and their code and evidence remain unchanged.
PRD 1.4, SRS 1.6, Contract 1.4, Responsibilities 1.2, System Model 1.3, Architecture 1.3, and
Implementation Plan 1.5 were accepted in order by D-032 through D-037 on 2026-08-25. I02 was
integrated before `feat/i03-mutations-sessions-durability` began. I03 now has a green deterministic
local gate, a passing tracked live CrewAI/Ollama regression, and a green remote Linux/macOS Python
3.11–3.13 matrix recorded in `docs/VALIDATION/durability.md`. D-038 accepts I03. I04 is implemented on
`feat/i04-web-browser-mcp-harnesses`. D-039 accepted its original gate on 2026-08-27. The requested
cross-baseline conformance audit then found gaps in durable concurrency, browser authority and
sensitive state, MCP containment and bounds, Web concurrency, and public input limits. Those gaps
are closed at checkpoint `6a73e98`. A transient Chromium screenshot refusal exposed during
promotion is closed at `3481ca5`. Its 400-test local gate and six-job Linux/macOS Python 3.11–3.13
topic and `develop` matrices are recorded in `docs/VALIDATION/web-browser-mcp.md`. D-040 records the
history-preserving integration checkpoint `0534988` and green six-job matrices on both `develop`
and `main`; I04 is integrated and closed. D-041 accepts Implementation Plan 1.6 and authorizes I05
on `feat/i05-skills-engineering-foundation`. D-042 accepts the complete I05 gate at `8605437` after
the seven-job Linux/macOS Python 3.11–3.13 matrix and real governed Docker, Compose, Dev Container,
and Podman lifecycles passed. I05 passed its seven-job integration matrix on `develop` at `65e8d42`
and is promoted to `main` at `ef185b9`. D-043 authorized I06 on
`feat/i06-organization-missions-communication`; its accepted scope is organization, missions,
communication, governed intervention, and evidence-based professional evolution. D-044 accepts
the completed I06 gate at `2d1f78c` after the final conformance review, local branch-coverage gate,
and seven-job Linux/macOS Python 3.11–3.13 matrix passed. I06 is integrated into `develop` at
`60e3ca7`, whose seven-job integration matrix also passed. The history-preserving synchronization
checkpoint `8b5a2b3` and the `main` promotion at `e171540` each passed the same seven-job matrix.
I06 is integrated and closed. D-045 authorizes I07 Attributed Knowledge and Degraded Operation on
`feat/i07-attributed-knowledge`. Its active scope is explicit literal, episodic, semantic, and
structural knowledge; immutable attributed retrieval evidence; project-scoped ingestion; governed
cross-project promotion; authenticated self-hosted mem0, Cognee, and Graphify integration; and
useful visible degradation. I08 and later increments remain unauthorized until I07 passes D-046.
The rejected
universal workflow, mandatory outcome catalogue, capability-family matrix, static role/tool matrix,
competing runtime, and private operational deny-list must not return.

D-037 accepts the detailed agent-authored mission environment lifecycle, per-context bindings,
Dev Container/Podman/Compose descriptor generation, ordinary effect boundaries, and acceptance
work packages. It does not waive any increment gate.

## Mandatory method

Follow the numbered `SWE-BASICS-BEFORE-CODE` chain without skipping or reordering stages:

0. Foundation and engineering mindset
1. Requirements: PRD
2. Requirements: SRS
3. Design: contract and invariants
4. Transition: requirements to architecture through explicit responsibilities
5. Modeling: behavioral UML, structural C4, and reviewed architecture decisions

The cited framework ends at Sequence 05. Implementation planning and coding follow the approved
architecture but are not additional numbered stages of that framework.

The active stage may use later concepts only as explicitly labeled candidate constraints. It must
not silently turn them into approved architecture.

## Source authority

- The original MISHKAN SPEC and SRS attachments are historical discovery sources.
- `docs/PROJECT/PRD.md` 1.4 is the product authority accepted by D-032.
- `docs/PROJECT/SRS.md` 1.6 is the behavioral authority accepted by D-032.
- `docs/PROJECT/DECISION_LOG.md` is the only decision-status registry.
- `docs/SYSTEM/CONTRACT.md` owns invariants and refusals after Gate G3 approval.
- ADRs own durable implementation decisions.
- When sources conflict, record the conflict. Never resolve it silently in code.

## Writing rules

- PRD statements describe problems, actors, capabilities, exclusions, and measurable outcomes.
- PRD statements must not prescribe libraries, languages, databases, APIs, processes, or topology.
- Every SRS requirement must be uniquely identified, binary, testable, and state its failure
  behavior where relevant.
- Use RFC 2119 vocabulary consistently: MUST, MUST NOT, SHOULD, MAY.
- Separate requirements, technical constraints, policies, defaults, and implementation decisions.
- Do not call a subjective adjective such as fast, safe, intuitive, reliable, or scalable a
  requirement without a measurable threshold.
- Every guarantee receives one primary responsibility owner before components are designed.
- Model behavior before structure. Draw only diagrams that answer a concrete design question.
- Treat skills as first-class portable procedural memory loaded progressively into authorized
  CrewAI work. Skills do not replace roles, execution-context-specific plans, CrewAI coordination,
  or deterministic capability enforcement, and skill mutations follow the public capability policy.
- Treat tools as first-class typed atomic capabilities resolved from a public versioned registry.
  Tool availability, grouping, and source do not grant authority; exact plan and policy scope is
  enforced before dispatch, and production invocation remains integrated with CrewAI.
- Prefer a small set of general file, terminal/process, web, and browser tools plus configurable
  toolsets and dynamic extensions. Project commands are governed inputs, not synthetic tool types;
  do not introduce a universal capability-family taxonomy or static role/outcome tool matrix.
- Treat the 59 identities as persistent professional profiles and Mission Crews as temporary
  contextual compositions. PM and CTO are agents in the organization; free-form missions may use
  optional templates but never require a universal workflow.
- Require an explicit execution-environment decision for environment-dependent mission work.
  An accountable Mission Crew agent authors it through CrewAI; deterministic resolution validates
  compatibility and binds adapters without selecting a different engineering outcome. Reuse
  existing project definitions when compatible; treat Dev Container, Podman, Docker, Nix,
  native-host, and other formats as contextual versioned inputs. Generated descriptors are
  artifacts or governed change sets and never prove runtime readiness by themselves.
- Treat CLI, SDK, chat, TUI, HTTP/SSE, MCP, schedules, Codex, Claude, and other harnesses as clients
  of the same MISHKAN application authority. The TUI may issue governed interventions; no client
  replaces CrewAI or owns stronger policy or authoritative state.
- Keep artifacts immutable, working references compare-and-swap, and terminal, PTY, job, browser,
  and MCP sessions explicitly owned. Availability, discovery, credentials, and instructions never
  grant authority.

## Change discipline

- Keep planning commits small and reviewable.
- Branch from `develop` using `feat/*`, `fix/*`, `docs/*`, `test/*`, `refactor/*`, or `chore/*`.
- Merge topic branches into `develop` after their gate; promote only `develop` into `main` after the
  integrated gate. Never merge a topic branch directly into `main`.
- Record assumptions and unresolved questions explicitly.
- Do not modify or erase historical source attachments.
- Do not force-push or rewrite repository history without explicit engineer authorization.
- A planning approval does not authorize implementation unless the engineer says to begin coding.

## Documentation discipline

- Version only durable requirements, decisions, contracts, models, procedures, or factual evidence.
- Keep source comparisons, assumptions, self-reviews, gate checklists, and temporary analysis in the
  task, `/tmp`, or an external work area.
- Do not create a separate approval or review document when the central decision log is sufficient.
- Organize durable documentation by authority domain, not by the order in which a task created it.
- Remove or supersede obsolete material instead of maintaining parallel active documents.
