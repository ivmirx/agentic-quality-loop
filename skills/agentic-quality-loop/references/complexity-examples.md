# Analyzer-backed complexity examples

Use these examples as selection and calibration patterns, not as universal
thresholds. Pin the selected analyzer, measure the repository at the task base,
and make new or increased complexity the first blocking condition.

## Shared workflow

1. Prefer a maintained compiler, analyzer, or parser that models the language's
   declarations and control flow.
2. Capture stable diagnostics or structured output when the tool supports it.
3. Map findings to changed declarations through tool-provided locations,
   symbols, or AST nodes.
4. Calibrate from the repository's starting revision. Do not baseline task
   changes as legacy debt.
5. Add positive and negative fixtures for representative syntax before making
   the check hard.
6. Keep complexity and coverage separate unless fresh method-level coverage can
   be mapped reliably to the same declaration.

## .NET

Use the .NET code-quality analyzers, including CA1502 when cyclomatic complexity
is the chosen signal, or another maintained Roslyn-based analyzer already used
by the repository. Configure the analyzer through its supported project and
additional-file mechanisms. Map diagnostics to changed methods or types by
symbol and source location; do not rediscover C# declarations with text.

Treat analyzer defaults as starting information only. Measure the codebase,
select a repository-owned threshold or changed-method ratchet, and cover
records, local functions, operators, generated code, and partial declarations
in fixtures.

Prove CA1502 remains live under the effective repository build configuration
with an isolated, deliberately complex method that must produce the diagnostic.
This black-box fixture covers project properties, analyzer configuration,
rulesets, and project-wide suppression without reimplementing those
configuration languages. It cannot prove that a different declaration lacks a
method-local suppression. If local suppressions are forbidden, enforce that
small escape-hatch policy through Roslyn or a bounded, explicit inventory with
adversarial fixtures. Run the real build/analyzer interface and keep all probe
output outside production sources.

## Swift

Use a pinned SwiftLint `cyclomatic_complexity` rule or another maintained
SwiftSyntax/compiler-backed analyzer. Normalize its diagnostics and map them to
changed functions, initializers, accessors, and subscripts. Use SwiftSyntax for
project-specific declaration policy rather than a repository-authored lexer.

Do not equate a long declarative SwiftUI body with high control-flow
complexity. Keep source size advisory unless the repository independently
calibrates a size rule.

## React Native

Analyze JavaScript and TypeScript with a pinned ESLint `complexity` rule and the
repository's parser-aware TypeScript configuration. Map findings to changed
functions, components, and hooks through ESLint locations. Treat JSX and style
object size as advisory rather than cyclomatic complexity.

Route native modules to their own maintained analyzers: use the Swift approach
for Swift, a maintained Kotlin analyzer for Kotlin, and the Objective-C
approach below for Objective-C. Do not combine unlike metrics into one shared
numeric threshold.

## Objective-C

Use Clang diagnostics, Clang Static Analyzer checks, and applicable clang-tidy
checks with the repository's real compilation database. If a maintained
Clang-based cognitive-complexity check is considered, first prove with fixtures
that it diagnoses the repository's Objective-C methods and Apple blocks
correctly.

When Clang has no practical cyclomatic-complexity diagnostic, a pinned,
maintained analyzer such as Lizard can be used as a changed-method ratchet only
after fixtures prove stable method identity and correct handling of the
repository's Objective-C syntax and blocks. Treat it as a bounded complexity
measurement, never as semantic compiler analysis.

When the available toolchain cannot report trustworthy declaration-level
complexity for the repository's Objective-C dialect, keep the measurement
advisory or reviewer-owned. Do not replace the missing analyzer with regex,
brace counting, or a home-grown parser.

## Python

Use a pinned Ruff C901 check, Lizard, or another maintained Python complexity
analyzer. Map diagnostics to changed functions and methods by reported
location. Calibrate the configured maximum or use a changed-function ratchet;
exercise decorators, async functions, comprehensions, pattern matching, and
nested functions in fixtures.

Keep import-time behavior, dynamic dispatch, cancellation, and resource
lifetime as separate analyzer or reviewer concerns. A complexity score does not
prove those semantics.
