# Instruction audits and paired outcome trials

These mechanisms support reward-shaped guidance: reward useful outcomes and evidence-led synthesis, not calls, length, model scores or automatic permissions. They do not train model weights. They do not automatically modify a policy, prompt, AGENTS file, skill or template.

## Audit the current instruction stack

Automatic discovery has a separate operator consent because it can disclose prompt, instruction-file and skill text to the configured TypeSafe endpoint. Enable both `typesafeEnabled` and `rsiInstructionDiscoveryEnabled` in the Host plugin configuration, then omit `sources`:

```js
memory_rsi({action: "audit", request: JSON.stringify({
  plan_id: "my-plan",                 // optional pinned contract
  feedback_ids: ["insight-..."]       // optional, explicit immutable selections
})})
```

The invoking Agent is required. The current-scope capture contains:

- rendered system-prompt sections (`renderPrompt` semantics), excluding runtime-context user snapshots, tools and live Agent objects;
- unmanaged text from operator-configured `instructionFiles`; memory-rsi managed policy mirrors are excluded and remain owned by `memory_policy sync`;
- the complete effective catalog of model-invocable skills for the Agent scope/workspace. An incomplete skill catalog is omitted rather than assessed as complete;
- canonical policy, included separately as `canonical-policy`.

No admitted source body is prefix-clipped. System members retain `renderPrompt`'s exact blank-line join bytes; managed/canonical mirror exclusions are explicit and their content is represented once by the separate canonical-policy member. Skill bodies are the exact active `skill.content` returned by the public registry—description/activation metadata is not synthesized into the body. Each member is stored as exact, gapless UTF-8 chunks in local-only `audit` artifacts **before** remote inference. A local capture manifest binds chunk order, byte ranges, hashes, source revisions and owner routes. Remote map/synthesis requests and ordinary tool results use opaque member/unit IDs; raw provider identifiers and routes exist only in snapshot/capture artifacts opened explicitly. Remote requests receive only complete members/chunks, opaque/path-free provider metadata, optional explicitly selected feedback and typed witness choices. Separately collected local routes, owner names, provider path metadata, provider error details, tool arguments/output, arbitrary transcript history, credentials and live objects stay local. Because instruction bodies are transmitted exactly after explicit discovery consent, paths written inside those bodies are part of the disclosed text and are not redacted. A chunk judgment remains chunk-local; multi-chunk members are `mapped-full-content`, not declared coherent as a whole. `whole_instruction_stack_reviewed` remains false unless relationship comparison is genuinely exhaustive.

Automatic audits force `no_git`, generate `local-audit-*` IDs, and install a narrow `shared/efforts/.gitignore` rule before the first raw snapshot. Later generic `memory_sync` therefore cannot stage them. Every raw chunk and map stays below the 128 KiB durable-record envelope; configured transport limits are enforced before HTTP inference. Admission, route, stage or assessment incompleteness blocks synthesis and proposals rather than clipping evidence into a pass.

TypeSafe Choice/Score distributions remain strictly validated and are never normalized locally. The configured retry cap is shared by retryable HTTP statuses and the observed intermittent `TYPESAFE_INVALID_DISTRIBUTION_SUM` provider response. Only that exact typed-response defect receives a bounded retry; unrelated malformed responses fail immediately. Successful audit usage reports logical stage calls, actual network attempts and distribution-sum retries, and aggregates token usage from every paid typed-response attempt.

### Feedback-grounded improvement

Structural coherence is not an outcome signal. `feedback_ids` must name exact fresh `insight` or `trial_review` artifacts; automatic audits never sweep the memory corpus. Observations and plans first pass through `mine` → `reduce`, which preserves evidence status, conservative source-family independence, omissions, success/failure strata and counterevidence.

Feedback classes have different force:

- root tool-dispatch telemetry is metadata only; it does not include arguments/output or establish task failure;
- explicit `observe.context_note` steering is an unreviewed agent-selected summary, not transcript capture or independent evidence;
- plan test/MR/task reports carry their recorded review state, but references are strings—the audit does not open them;
- reduced insights are source-linked hypotheses, never new independent votes;
- paired trial reviews are caller-reported corroboration and cannot independently unlock an instruction change.

Instruction candidates use concrete selected evidence and report uncertainty honestly; a fixed number of reviewed plans or a manufactured counterexample is not required. An audit is an optional aid and does not apply edits. Route a supported change to the real owner: canonical policy (`memory_policy`), managed mirrors (`memory_policy sync`), external AGENTS text, system sections or skills. The ordinary authorized revision-checked writer can also revise, relax or retire process rules without an audit or trial.

### Explicit custom snapshots

The original explicit path remains available and does not call discovery services or read configured files:

```js
memory_rsi({action: "audit", request: JSON.stringify({
  sources: [
    {id: "host-guidance", kind: "system", scope: "selected host contribution", body: "...explicitly selected text..."},
    {id: "project-guidance", kind: "agents", scope: "this repository", body: "..."},
    {id: "coding-skill", kind: "skill", scope: "selected coding work", body: "..."}
  ]
})})
```

Select **1–5 sources** of kind `system`, `agents`, `skill`, `template` or `memory`. Each has exactly `id`, `kind`, `scope`, `body`; bodies are bounded to 32,768 codepoints and the combined snapshot to 64 KiB. Kind/scope are caller declarations, not authenticated authority. No supplied path is followed.

Typed questions inspect each source and pair for contradictions, redundant guidance, misplaced detail, complementary guidance and legitimate scope differences. Low confidence and unknown scope remain uncertain. Resolve authority before wording; prefer one small replacement, merge or retirement, or recommend no change. Never paste another incident-specific rule into every layer or use an audit to weaken plans/platform boundaries.

## Register a trial before recording results

First create a normal revision-bound proposal. `trial_spec` registers one immutable protocol against that exact proposal, including baseline/candidate policy revisions. It is local-only and does not execute agents or invoke TypeSafe.

```js
memory_rsi({action: "trial_spec", request: JSON.stringify({
  trial_id: "coherent-guidance-trial-1",
  proposal_id: "coherent-guidance-candidate",
  hypothesis: "Clarifying evidence review reduces unsupported completion without unnecessary steps.",
  procedure: "Use the fixed task fixtures and same model/tool budgets; counterbalance run order; inspect actual artifacts against the prespecified criteria.",
  stopping_rule: "Run every registered case once per arm, retaining failures and missing measurements; do not stop after favorable outcomes.",
  environment: {agent_model: "pinned-model-id", tool_environment: "tool-and-fixture-version", max_tokens: 10000, max_steps: 100},
  metrics: {outcome: "The task-specific observable acceptance check is met", safety: "Count any permission bypass or fabricated test result", cost_unit: "tokens"},
  cases: [
    {case_id: "task-dev", revision: "sha256:<64 lowercase hex case digest>", split: "development", family: "task-family-a"},
    {case_id: "task-holdout", revision: "sha256:<64 lowercase hex case digest>", split: "holdout", family: "task-family-b"},
    {case_id: "task-control", revision: "sha256:<64 lowercase hex case digest>", split: "control", family: "task-family-c"}
  ]
})})
```

Register at most 64 fixed paired units. Replicates get distinct case IDs but the same family. Keep holdout families separate from development; the report exposes cross-split family overlap. Case digests/references are caller-selected identifiers, not file reads. `environment` fixes model, tool context and budgets for comparable arms; mismatches reject. Supported cost units: `tokens`, `milliseconds`, `usd`, `units`.

Registration proves ordering before result **recording** only; it cannot prove experiments were not run earlier. Freeze real fixtures, randomize/counterbalance run order, preserve artifacts and arrange independent review outside this bookkeeping API. Author and reviewer labels are not attestations.

## Report actual paired measurements

```js
const environment = {agent_model: "pinned-model-id", tool_environment: "tool-and-fixture-version", max_tokens: 10000, max_steps: 100};
const observed = (outcome, cost, reference) => ({
  status: "observed", outcome, safety_violations: 0, cost, environment,
  evidence_refs: [reference], note: "Describe the actual check and limitations, not a model preference."
});
memory_rsi({action: "trial_results", request: JSON.stringify({
  trial_id: "coherent-guidance-trial-1",
  trial_revision: "sha256:<exact returned trial artifact revision>",
  results: [{case_id: "task-holdout",
    baseline: observed(false, 1200, "artifact:baseline-run"),
    candidate: observed(true, 1250, "artifact:candidate-run")
  }],
  review_note: "Reported measurements; references require independent inspection."
})})
```

**Illustration only:** do not copy example numbers as evidence. Run the actual comparison first. For missing/invalid arms use `status:"missing"` or `"invalid"`, `outcome:null`, `safety_violations:null`, `cost:null`, `environment:null`, explicit `evidence_refs` and `note`. Unknown metrics in observed arms remain null and counted as unknown; they are never zero/pass. Omitted cases remain in registered denominators. Duplicate/undeclared cases, nonfinite values, negative costs or violations, invalid revisions and environment mismatches reject.

One immutable result report per protocol prevents silently replacing an adverse/missing report. Plan complete capture before reporting; further observations need another explicit registered trial. Exact protocol/results ancestry remains revision-bound; stale or missing sources do not become fresh evidence.

Summaries expose development/holdout/control and total paired denominators, success rates/deltas, wins/losses/ties, missingness, safety violations/regressions and separate cost deltas. No weighted score can compensate for a boundary violation. Source-family counts are descriptive, not proof of independent samples; no statistical significance or causal improvement is inferred.

## TypeSafe review of reported trials

```js
memory_rsi({action: "trial_review", request: JSON.stringify({results_id: "<trial-results artifact id>"})})
```

This opt-in remote review selects the exact saved protocol, result notes, computed summaries and limitations. TypeSafe judges comparability, reported support and evidence reviewability. It never opens referenced files. The review binds exact result ancestry and is retained as an immutable `trial_review` artifact.

Interpretation cannot turn missing pairs, absent holdout coverage, declared family leakage, uncertain judgments or unknown safety into a positive gain claim. Safety regressions retain the baseline; adverse outcome/cost tradeoffs remain separate. Even `reported-gain-for-review` means **descriptive caller-reported gain for further review**, not verified behavior, causal improvement, achievement credit or promotion permission.

These optional trial reports are an evidence path, not an authorization or mandatory prerequisite to policy changes. They do not become independent votes merely because TypeSafe reviewed them.

## Plan and evaluation changes

Preflight now retains all capability exceptions and recorded review history, including latest rejection/unreviewed status, while omitting outcome claims before work. A refreshed preflight can see a resolution instead of repeatedly flagging an invisible exception. This never changes the pinned requirement or independently verifies the exception.

Candidate evaluation reports comparison coverage and reason codes. Missing, low-confidence, insufficient or all-equivalent comparisons no longer imply a candidate advantage; quality/maintenance concerns require revision. These remain advisory judgments rather than authorization gates. Large plan responses preserve valid structured JSON and receipt/status information with explicit omissions instead of prefix-truncating away coaching.

See [ranked review and validation](rsi-improvement-review.md) for baseline findings and measured delivery evidence.
