<!--
SYNC IMPACT REPORT — temporary review scratch, remove before committing.

Version change: (unfilled template) → 1.0.0
Rationale: initial ratification; no prior version exists, so MAJOR/MINOR/PATCH
increment rules do not apply.

Added principles (all new):
  - I. Quality Gates Are Blocking (NON-NEGOTIABLE)
  - II. Tests Accompany Every Behavior Change
  - III. Coverage Floor of 90%
  - IV. Static Analysis Is Mandatory
  - V. Maintainability by Construction

Renamed sections:
  - [SECTION_2_NAME] → Technology & Quality Standards
  - [SECTION_3_NAME] → Development Workflow & Review

Removed sections: none.

Deferred TODOs: none. Ratification date is the date this constitution was
adopted, not the project's inception date (the local checkout carries a single
squashed commit, so the upstream inception date is not recoverable).

Known configuration drift this constitution deliberately rules against (to be
closed by follow-up work, not by this amendment):
  - apps/api/pyproject.toml fail_under = 25 and api-tests.yaml
    --cov-fail-under=25 contradict codecov.yml's 90% target.
  - apps/web/package.json "lint": "eslint . || true" swallows lint failures.
  - apps/web/tests/ is not executed by any CI workflow.
  - No Python type checker in CI.
  - No automated formatting check in CI.
-->

# LearnHouse Constitution

## Core Principles

### I. Quality Gates Are Blocking (NON-NEGOTIABLE)

Every quality gate MUST block merge. A gate that cannot fail is documentation, not a gate.

- Lint, type-check, and test scripts MUST propagate their exit codes. The `|| true` pattern and
  every equivalent exit-code suppression is forbidden in package scripts and CI steps.
- Every app that ships source code MUST have at least one CI job that runs its tests. This
  explicitly includes `apps/web`, whose `bun test tests` suite is currently invoked by no workflow.
- Skipping a gate is permitted only as an exception stated explicitly in the pull request and
  linked to a tracking issue.

### II. Tests Accompany Every Behavior Change

Every change in behavior MUST land with tests in the same pull request.

- Bug fixes MUST include a reproducing test that fails before the fix and passes after it.
- API tests live in `apps/api/src/tests/` (pytest with pytest-asyncio, `asyncio_mode = "auto"`).
  Web tests live in `apps/web/tests/`. End-to-end user journeys live in `apps/e2e/features/`
  (Playwright).
- Pure refactors that preserve behavior are exempt from new tests but MUST keep the existing suite
  green and MUST NOT delete or weaken coverage of the refactored code.

Rationale: tests are required to travel with the change, but the order in which they are written is
left to the author's judgment.

### III. Coverage Floor of 90%

Project and patch coverage MUST each meet or exceed 90%, with the 1% threshold already declared in
`codecov.yml`.

- `fail_under` in `apps/api/pyproject.toml` and `--cov-fail-under` in
  `.github/workflows/api-tests.yaml` MUST match this figure. The current value of 25 is technical
  debt being repaid, not the standard.
- The coverage `omit` list MAY only be widened with a stated reason recorded in the pull request.
  Narrowing it is always permitted.

Rationale: two contradictory thresholds coexisting in the repository means nobody knows which one
is real.

### IV. Static Analysis Is Mandatory

Code MUST be machine-checked before review, not only reviewed by humans.

- TypeScript MUST compile under `strict: true` with a clean `tsc --noEmit`.
- Python MUST pass `ruff` at the pinned version (currently `0.15.9`), and a type checker MUST run in
  CI. No type checker exists today; this is a mandatory gap to close.
- Formatting MUST be verified automatically in CI on both the Python and TypeScript sides.
- Backlogged ESLint rules (`no-console`, the `react-hooks/*` React Compiler set, tracked in issue
  #800) MAY remain at warning severity only while that tracking issue is open.
- `rtl/no-physical-direction` MUST stay at `error`. Entries MAY only be added to its `LTR_LOCKED`
  allowlist with a stated reason.

Rationale: linting without type checking catches roughly half the defect classes it appears to.

### V. Maintainability by Construction

Prefer the simplest solution that is still correct.

- Every added abstraction MUST be justified in the pull request that introduces it (YAGNI).
- Schema changes MUST go through an Alembic migration in `apps/api/migrations/versions/`. Manual
  schema edits are forbidden.
- Enterprise Edition code MUST live under `apps/web/ee/` and MUST respect the `LEARNHOUSE_DISABLE_EE`
  flag, so a community build never depends on it.
- After touching any `package.json`, `apps/api/pyproject.toml`, or version number, `scripts/lockfiles.sh`
  MUST be run. CI verifies this with `--check`, and every install in CI and Docker is frozen.

Rationale: these constraints are already mechanically enforced; the constitution makes them explicit
so that plans and specs account for them up front.

## Technology & Quality Standards

The baseline toolchain is a constraint on every plan, not a suggestion. Introducing a different
language, test framework, or package manager into an existing app requires a constitution amendment.

- **`apps/api`** — Python `>=3.14.7,<3.14.8`, managed by uv. FastAPI + SQLModel + Alembic over
  PostgreSQL. Tests with pytest 9, pytest-asyncio, pytest-cov. Lint with ruff 0.15.9.
- **`apps/web`** — Next.js 16, React 19, TypeScript 6 in strict mode, Tailwind 4, bun 1.4.0.
  ESLint 9 flat config plus the local `rtl` rule plugin. Prettier settings: `semi: false`,
  `singleQuote: true`, `trailingComma: "es5"`.
- **`apps/collab`** — Hocuspocus + Yjs + ioredis.
- **`apps/cli`** — Commander, bundled with tsup, tested with Vitest, published to npm with
  provenance. The published tag, `package.json` version, and built binary version MUST agree.
- **`apps/e2e`** — Playwright, `workers: 1`, `fullyParallel: false`, driving a real self-hosted stack.
- Dependency updates flow through Renovate (`baseBranches: ["dev"]`, `minimumReleaseAge: "3 days"`).
  Manual upgrades outside Renovate MUST state why in the pull request.

## Development Workflow & Review

Contribution process follows `CONTRIBUTING.md`:

- Bugs: open an issue with a reproduction and wait for a maintainer go-ahead before writing code.
- Features: open a Discussion under "ideas", get a go-ahead, open a linked issue, then fork, branch,
  and open a pull request. The base branch is `dev`.

The following CI checks MUST be green before merge: `api-lint`; `api-tests` including the Codecov
upload under flag `api`; `web-lint` (both the `next-lint` and `typecheck` jobs); `cli-tests`; and
`lockfiles`. The web unit-test job and the Python type-check job required by Principles I and IV MUST
be added and MUST join this list.

`e2e.yaml` runs nightly and on manual dispatch and does not gate pull requests. Changes that affect
an end-to-end journey MUST trigger it manually and confirm it passes before merge.

Pull requests MUST state any constitution exception explicitly. Reviewers confirm compliance as part
of approval.

## Governance

This constitution supersedes any conflicting practice, convention, or habit.

- Amendments require a pull request editing this file, stating the rationale and, where an existing
  practice changes, the migration plan.
- Versioning is semantic: MAJOR for removing or incompatibly redefining a principle, MINOR for adding
  a principle or section or materially expanding guidance, PATCH for clarifications and wording.
- Compliance is verified on every pull request by the reviewer. Repeated exceptions to the same
  principle are a signal to amend the principle, not to keep granting exceptions.
- The ratification date below is the date this constitution was adopted, not the date the project
  began.

**Version**: 1.0.0 | **Ratified**: 2026-09-16 | **Last Amended**: 2026-09-16
