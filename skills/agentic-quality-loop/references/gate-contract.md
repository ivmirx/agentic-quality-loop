# Repository gate contract

## Contents

- [Entrypoint](#entrypoint)
- [Behavior](#behavior)
- [Enforcement levels](#enforcement-levels)

## Entrypoint

Expose one repository-owned command accepting:

```text
<entrypoint> [auto|fast|full|native] [--base <git-ref>] \
  [--keep-previous-runs <count>]
```

Required profiles:

- `auto`: select the smallest sufficient union of checks implied by all changed
  paths;
- `fast`: run the inexpensive deterministic development gate;
- `full`: run the repository's complete default local non-live verification.

`native` is optional. When present, it runs `full` plus the documented
SDK-dependent native or platform verification. A repository whose normal local
verification is inherently platform-dependent may omit `native` and include
those checks in `full`. Unknown changed paths must widen the selection to
`full`, or to `native` when they may affect its evidence, rather than silently
skipping checks.
Profiles are operator-facing choices, not required internal routing buckets.
An `auto` run may compose checks more narrowly than a named profile; it must
not inherit unrelated expensive checks merely because one required check also
belongs to `full` or `native`. An explicit `full` run remains exhaustive for
the repository's default local tier.
When every check is already cheap, `auto`, `fast`, and `full` may deliberately
run the same set. In that case no changed-path router is required.
`--keep-previous-runs` is optional and, when supported, retains the current run
plus the requested number of earlier evidence runs. A repository may default
this to a small value such as one; cleanup must be deterministic, confined to
the gate's own artifact root, and performed only while holding its run lock.

Place the entrypoint in the repository's quality root. The common runner accepts
exactly one of these language variants:

```text
quality/gate
quality/gate.sh
quality/gate.mjs
quality/gate.py
quality/gate.ps1
```

Discovery fails if more than one candidate exists. Do not retain compatibility
wrappers under `script/`, `scripts/`, or the repository root.

Organize supporting files by purpose without fighting mandatory ecosystem
locations:

```text
quality/
  gate.<language>
  README.md
  TODO.md
  checks/
  config/
  lib/
  tests/
```

Add narrower folders such as `native/`, `generator/`, or `policy/` when the
repository needs them. Keep files such as package manifests, `.editorconfig`,
tool manifests, and build props in ecosystem-standard locations when tools
require it.

## Behavior

- Return zero only when every required hard check passes.
- Never modify production sources or tracked generated artifacts.
- Pin or verify tool versions.
- Use locked dependency resolution where the ecosystem supports it.
- Keep existing violations behind an explicit baseline or apply a
  changed-declaration ratchet.
- Bootstrap a debt baseline from the task's starting revision, or prove each
  entry predates the task. Never record new or task-modified files as
  pre-existing debt.
- Treat a committed baseline and its analyzer configuration as policy, not as
  ordinary task output. During a product task, require the working copy to
  match the recorded task-base policy. Recalibration is a separate, explicit
  quality-policy task with fresh measurements and review; a task must not make
  itself green by editing both source and exemptions.
- For `auto`, select expensive checks only when a changed input or affected
  consumer can invalidate the evidence they prove. Keep routing groups coarse
  enough to maintain, but do not use one broad native or UI flag to run
  unrelated behavior checks.
- When `auto` or `full` is materially slower than `fast`, print elapsed time for
  each check and the changed path or rule that selected every expensive check.
  Do not execute the same deterministic check twice in one invocation unless
  the second execution proves a distinct state and the gate reports that
  reason.
- Treat every changed-path consumer as deletion- and rename-aware. A scanner
  must not assume that each path in a complete diff still exists in the working
  tree.
- Do not retry deterministic, policy, compiler, or toolchain failures blindly.
  Retry only an identified transient operation, use a narrow bounded attempt
  count, and retain the first failure in diagnostics.
- Make concurrent invocations safe. Read-only checks with no shared mutable
  state need no repository lock. Otherwise isolate compiler/package caches,
  build servers, output roots, and evidence completely, or fail quickly under
  a repository-scoped interprocess lock. A lock must identify its live owner,
  recover safely from stale ownership, and clean up on normal or signaled exit.
  Safe recovery may be deliberate and manual after verifying that the recorded
  owner is gone; never race by deleting a lock merely because a PID appears
  dead. Concurrent agents must never hang on shared tool state or corrupt
  evidence.
- Write useful diagnostics to a stable log or structured artifact. For a
  text-only gate invoked through the bundled universal runner, its captured log
  satisfies this requirement; do not add a second artifact system by default.
- Before writing or pruning evidence, require the artifact root, run root,
  cache root, and deletion candidates to be current-user-owned, non-symlink
  directories contained beneath the configured artifact root. This requirement
  applies when the repository gate owns such directories. Acquire any required
  run lock before creating a run directory, and count only completed runs for
  retention.
- Print one terse line for each successful check.
- On failure, print the check identifier, first useful diagnostics, and log
  path. Avoid dumping successful compiler/test logs into the agent context.
- Distinguish unavailable prerequisites from a passing check.
- Keep network-dependent vulnerability results advisory or scheduled unless
  the repository explicitly opts into them.

## Enforcement levels

Hard checks must be deterministic and have low false-positive rates. Advisory
checks may report whole-tree size, duplication, dead code, low-severity
dependency findings, or uncalibrated mutation/coverage. Reviewer-only judgments
must not be converted into brittle token searches merely to appear automated.

Language-aware hard checks and standard metrics must be backed by a maintained
compiler/analyzer or ecosystem parser/typed AST. Repository code may pin,
configure, invoke, and normalize that tooling, select changed declarations, or
enforce a project-specific rule through its supported AST API. It must not
reimplement declaration discovery, control flow, complexity, or semantic
concurrency analysis using text, token counts, or bracket heuristics. Exact
inventories of explicitly named forbidden APIs or escape-hatch spellings may
remain lexical when comments, strings, aliases/qualification, and adversarial
variants are handled honestly and the check makes no broader semantic claim.
If no reliable maintained tool or AST is practical, keep the language-aware
signal advisory or reviewer-only and record the gap in `quality/TODO.md`.
Where repository configuration can suppress a required analyzer, add a
black-box liveness fixture using the analyzer's supported build/test interface:
a deliberately violating declaration must produce the expected diagnostic
under the same effective invocation as production. Prefer this proof over an
ever-growing repository-authored parser for analyzer configuration.

When the ecosystem can map method complexity to fresh method coverage
reliably, prefer a localized risk metric such as CRAP:

```text
CRAP = CC^2 * (1 - coverage)^3 + CC
```

Calibrate its threshold from the repository rather than copying a number from
another language or tool. When reliable method mapping is unavailable, keep a
changed-declaration complexity ratchet and fresh coverage floor/report as
separate signals. Global coverage alone is not a quality score; use targeted
mutation testing for important pure rules when its runtime and signal are
proven. Every mutation pilot must record its runtime, useful findings, and
dependency cost, then explicitly retain it, make it periodic, or remove it.
Do not leave rejected pilot tooling installed.

CI is not required by this contract. The same local entrypoint may be wired to
CI later without changing its semantics.

The bundled universal runner adds a separate per-user, machine-wide POSIX
kernel lock around non-dry-run repository gates. It serializes universal-runner
invocations across repositories without waiting: contention returns `BUSY`,
exit 75, and owner repository/runner PID/guard PID/profile/start metadata.
It also captures no more than 8 MiB of output in a private external log,
retaining the beginning and end when needed. By default it retains the current
completed log plus one previous completed log and removes older or incomplete
logs only while holding that machine lock.
Direct repository
entrypoint calls bypass that outer lock, so it supplements rather than replaces
the repository-scoped concurrency rule above. An internal guard keeps the lock
while an already-started gate survives an uncatchable runner exit, without
passing the lock descriptor into gate/build descendants. On hosts without the
required kernel lock, the universal runner fails explicitly instead of running
unlocked.
