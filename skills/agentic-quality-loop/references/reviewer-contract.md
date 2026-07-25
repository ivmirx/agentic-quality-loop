# Independent reviewer contract

## Prompt inputs

Give a fresh read-only reviewer:

- the original request and explicit non-goals;
- repository root and task base;
- the raw task diff, excluding known pre-existing user changes;
- the applicable `AGENTS.md`, quality policy, and architecture/concurrency
  documents;
- changed tests and the concise deterministic gate result.

Do not provide the implementer's design defense before the first pass. Ask the
reviewer to inspect changed declarations plus one direct producer/consumer hop,
not to launch a repository-wide cleanup.

## Review lenses

Review only lenses relevant to the touched zones:

- correctness and failure behavior;
- dependency direction, DI, capability, and authority boundaries;
- async cancellation, reentrancy, stale results, task/resource lifetime;
- persistence atomicity, idempotency, replay, and compatibility;
- deterministic ordering, culture/time/randomness, and generated artifacts;
- test value, missing boundary cases, and implementation-coupled assertions;
- unnecessary abstraction and duplicated policy;
- complexity growth that represents mixed responsibility rather than mere
  declarative size.

## Finding format

Every blocking finding must contain:

```text
Severity:
Invariant:
Evidence: <path:line>
Failure scenario:
Smallest fix or proving test:
Confidence:
```

Block only for a concrete correctness, safety, architecture, determinism, or
maintainability regression. Do not block on naming preference, hypothetical
future consumers, blanket coverage goals, broad snapshots, or a request to
refactor unrelated legacy code.

Require a new abstraction only when it removes a demonstrated boundary leak,
centralizes duplicated policy, creates a necessary test seam, or separates
responsibilities that already change independently.

## Verification

After fixes, verify the accepted findings once against the new diff and gate
result. Do not restart the review from scratch or introduce an unrelated design
agenda. Escalate only unresolved high-severity findings after the second pass.
