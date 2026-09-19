# RSI: lean policy, rich contracts, evidence-led improvement

## Design and implementation contract

The governing implementation plan is `memory-rsi-typesafe-v04` (coding template
`6a62b8c1cb460f1256385c0d484a1ef57d3008ed74c05f3d7e1ee1c247c7c79a`).
This design extends the existing memory workflow rather than introducing another
planner, rule hierarchy, skill catalogue, or canonical policy file.

### What belongs where

- **Platform:** permissions, safety boundaries, credentials, and human authority.
  Neither editable policy nor a model judgment can override these.
- **One canonical policy:** existing `shared/policies/agent-policy.md`. Keep enduring
  principles here: useful outcomes, honest evidence, proportionate contracts,
  shared delegation context, explicit revision/review and reversible changes.
- **Reusable templates:** select the right job pattern instead of appending skills
  to the global prompt. Template improvements are separate versioned operations.
- **One job contract:** task-specific knowledge, tool strategy, responsibilities,
  constraints, achievements, validation and justified capability exceptions.
  Requirements remain pinned even when the global policy evolves.
- **RSI artifacts:** immutable, searchable records of coaching, outcome reflection,
  candidate policy and comparative assessment. They are evidence and proposals,
  not additional instructions or an alternative source of authority.

### Roles: reasoning author, typed assessor, deterministic lifecycle

Jev does **not** generate policy prose. A reasoning agent uses a preparation brief,
selected contracts and reviewed outcomes to draft a candidate. TypeSafe supplies
small typed judgments; code controls input selection, budgets, response validation,
revision checks, persistence, comparison, and explicit promotion.

This avoids pretending a text-generation/chat API exists for Jev. The wire API is
`POST https://api.typesafe.ai/v1/systemone`, with `{model,state,questions}` and typed
`{model,answers,usage}` results. An OpenAI-completions custom-model entry does not
make that protocol chat-compatible. Credentials remain in the host credential
service (or named environment variable); they never enter a memory artifact.

### The work loop

1. Discover and review the appropriate template, then create the shared plan.
2. When explicitly enabled by the operator, plan creation automatically runs a
   policy preflight **before returning the plan to the agent**. It snapshots the
   current policy and the exact saved contract, batches independent questions,
   preserves raw uncertainty and returns concrete improvement opportunities.
3. Address meaningful gaps before implementation. A missing/uncertain assessment
   is reported honestly, never presented as compliance. Create an explicit
   successor contract for acceptance/scope changes; do not rewrite pinned terms.
4. Work and delegate under the same plan and scoped work items. Pre-work scores
   are not earned achievements. Credit is grounded in observed outcome evidence
   and explicit review after the effort.
5. Reflect on the contract's reported evidence, accepted/rejected reviews,
   exceptions and remaining work. Self-reported lessons are labelled as such.
6. Periodically prepare a small policy improvement from selected outcomes. Draft
   one coherent edit: state the problem, expected benefit, counterexamples,
   representative replay cases and rollback condition. Prefer no change over
   unsupported growth. Job-specific knowledge should remain in templates/contracts.
7. Evaluate the candidate against the unchanged baseline and selected contracts.
   Keep preservation risks separate from compensating quality preferences.
   A preference score is a hypothesis, **not proof of improved agent performance**.
8. Review the exact diff and assessment; promote explicitly with current revisions
   or retain the baseline. Preserve history and rollback. Shared-policy weakening
   needs human review; an `actor` field or review note is not authenticated approval.
9. Observe later real outcomes. Stop iterating on a plateau, missing evidence or a
   task/API budget; do not optimize indefinitely against one's own evaluator.

### Rewards over gates

Preflight is coaching that improves the decision *before* effort is spent. It does
not block shell/file/delegation tools, change approval policy, or award badges for
passing a classifier. Opportunities are phrased as useful achievements to earn.
The existing post-work evidence-review mechanism remains the completion standard.
Deterministic schema, path, budget and stale-revision checks protect data integrity;
these are not substitutes for motivation, reasoning or external authorization.

There is no auto-promotion threshold and no self-rewriting background timer.
Uncertain semantic judgments lead to review, not fabricated certainty. Separate
signals for boundary conflicts and lost requirements cannot be averaged away by
better brevity scores. Missing API access leaves useful local contracts operational.

### Versioning and freshness

Keep the existing SHA-256 policy revision and immutable policy history. RSI records
bind exact policy/plan revisions, candidate identity, evaluator/rubric version,
questions, actual returned model, probabilities, usage and elapsed time. A result
is attached only after a post-inference revision check. Reading historical records
recomputes freshness; old results remain inspectable but are never silently current.
No filesystem lock is held while waiting on TypeSafe. Cooperating local writers
serialize snapshots/saves; external file editors are detected by content hashes,
not claimed to participate in a transaction.

Promotion reuses `PolicyStore` rather than duplicating canonical writes. Preview is
the default; apply requires explicit review metadata and current bindings. Updating
policy does not alter templates, existing contracts, past prompts, or managed
`AGENTS`/`CLAUDE` sections. Instruction sync retains its separate allowlisted
preview/apply operation.

### Privacy, failure and portability

Network assessment is opt-in. Only the canonical policy and explicitly selected
contract data are sent to the configured TypeSafe endpoint. The plugin does not
crawl repositories, resolve evidence-reference files, read session transcripts,
or discover credentials on disk. Selected contracts may themselves contain private
information: review them before enabling remote assessment. Key-pattern filtering
is not a comprehensive data-loss-prevention system.

Use bounded request/response sizes, total timeouts, cancellation, limited retries
for transient statuses, strict typed responses, and no credential-bearing redirects.
An error never includes a response body, authorization header or credential value.
No imports, mounts, prompt assemblies, status calls or local preparation perform
an inference request. Missing credentials and API failures are visible states.
Git save, commit and push outcomes are reported separately.

### Responsibility boundaries

- `lib/typesafe.js`: authenticated typed transport and protocol checks.
- `lib/rsi-rubric.js`: versioned global/section-specific judgments, conservative interpretation and coaching.
- `lib/rsi-budget.js`: end-to-end credential-resolution/inference deadline and cancellation.
- `lib/rsi-errors.js`: one-shot, own-data-only safe error-code extraction; no accessor or raw-message forwarding.
- `lib/rsi.js`: bounded workflow orchestration and `memory_rsi` tool.
- `lib/contracts.js`: automatic preflight after successful plan creation.
- Python `rsi*` modules: snapshots, immutable searchable artifacts, freshness,
  proposal and revision-checked promotion using existing policy storage.
- Existing policy, plan and prompt modules retain their original responsibilities.

### Validation strategy

1. Protocol fixtures: all primitive types, invalid/missing/extra answers, nonfinite
   numbers, distributions, oversized streams, timeout/cancellation, retry limits,
   redirects and secret-safe failures.
2. Lifecycle tests: immutable/tamper-evident records, path safety, concurrent/stale
   revisions, explicit preview/apply, unchanged active contracts, policy rollback,
   generic-writer protection and scoped Git behavior.
3. Workflow fixtures: useful outcome contracts, ritual/tool-count rewards, vague
   evidence, instruction injection, policy conflicts, missing provider, and stale
   results. No mock assessment is represented as live model evidence.
4. Live smoke: real configured Jev requests, actual host plugin tools, prompt
   reread, local artifacts and explicit safe candidate comparison; record actual
   model, usage, latency, limitations and disposition.
5. Review: AST/graph exploration, available language-server navigation, complete
   regression suites, independent adverse-case review and clean scoped Git diff.

The initial rubrics are engineering defaults, not statistically calibrated policy
compliance thresholds. Before claiming optimization, build labelled representative
cases, separate development cases from held-out cases, compare false reassurance
and unnecessary review rates, and measure later outcome quality and cost. A live
smoke proves connectivity and lifecycle behavior, not general model accuracy.

## Learning across contracts and current-session friction

The successor contract `memory-rsi-learning-v04` extends the original release scope.
The system should improve its *compression and placement of knowledge*, not merely
collect more instructions. A recurring failure is a research lead, not a new rule.

### Incremental map/reduce, not a giant retrospective prompt

1. **Discover locally:** cursor through saved contracts and explicitly persisted
   session observations. Show coverage, corrupt/oversized sources and remaining
   pages. An analysis page is never represented as the entire corpus.
2. **Map small evidence units:** Jev classifies mechanism, evidence maturity,
   success/failure/mixed signals, salience and likely intervention destination;
   it selects source-linked evidence-row witnesses rather than inventing explanations.
   Prose witnesses retain exact UTF-16 spans; structured telemetry witnesses are
   explicitly labelled JSON-literal values at original source paths, not false
   verbatim prose quotes. Actual observed/reviewed outcomes stay distinct from planned
   work and unreviewed assertions. Reference paths are never opened for upload.
3. **Reuse exact work:** cache successful maps by source revision, unit content,
   question/rubric identity, endpoint and requested model. Preserve actual returned
   model. Model aliases can change invisibly; a deliberate refresh or pinned model
   is needed to reassess those cases. Failed assessments never poison successful
   caches. A changed source/rubric/model invalidates the relevant extraction.
4. **Reduce mechanisms:** code deduplicates source identities and copied evidence;
   Jev assesses compact pattern groups, contradictory/successful cases, maturity,
   generality and proposed intervention placement. Hierarchical reduction follows
   leaf evidence; prior model summaries are not counted as new independent votes.
   The model sees representative witnesses and typed aggregates, not every log.
5. **Surface an issue brief:** show the problem/positive pattern, exact evidence,
   alternative explanations, uncertainty, covered/omitted sources and an operation
   to consider. The reasoning author supplies the causal hypothesis and prose.
   A rare severe issue can matter; simple recurrence counts do not prove causality.
6. **Validate a revision:** use representative cases and adverse/held-out cases,
   preserve baseline obligations, then observe later outcomes. Periodic reflection
   is prompted by useful new evidence, not an infinite score-optimization timer.

Work is bounded and resumable. Larger corpora are handled as pages/cohorts; hierarchy
reduces context consumption without pretending unlimited computation or certainty.
Independent source counts are evidence descriptors, never gamified activity points.
Novel patterns retain an `other/uncertain` path and selected raw witness, so a fixed
classification vocabulary does not silently erase unfamiliar issues.

### Current-session signals without surveillance

Operator-enabled telemetry observes minimal current runtime tool-result metadata:
transport success/error codes, repeat/recovery patterns and denominators. It never
copies tool arguments, raw output, error messages, credentials, session transcripts,
or another session's buffer into memory. Capture is local and bounded; disposal or
reload removes listeners/buffers. Explicit `observe` persists an owned snapshot and
optional agent-selected context note. Only explicitly selected observations reach
Jev during mining. Default capture is off and there is no automatic paid inference.

Transport success does not prove a command/test succeeded. Some denials expose only
free text, which the collector deliberately does not read: these remain unspecified
failures. A failed probe may have been intentional. Selected context notes can add
missing meaning but remain labelled hypotheses, not authenticated observations.
This makes important context available while acknowledging what safe telemetry
cannot establish. Redaction heuristics are not a comprehensive secrecy guarantee.

Known v1 snapshots are projected into coherent per-tool aggregates and a separate
unreviewed author-note unit, rather than many paid classifications of unrelated
zero counters. Totals, rates, capture status and coverage remain shared context;
matching patterns and representative sample strata have exact source references.
Static bookkeeping and repeated-sample exclusions are explicit; completing a page
is not a claim that every source leaf was assessed. Unknown schema/field extensions
fall back to lossless projection, rather than silently discarding novel facts.
Corpus mining selects contracts and persisted observations, not arbitrary memory
files or raw session history. Reflections remain available for reasoning review.

### The destination matters more than another rule

A focused issue can recommend:

- **Rewrite an existing policy section** for a general, enduring ambiguity.
- **Merge or retire sections** when guidance overlaps or is demonstrably obsolete.
- **Revise a template** for reusable domain/task practice.
- **Save ordinary memory** for facts, examples, environment knowledge or incident history.
- **Fix a tool/integration** when software behavior is the root cause.
- **Investigate / retain baseline** when evidence is weak, conflicting or isolated.

Policy size is a consequence of useful coverage, not a target. Character/section
counts and append-only detection are diagnostic signals, not semantic gates. A longer
coherent replacement can be better than a shorter ambiguous rule. New sections may
be justified for genuinely new enduring concepts, but appending a line after every
incident is not learning. The preferred change unit is the complete existing section:
replace, merge or retire against exact section IDs and the policy's content revision,
preserving all unrelated bytes and recording reasons. Existing technical storage
ceilings protect resources; they do not prescribe an arbitrary small policy.

## Configuration and operating guide

Add these fields to the **existing** host `memory-rsi` row's config (do not create
a duplicate provider row). Retain its existing memory/runtime settings:

```yaml
typesafeEnabled: true
typesafeEndpoint: https://api.typesafe.ai/v1/systemone
typesafeModel: jev-latest
typesafeApiKeyEnv: TYPESAFE_API_KEY
typesafeTimeoutMs: 20000
typesafeRetries: 1
rsiTelemetryEnabled: false  # opt into local metadata capture separately
rsiLearningTimeoutMs: 300000  # complete tool turn; each inference remains bounded
```

Store the key in DSH's credential manager under `TYPESAFE_API_KEY`, or provide the
named environment variable to the host. Do not put a key in a plan, tool argument,
policy, YAML composition or Git. Resolve credentials afresh for every assessment.
Remove an incompatible Jev OpenAI-chat provider entry rather than inventing an
unsupported chat `api` type. The dedicated adapter does not change the agent's
normal reasoning model. `memory_rsi status` checks configuration without inference.

Defaults: network disabled; 20-second **total** inference budget, one retry,
128-KiB request and 256-KiB response. Operator overrides have hard transport ceilings
(120 seconds, 3 retries, 1-MiB request, 2-MiB response). Retries apply only to
429/529/502/503/504; authentication failures are not retried. Memory operations have
their own subprocess budgets. Nothing downloads a model or starts an extra server.

### Tool sequence

All `request` arguments are JSON strings **excluding** action. Optional `no_git`
retains local durability while skipping memory publication.

| Action | Request | Result / side effect |
|---|---|---|
| `status` | `{}` | Enabled/credential state, endpoint/model/budget; no inference |
| `preflight` | `{plan_id}` | Fresh policy/contract coaching + immutable receipt |
| `reflect` | `{plan_id, lesson?}` | Local review-state summary + labelled hypothesis; no inference |
| `observe` | `{plan_id?,context_note?}` | Persist only the invoking session's metadata and explicitly selected summary; no inference |
| `clear_signals` | `{}` | Clear own local buffer, not saved observations |
| `corpus` | `{kind:"plans" or "observations",after?,limit?}` | Local metadata page, exact revisions, coverage/errors and cursor |
| `mine` | `{kind,source_ids? OR after?,limit?,max_calls?,refresh?,cursor?}` | Incremental typed maps, successful cache reuse, receipts and resumable unit cursor |
| `reduce` | `{artifact_ids:[],max_issues?}` | Focused issue briefs from mappings or prior insight lineage; preserve counterevidence |
| `prepare` | `{plan_ids:[],insight_ids?}` | Local baseline, exact sections, cases/outcomes and selected current issue briefs |
| `sections` | `{}` | Exact policy section IDs, text and content revisions |
| `propose` | `{proposal_id,body OR edits,reason,expected_revision,plan_ids:[],source_artifact_ids?}` | Immutable whole-policy or section-based candidate, source insight refs, snapshots and descriptive metrics |
| `evaluate` | `{proposal_id}` | One batched comparative inference + immutable receipt |
| `promote` | `{proposal_id,evaluation_id,expected_revision,review_note,apply?:false}` | Exact diff by default; explicit apply reuses policy history |
| `read` | `{id,issue_index?}` | Artifact and recomputed freshness; select one insight issue/assessment for focused drill-down |
| `list` | `{kind?,limit?,after?}` | Bounded summaries; follow `next_after` while `has_more` |
| `diagnose` | `{}` | Three labelled synthetic live assessor probes; not calibration or operational plans |

Section edits have `{operation:"replace" or "merge" or "retire", section_ids:[],
reason, body?}`. Replacement addresses one complete section; merging selects
adjacent sections in document order; retirement has no body. These operations preserve
unselected bytes and are replay-checked against the immutable parent snapshot. The
full-body path remains available for a coherent complete rewrite or a genuinely new
section. A descriptive append-only flag invites review; it does not prohibit useful
new guidance or substitute for semantic preservation review.

`mine` defaults to four sources and four logical assessment attempts per turn;
follow the returned `resume` object until that selected page finishes, then its corpus
cursor. Resume pins the selected page/order and exact source projection, questions,
rubric, requested model and endpoint. Changed selection/projection/evaluator or legacy
cursors require a restart; `max_calls` may change. An ID cursor is **not** a change
feed: periodically rescan from the start for changed or earlier-sorting source IDs.
Successful unchanged unit mappings are reused. Model-alias drift still requires an
explicit refresh or pinned model; it cannot be detected from the alias alone.

`calls` counts logical assessor invocations, not HTTP retries or proof of billing
(disabled assessment can stop locally). Usage is a subtotal of validated reported
fields; `complete`, `unknown_usage_calls` and `unavailable_calls` disclose missing or
failed responses, not invoice completeness. Safe allowlisted failure codes survive
receipts and compact output; arbitrary errors, getters and provider prose do not.
`reduce` accepts up to 128 direct artifacts and follows up to 1,024 distinct mapping
roots across prior insights, exposing deferred mechanisms rather than silently
calling them reviewed. Larger histories remain separately reviewable cohorts; there
is no claim of an unlimited whole-corpus judgment. Model state contains compact
aggregates and at most 12 stratified witnesses per selected group, not the raw corpus.
The 1,024-root test uses mocked local I/O and proves dedup/budget behavior, not real
CLI throughput; serial subprocess reads make smaller cohorts preferable in practice.

Use the receipt `id` from `evaluate` as `evaluation_id`. Re-read current policy and
contracts if a stale error occurs; create a new candidate ID rather than overwriting
an old one. Select at most eight plans, and prefer a small representative set.
Artifacts live under `shared/efforts/rsi-*.md`, can be found with ordinary memory
search, and cannot be rewritten with `memory_new`/`memory_update`. Local hashes
provide integrity, not signed authenticity. Avoid sensitive content in selected
contracts; evidence references are **not** dereferenced. Large responses retain
receipt IDs, source revisions, cursors, publication status and coverage rather than
silently losing completed work; `read` with `issue_index` retrieves one focused issue.

Source freshness is transitive: a bounded memoized traversal checks reachable plan
pins and artifact hashes, rejects cycles/missing/corrupt sources, and reports a
compact `freshness.lineage` summary. Proposal, evaluation recording and promotion
recheck that lineage under the existing contracts→policy locks. Cooperative writers
cannot change source pins during that transaction; external edits ignoring locks
remain outside its guarantee. Caps are 4,096 artifacts, 1,024 plans, 64 MiB read and
65,536 edges; incomplete traversal is unavailable, never fresh. Historical nested
policy revisions do not expire policy-independent extracted evidence, but adopting
an issue brief requires that selected insight's policy to be current. Re-reduction
against current policy is the explicit way to reuse valid historical ancestry.

A `reason` should identify the observed pattern, expected benefit, counterexamples
and rollback condition. Evaluation dispositions (`human-review-required`,
`retain-baseline`, `needs-outcome-evidence`, `candidate-for-review`) are conservative
coaching, not authorization. All promotion applies are explicit and revision-bound.
A technically unavailable evaluation cannot be promoted through this workflow;
semantic scores themselves do not decide. The direct `memory_policy` API remains an
explicit operator repair/edit surface, not a hidden classifier bypass or security
boundary. Do not promote shared weakening without actual human review.

The Python `memory rsi` command implements the local store actions `context`,
`sections`, `corpus`, `lookup`, `propose`, `record`, `read`, `list`, `promote`; it makes no TypeSafe requests. The DSH
`memory_rsi` tool owns credential resolution and semantic orchestration. Standalone
CLI callers may record assessor data, explicitly labelled unauthenticated provenance;
that is not proof it came from Jev. The model-facing tool intentionally does not expose
`record`, arbitrary questions, endpoint overrides, or an `actor:human` argument.

### Choosing and retiring improvements

Prefer independently reviewed outcomes across varied tasks, including an adverse
case and a case that should not change. Do not feed held-out test cases into the
author's iterative tuning. Track later outcome quality, regression rate and time/
tokens, rather than counting policy edits or selecting the highest self-score.
No statistical improvement is claimed by this release's unit/live smoke tests.
Keep the baseline when evidence is insufficient; do not pad policy to satisfy a
rubric. To revert a promoted change, use `memory_policy history` then `rollback`
with the current revision. Preview/sync allowlisted instruction copies separately.

## Sources

- [Attached skill's live documentation index](https://docs.typesafe.ai/llms.txt)
- [TypeSafe HTTP API](https://docs.typesafe.ai/api.md)
- [Score semantics and independent questions](https://docs.typesafe.ai/primitives/score.md)
- [Evidence-led proposer/assessor loop](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery.md)
