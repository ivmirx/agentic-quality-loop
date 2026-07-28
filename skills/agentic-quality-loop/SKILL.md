---
name: agentic-quality-loop
description: Design, install, run, and review repository-owned deterministic quality gates with a bounded independent-review loop. Use when bootstrapping quality infrastructure in a new repository, changing code in a repository with a quality/gate entrypoint using a supported optional suffix, or enforcing mechanical architecture, lint, test, coverage, mutation, and determinism evidence before a fresh reviewer examines the diff.
---

# Agentic Quality Loop

Use deterministic tools for facts and a fresh reviewer for judgment. Keep the
repository's scripts, configuration, architecture documents, and tests as the
source of truth; do not duplicate their rules in this skill.

Prefer maintained compiler or ecosystem analyzers for language-aware facts and
standard metrics. Do not create repository-owned lexers, token counters, or
regex approximations to discover declarations, model control flow, calculate
complexity, or claim semantic concurrency correctness. Thin adapters may
configure tools, normalize their output, map findings to changed declarations,
or implement project-specific checks through supported AST or symbol APIs.
Exact inventories of explicitly named forbidden APIs or escape-hatch spellings
may remain lexical when they handle comments/strings and adversarial variants
honestly and make no broader semantic claim. If no reliable maintained tool or
AST is practical, keep the language-aware signal advisory or reviewer-owned and
record the limitation rather than inventing a hard parser.

## Run the loop

1. Establish the change boundary before editing:
   - record the repository root, `HEAD`, intended base, branch, and short status;
   - preserve pre-existing changes and distinguish them from the task delta;
   - summarize the requested outcome, non-goals, invariants, and touched zones.
2. Read the nearest `AGENTS.md` when present, follow its referenced architecture,
   concurrency, build, and quality documents, then select only the sections
   relevant to the changed paths.
3. Implement with one writer. Run the narrowest focused checks while working,
   or `fast` when no narrower command exists. Do not run `auto` or `full` after
   every small edit.
4. When the implementation is stable, run the repository gate through the
   resolved `scripts/run_quality_gate.py` executable, normally with `auto`.
   Rerun it after correcting a failed gate and after accepted reviewer fixes.
   Use `full` before handoff only when the local policy requires it. Never
   replace a failed deterministic check with manual inspection.
5. Resolve quality-system defects and task-caused regressions before review.
   Keep genuine pre-existing product, test, or data failures hard and report
   them; never weaken the gate to manufacture a pass. Read full logs only for
   failed checks; successful checks need no manual re-check.
6. Spawn a fresh read-only reviewer with minimal context. Give it the original
   request, task delta, applicable repository documents, changed tests, and
   concise gate result. Do not give it the implementer's rationale initially.
   Follow `references/reviewer-contract.md`. If the host cannot create a
   genuinely fresh reviewer, report independent review as unavailable and do
   not present self-review as independent.
7. When a fresh reviewer is available, let the writer fix accepted blocking
   findings, rerun the gate, and ask the same reviewer for one verification
   pass.
8. After the initial review and its one verification pass, stop unless a
   correctness, security, data-loss, authority, or similarly severe defect
   remains. Record non-blocking ideas without expanding the task.

## Select the gate

Use exactly one supported repository entrypoint: extensionless `quality/gate`,
or `quality/gate.sh`, `.mjs`, `.py`, or `.ps1`. The bundled runner discovers
that entrypoint and captures noisy output outside the repository:

```text
<absolute-skill-root>/scripts/run_quality_gate.py \
  --repo <absolute-repository-root> --profile auto --base <base-sha>
```

Resolve both absolute paths and invoke the executable directly. Do not prefix
it with `python` or `python3`: sandbox approvals may be scoped to the runner
executable, and an interpreter prefix can leave SDK, IPC, cache, or package
restore operations sandboxed. When the host blocks facilities a repository
gate genuinely needs, request narrow approval for the direct runner command
and run outside the sandbox only to that extent. The runner cannot grant itself
host permissions.

Every non-dry-run invocation takes one per-user, machine-wide POSIX kernel lock
before starting the repository gate. If another universal-runner gate is
active—even for a different repository—the contender does not wait: it reports
`BUSY` with the owner repository, runner/guard PIDs, profile, and start time,
then exits 75.
This keeps local builds predictable. A small internal guard retains the kernel
lock if the runner is killed, until its already-started repository gate exits;
the lock descriptor is not inherited by the gate or its build descendants.
Calling a repository's `quality/gate*` entrypoint directly bypasses this
machine-wide lock, so repository-scoped concurrency protection remains
required. Never evade `BUSY` by calling the repository gate directly, removing
the lock, or changing the runner. Report or defer the gate and retry only after
the active gate finishes and releases the lock.

The runner captures at most 8 MiB of gate output, retaining the beginning and
end when truncation is necessary. Its private per-user log store keeps the
current completed run plus one previous completed run by default and removes
older or incomplete runs only while holding the machine lock.

Use the task's recorded base. If there is no recorded base, use `HEAD` only for
an uncommitted task delta; do not guess across unrelated commits. See
`references/gate-contract.md` for profiles and output requirements.

For a materially expensive gate, `auto` selects the smallest sufficient union
of checks rather than an unrelated coarse profile. The repository gate owns
that routing and must explain why it selected expensive checks. Do not add
cross-run result reuse to hide overbroad routing.

## Bootstrap a repository

When the repository has no supported `quality/gate` entrypoint, or the user asks
to design its quality infrastructure, read `references/bootstrap.md` completely
before editing. Inventory the repository and measure its current behavior
rather than copying thresholds or allowlists from another project. Start with
its minimum-viable-gate section. Do not install routing, locking, artifact
retention, caches, baselines, or metric systems until the repository's actual
costs and risks earn them.

When selecting or calibrating complexity checks, also read
`references/complexity-examples.md`. Treat its ecosystem examples as patterns,
not thresholds.

## Enforce the evidence boundary

Treat these as mechanical when the gate covers them:

- toolchain and dependency resolution;
- formatting, compiler diagnostics, lint, and banned APIs;
- dependency direction and cycles;
- focused/full test execution and forbidden skipped/focused tests;
- coverage, mutation, generated-artifact drift, and reproducibility probes.

A mutation pilot must end with an explicit decision to retain it, run it only
periodically, or remove it. Removal is valid when runtime, dependencies, or
survivor signal do not justify continued maintenance.

Leave these to the reviewer:

- whether responsibilities change independently;
- whether an abstraction has a real consumer;
- whether ownership, cancellation, authority, and lifetime semantics are true;
- whether tests protect behavior instead of incidental implementation or data;
- cross-file semantic leaks not represented by dependency rules.

Do not ask the reviewer to re-run or manually emulate a passing mechanical
check.

## Report completion

Report the selected profile, gate result, reviewer findings that were fixed,
remaining non-blocking items, and the exact verification evidence. Do not claim
an SDK-enabled, device, live-service, release, or full-platform pass when only a
lower profile ran.
