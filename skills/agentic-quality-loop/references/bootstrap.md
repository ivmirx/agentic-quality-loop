# Bootstrapping a repository quality system

Use this procedure when a repository has no supported `quality/gate`
entrypoint. The objective is not to copy another repository's tools; it is to
turn this repository's actual constraints into cheap, trustworthy evidence.

## Contents

- [1. Establish the boundary](#1-establish-the-boundary)
- [2. Inventory the repository](#2-inventory-the-repository)
- [3. Map risks to evidence](#3-map-risks-to-evidence)
- [3a. Start with the minimum viable gate](#3a-start-with-the-minimum-viable-gate)
- [4. Create the repository contract](#4-create-the-repository-contract)
- [5. Calibrate rather than copy](#5-calibrate-rather-than-copy)
- [6. Make automatic routing fail closed](#6-make-automatic-routing-fail-closed)
- [7. Protect evidence integrity](#7-protect-evidence-integrity)
- [8. Attack the gate before trusting it](#8-attack-the-gate-before-trusting-it)
- [9. Roll out without hiding product failures](#9-roll-out-without-hiding-product-failures)
- [Required handoff](#required-handoff)

## 1. Establish the boundary

Before editing, record:

- repository root, `HEAD`, branch, submodules, and worktrees;
- tracked, staged, unstaged, untracked, and ignored generated state;
- which changes predate the task and how their exact content will be preserved;
- explicit non-goals such as CI, release signing, devices, or live services.

Never create a baseline from the task's new files or current modifications.
Anchor it to the task base, or prove every exempted artifact predates the task.

## 2. Inventory the repository

Read the nearest `AGENTS.md` when present and every document it directly selects
for the active architecture, concurrency, build, data, and platform zones.
Discover:

- toolchain and dependency manifests, lock files, projects, targets, and
  platform matrices;
- production layers and intended dependency directions;
- existing build, test, lint, formatter, analyzer, packaging, and release
  commands;
- maintained compiler/analyzer rules and supported AST or symbol extension
  points for language-aware project policy and complexity;
- generated sources, databases, resource bundles, schemas, migrations, and
  reproducibility requirements;
- clocks, randomness, filesystem, network, UI framework, service locator,
  global singleton, and native-boundary access;
- test frameworks, skipped/focused markers, fixture ownership, test duration,
  and current coverage collection;
- SDK-, device-, credential-, or live-service-dependent checks that cannot be
  claimed by an ordinary local run.

Run current commands before designing replacements. Measure duration and
capture genuine failures without changing product code.

## 3. Map risks to evidence

For each important invariant, choose one owner:

- **hard deterministic gate**: objective, repeatable, low false-positive fact;
- **advisory measurement**: useful signal that is not calibrated enough to
  block;
- **independent reviewer**: semantic judgment that tools cannot prove.

Good hard checks include compiler diagnostics, exact target inventories,
dependency direction, banned boundary APIs, focused-test detection, scoped
coverage evidence, generated-artifact drift, and reproducibility. Cohesion,
useful abstractions, lifetime semantics, and assertion value remain reviewer
judgments.

Distinguish a generated product artifact from an evolving data snapshot.
Require byte-for-byte drift or regeneration checks only when reproducibility is
an actual product invariant. For a database whose content is expected to
change, prefer stable semantic contracts—schema, integrity, identifier ranges,
relationships, versions, and registry consistency—over row-count or byte
baselines.

Do not turn a reviewer concern into a brittle text search merely to automate it.
Static rules must have positive and negative counterexamples.

## 3a. Start with the minimum viable gate

Scale the quality system to the repository and the cost of its checks. The
contract describes how a feature must behave **when it exists**; it does not
require every repository to implement every feature.

For a small repository whose useful checks finish quickly, begin with:

- one gate file that accepts `fast`, `auto`, and `full`;
- `auto = full`, and even `fast = full`, when routing would save no meaningful
  time;
- existing compiler/runtime syntax checks and locked package commands;
- strict nonzero test evidence when product tests exist, or an honest hard red
  when they do not;
- a short README, TODO, and only the adversarial fixtures needed for checks
  actually implemented.

Do **not** build a generic gate framework inside a tiny repository. Add these
subsystems only when the inventory proves they are needed:

- changed-path routing, when a full run is materially slower;
- a repository lock, when direct invocations share mutable build output,
  package state, devices, ports, or caches and cannot instead isolate them;
- repository-owned evidence directories and retention, when checks produce
  structured or large artifacts that the universal runner's captured text log
  cannot represent;
- persistent caches, when a pinned tool or dependency workflow needs them;
- baselines, coverage, mutation, generators, or AST policy only when the
  repository has the corresponding tool, debt, test suite, or product risk.

Read-only checks with no shared mutable state are already concurrency-safe.
Text-only gates run through the universal runner already receive a bounded
8 MiB external log, current-plus-one-previous completed log retention, and a
machine-wide lock. Keep direct output useful, but do not duplicate those
facilities without a repository-specific reason.

Do not cache prior pass/fail results across gate runs. Dependency or compiler
caches may accelerate a freshly executed check when their toolchain supports
correct invalidation, but stale evidence is not a current pass.

Use proportionality as a design test. If the quality implementation approaches
or exceeds the product code it checks, pause and justify each subsystem. A
small honest gate with documented limitations is preferable to a comprehensive
framework its owner cannot maintain.

## 4. Create the repository contract

Use this shape as a menu, not a requirement to create empty layers:

```text
quality/
  gate | gate.sh | gate.mjs | gate.py | gate.ps1
  README.md
  TODO.md
  checks/
  config/
  lib/
  tests/
```

Choose exactly one launcher variant from that first line. The bare `gate`
variant must be executable. Do not add another `quality/gate*` candidate or a
compatibility wrapper under `script/`, `scripts/`, or the repository root; the
common runner must fail when discovery is absent or ambiguous.

A minimum gate may need only the launcher, README, TODO, and one focused test
file. Add `checks/`, `config/`, `lib/`, `tests/`, or narrower topic folders such
as `native/`, `generator/`, or `policy/` only when earned. Keep mandatory
ecosystem files in their conventional locations.

The gate accepts `fast`, `auto`, and `full`, plus optional `native`, and accepts
`--base <commit>`. Profiles may intentionally select the same checks when the
whole gate is cheap. Document exactly what each profile proves and what it does
not prove. When `native` exists, make it a documented SDK-dependent superset of
`full`. If routine verification is already inherently platform-dependent, omit
the extra profile and keep those checks in `full`.

Wire the repository's existing agent instructions to the gate and its README
without copying the policy into those instructions. If autonomous agents use
the repository and no durable agent guide exists, add a short `AGENTS.md` that
names the gate command, requires it before handoff, and points to
`quality/README.md`.

## 5. Calibrate rather than copy

Start with fresh measurements from this repository:

- ratchet existing lint or formatting debt so new or increased debt fails;
- apply complexity limits reported by a maintained ecosystem analyzer to new
  or changed declarations, while anchoring legacy allowances to the task base;
  never calculate source-language complexity with repository-authored text or
  token parsing;
- collect coverage from the intended production scope and prove that tests
  actually executed; require the expected package/assembly and map covered
  files to an explicit production-source inventory rather than trusting names
  embedded in a report;
- use method-level CRAP only when the toolchain reliably maps method complexity
  to fresh method coverage; otherwise keep complexity and coverage separate;
- pilot mutation testing on a small, important pure rule before making it hard,
  record runtime and useful survivors, then explicitly retain it, make it
  periodic, or remove its dependencies and commands;
- audit tests for observable behavior, invariants, state transitions, failure
  recovery, and boundary contracts.

Do not reward assertion count, snapshots, fixture-string mirrors, or a global
coverage percentage detached from behavior. Do not copy a threshold from
another language or repository.

## 6. Make automatic routing fail closed

When routing is worth its maintenance cost, `auto` considers committed changes
since the base plus staged, unstaged, untracked, deleted, and both sides of
renamed paths. Map path families to the smallest sufficient union of required
checks, not merely to a coarse named profile. Shared contracts must route every
affected consumer. Unknown relevant paths widen to `full`, or to `native` when
they may affect its evidence, rather than silently passing. An explicit `full`
run remains exhaustive for the repository's default local tier.

Measure before routing. Tie an expensive check to the inputs or behavior it can
actually invalidate: dependency reproducibility to dependency metadata, native
builds to native consumers, and runtime smokes to the behavior groups they
exercise. Prefer a few stable semantic groups over a per-file matrix, but do
not let one broad native or UI boolean run unrelated smokes. Pure documentation
does not earn product builds merely because it lives under `quality/`.

Print the selected expensive checks and the changed path or rule that selected
each one. Print per-check elapsed time when `auto` or `full` is materially
slower than `fast`. Remove duplicate execution within one invocation before
adding caches or more profiles.

Every diff consumer—not only the router—must handle a path that was deleted or
renamed without trying to read a missing working-tree file.

When the whole gate is cheap, make `auto` run `full` and omit routing code and
routing fixtures. Otherwise write routing fixtures using real temporary Git
repositories for rename, deletion, staged, and untracked cases.

## 7. Protect evidence integrity

Apply the following requirements to the facilities the gate actually owns.
Do not create an artifact subsystem solely to satisfy this section.

- when structured artifacts exist, create a unique evidence directory and
  avoid accepting stale artifacts;
- whenever the repository gate owns evidence or cache directories, require the
  artifact root, run root, persistent cache, and every retention candidate to
  be current-user-owned, non-symlink directories whose resolved paths remain
  beneath the configured artifact root;
- acquire a repository run lock before creating or pruning those directories
  unless all direct invocations otherwise serialize or isolate those writes;
- when repository-owned evidence exists, prune only gate-owned evidence under
  a documented bounded-retention policy;
  retaining the current completed run plus one previous completed run is a
  sensible local default, and incomplete/crashed runs must not displace them;
- when persistent caches are justified, keep them in a stable gate-owned
  location outside individual run directories; populate network-backed tools
  only through an explicit first-run opt-in, then run offline where the
  ecosystem supports it;
- propagate child failures and timeouts;
- do not retry deterministic, policy, compiler, or toolchain failures; retry
  only a known transient operation, bound the attempt count, and preserve the
  first failure in diagnostics;
- verify test-result counters, coverage package/assembly and source identity,
  and nonempty production data; if collectors emit multiple candidate reports,
  accept them only when the gate can prove they are equivalent (for example,
  byte-identical copies), otherwise fail as ambiguous;
- keep generated output in isolated directories and compare it with shipped
  artifacts when those artifacts are part of the product;
- make tests and helpers locate repository fixtures from an injected repository
  root or a portable relative convention; isolated build output must not expose
  absolute developer-path assumptions;
- snapshot the worktree, including ignored product artifacts that a check might
  mutate, and prove it is unchanged afterward;
- when checks use mutable shared state, isolate concurrent caches/build servers
  or reject a concurrent gate quickly
  under a repository-scoped interprocess lock. Prefer a kernel-held lock for
  crash-safe release; an atomic lock directory must record its owner and
  document deliberate manual recovery after an uncatchable process death;
- when a repository lock is required, keep it even though the bundled
  universal runner also rejects concurrent gates machine-wide: direct
  `quality/gate*` calls bypass the outer lock; ensure an already-started
  compiler or build child cannot outlive the process that owns and releases
  the repository lock;
- treat a missing tool, SDK, credential, or other prerequisite as an explicit
  non-pass or unavailable result; never count an unexecuted hard check as pass;
- print terse success lines and bounded failure diagnostics with full log paths;
- when profiles have materially different costs, print per-check durations and
  route reasons for expensive checks.

## 8. Attack the gate before trusting it

Add focused fixtures for the checks and infrastructure actually implemented.
Examples include:

- zero executed tests, failed tests with a misleading process exit, empty or
  wrong-assembly or wrong-source coverage, ambiguous duplicate reports, and
  stale evidence;
- widened or replaced baselines, task-created exemptions, and simultaneous
  baseline-plus-source tampering;
- required-analyzer liveness under the effective build configuration, plus
  project-specific AST adapters across relevant
  syntax forms such as modifiers, attributes, overloads, operators, qualified
  APIs, comments, and strings; do not duplicate the upstream parser's
  conformance suite;
- renamed/deleted sensitive paths, unknown paths, duplicate entrypoints, and
  concurrent invocation; exercise every changed-file scanner against a deleted
  path, not only the route selector;
- symlinked or foreign-owned artifact roots, caches, run directories, and
  retention candidates before any write or deletion;
- generated outputs that are mutually deterministic but differ from the
  product's shipped copy.

Do not add coverage, baseline, routing, retention, lock, or generator fixtures
when the repository has no corresponding feature. For every hard parser or
evidence check that does exist, its positive and adversarial fixtures are part
of the implementation, not optional polish.

## 9. Roll out without hiding product failures

Run focused fixtures, then `fast`, `auto`, and any justified `full` or `native`
profile. Do not weaken rules or update baselines to make rollout green.
Separate:

- quality-system defects to fix now;
- genuine product/test/data failures to leave hard and record in `quality/TODO.md`;
- environment-only evidence that needs a device, SDK, credential, or release
  context.

Give a fresh reviewer the raw quality-system diff, repository documents, and
concise evidence. Fix accepted P0/P1 findings and ask the same reviewer to
verify them once.

## Required handoff

Report the exact gate paths and profiles, measurements, adversarial fixtures,
independent-review outcome, remaining hard reds, advisory findings, preserved
user changes, and all unimplemented items in `quality/TODO.md`.
