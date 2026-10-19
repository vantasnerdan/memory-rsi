# Review: measurable, reward-shaped policy improvement

## Scope and baseline

Reviewed `c8ea220` (the TypeSafe/RSI implementation) and `1c42c79` (delivery evidence), against `origin/main` at `1c42c79808b1dfa8a9827af06d75e3b04ded9cbe`.

The design already has strong foundations: opt-in remote assessment, strict typed transport, immutable artifacts, revision-bound transitive lineage, source-linked learning, explicit promotion, and no claim that model scores grant permissions or prove improvement. Preserve these properties.

Baseline checks: **277 Node tests**, **1,118 Python tests**, Python AST parsing of **39 modules**, and actual Python/JavaScript LSP document-symbol queries across **28 modules** passed. Symbol navigation is not a claim of full LSP diagnostics or correctness. Optional GitNexus FTS was unavailable; graph-only indexing and lexical queries worked without embeddings.

Live TypeSafe `jev-1.13.0` preflight of the governing coding contract judged alignment likely (0.78), reward design 1.99/2 and evidence design 1.98/2. Proportionality was uncertain (confidence 0.06); change-review coverage was also uncertain. Those are coaching signals, not pass/fail gates or achievements. The scope comes from the explicit user request; canonical policy is not implicitly promoted and no rituals were added to optimize scores. Three labelled synthetic diagnostics distinguished useful work, ritual/permission bypass, and an embedded assessor injection. Three cases are not calibration or evidence of better agent behavior.

Governing plan: `rsi-measurable-improvement-20260919`, pinned coding template `6a62b8c1cb460f1256385c0d484a1ef57d3008ed74c05f3d7e1ee1c247c7c79a`. The plan/evidence lives in local memory; private memory and credentials are not release artifacts.

## Ranked improvements

| Rank | Improvement | Observable benefit / appropriate evidence |
| --- | --- | --- |
| **1 — implement** | Audit the selected instruction stack together: prompt, AGENTS, skills, canonical policy, templates and factual memory. | Exact source snapshots/hashes; typed within-source and cross-source conflicts, redundant guidance and scope exceptions; selected-scope coverage, not a claim to inspect unseen layers. |
| **2 — implement** | Register paired behavioral trials before recording results, then review their reported outcomes with TypeSafe. | Exact baseline/candidate/protocol bindings, predefined holdout/control/development cases and fixed environment; paired useful-outcome, safety and cost denominators/deltas, with missingness and independence limits. No score-only improvement claim. |
| **3 — implement** | Close the plan feedback loop. | Accepted/rejected/pending capability exceptions and their review history reach preflight; large plan responses preserve valid JSON, preflight status and durable receipt rather than truncating away warnings. |
| **4 — implement** | Make RSI recommendations evidence-conservative. | Uncertain/irrelevant comparisons and poor synthesis do not become positive recommendations; contradicted/missing witnesses do not motivate rewriting; linked plan/observation sources are not counted as independent votes. |
| 5 | Expand labelled, held-out evaluator diagnostics. | False positives, false negatives, abstentions and case-level drift by resolved model/rubric; do not tune and report on the same cases as an unbiased test. |
| 6 | Pin resolved evaluator versions and detect drift before reusing cached judgments. | Reproducible model/rubric identities, deliberate re-evaluation cohorts, changes in calibration and disagreement. An alias alone is not stable semantics. |
| 7 | Monitor after explicit promotion and define rollback criteria. | Later task outcomes linked to the active policy/version; adverse behavior and cost regressions remain visible alongside successes. |
| 8 | Maintain explicit ownership/placement across instruction layers. | One maintained owner per enduring rule, references where appropriate, separately versioned skills/templates; fewer unresolved contradictory obligations, not a target line count. |
| 9 | Strengthen independent evidence review. | Review actual artifacts and adverse cases, not author labels or repeated summaries. Separate reported facts, independent checks and unauthenticated reviewer metadata. |
| 10 | Measure useful improvement per maintenance/inference cost over time. | Outcome improvements considered separately from safety and cost; bounded cohorts and no endless policy or skill accumulation. |

## Reproduced baseline defects

- `contractState(..., outcomes=false)` omitted all exceptions and their reviews even though preflight questions explicitly accepted justified unavailable-capability exceptions. A resolved exception and no exception yielded the same assessor input.
- `interpretEvaluation` treated absence of a *confident baseline preference* as sufficient comparison support. With other globals favorable, all-insufficient or low-confidence cases could yield `candidate-for-review`. Several quality judgments were computed but did not affect that recommendation.
- Plan creation appended preflight to a potentially large plan then used prefix string clipping; at the production size limit the response could be invalid JSON and lose its assessment warning/receipt.
- Reduction source-family counting did not join an observation to its already-bound plan. The same task reported as plan evidence and telemetry could count twice.
- Contradicted sufficiency and missing selected witnesses could still motivate a policy rewrite. Raw model output must remain inspectable, but unsupported interpretation should request investigation rather than rewriting.

These are implementation defects/opportunities, not reasons to append another rule to every system prompt, AGENTS file or skill. The top four changes address mechanisms and preserve authority boundaries.

## Measurement limits

A TypeSafe judgment evaluates selected documents; a typed answer is not a verified outcome. A trial records caller-reported measurements and references, not authenticated execution or causality. Registration is verifiable before result **recording**, not necessarily before the caller ran an experiment. Family labels and content hashes reduce obvious duplication but do not prove independence. Historical evidence remains distinct from current-adoptable evidence.

No canonical policy is automatically changed by audits, trials, interpretation or scores. Preserve the baseline where support is weak; use explicit review/versioned promotion only for a justified candidate. The remaining ranked items are future work, not claimed implemented capabilities.

## Implementation and independent review

All top-four mechanisms are implemented. The Host module and bundled CLI are **0.8.0**, and the preflight/evaluation rubric remains `memory-rsi/2`. Instruction discovery/audit schemas advance independently: exact capture and evidence-grounded staged mapping live in `rsi-source-discovery`, `rsi-instruction-feedback`, `rsi-instruction-stages` and `rsi-instructions`; trial review, deterministic trial-schema/statistics, assessment retention and contract output remain focused modules. Canonical policy and pinned template requirements are unchanged.

Two scoped reviewers inspected the changes and reproduced regressions. Independent review caught and closed two additional defects before delivery: disabled oversized audits/trials could attempt to persist more than the 128 KiB record budget, and regex coercion admitted non-string source IDs. Shared status-independent input retention now preserves an explicit digest/omission; number/array/null IDs reject. An independent rerun reduced the failing 165,057-byte disabled audit to a 957-byte honest `disabled/not-assessed` record with no credential access. No remaining reproduced P1/P2 blocker was reported in the inspected scope. Review is not a security proof.

## Reproducible local validation

The full frozen candidate passed:

- **373 Node tests**, including actual registered-tool → real Python CLI staged-capture integration with mocked transport.
- **1,157 Python tests**; only the existing pytest-asyncio fixture-scope deprecation warning appeared.
- Python AST parsing of **41 modules** and real pylsp/TypeScript LSP document-symbol queries across **34 selected modules**.
- `git diff --check`, package dry-run, packaged Host import, and private CLI upgrade/version check.

Run the targeted before/after behavior replay against an immutable baseline checkout:

```sh
git worktree add --detach /tmp/memory-rsi-reviewed-baseline-1c42c79 1c42c79
node scripts/measure-rsi-regressions.mjs /tmp/memory-rsi-reviewed-baseline-1c42c79
```

Expected behavior was observed in **2/10 baseline cases → 10/10 candidate cases**: eight targeted reproduced defects now behave correctly, with two positive/boundary controls preserved. The script prints every case, before/after observations, and explicit measurement limits. This is a defect-selected mechanism regression suite, **not a held-out accuracy benchmark, policy-induced behavioral trial or statistical generalization claim**.

## Live TypeSafe / existing-runtime checks

The packed candidate was activated through the existing Host row, retaining its ID and configured credential reference; no secret was read or printed. The private CLI reports 0.6.0. Existing runtime tool calls succeeded; the existing GUI listener returned its expected unauthenticated HTTP 401 (reachability/authentication evidence, not a screenshot or browser-render test). No replacement server, shipped preset edit or UI rebuild was involved.

Every live assessment below returned resolved model **`jev-1.13.0`**:

| Check | Observed result | Durable receipt |
| --- | --- | --- |
| Selected instruction-stack + plan audit | Five sources including canonical policy, ten pairs; no confident conflict, two duplicate findings and eight uncertain judgments; plan aligned probability 0.83. | `audit-739bf23806e9409fb1b1910efe8bba52` |
| Updated plan preflight | Reward 2/2, evidence 1.98/2; alignment/proportionality uncertainty remains explicit as `strengthen-plan`. | `preflight-209d1e52930b434fa715c0c0ded1bc85` |
| Coherent existing-section policy hypothesis | Preservation favored, **outcome support insufficient (1.0)**; no comparison cases, synthesis/maintenance concerns; `needs-outcome-evidence`. | `evaluation-bed2e400722648eabdc8ecfd26b9b01e` |
| Missingness trial lifecycle | Two registered cases, zero reported/observed pairs, all metrics missing; `needs-trial-evidence`, support insufficient (1.0). | `trial_review-c2401810c0514bcdb95058a4dac5b97c` |
| Before/after synthetic diagnostics | Same three labels distinguished in both runs: useful outcome aligned; ritual/bypass and embedded-injection conflicted. | Ephemeral diagnostics; no records or policy changes. |

**Assessment review matters:** the audit correctly noticed the managed AGENTS mirror overlaps canonical policy, but this is intentional synchronization, not a reason to delete a required managed section. Its second confident duplicate finding (engineering guidance versus an ordinary-entry CLI behavior excerpt) is disputed by source inspection: related GitOps vocabulary does not establish redundant obligations. No instructions were deleted to satisfy these model judgments, and no additional inference was used to chase a favorable score. This is concrete evidence for the remaining calibration/false-positive work, not hidden success.

The live trial was explicitly a **missingness probe**, not a completed behavioral experiment. Its protocol `rsi-v05-missingness-probe` and immutable result `trial-result-e81a45e83b8673b7a27c0db06b4c9de19d051fcbbed0e597a5a3fbe4df41fa81` retain the empty/adverse-to-claim evidence. No paired agent runs or outcome gains are invented. Positive/safety/cost/missingness control-flow paths are covered by deterministic cross-language fixtures, separately from live semantic smoke.

**Canonical policy retained:** `sha256:c21ab55e40fe5dafacd747fe83b9d757954f8e6b172b0b592fe4d17e66021998`. The 93-character existing-section hypothesis was not promoted: TypeSafe preference and implementation fixes do not establish a benefit from changing canonical wording. Existing AGENTS and skills were not expanded; the existing Host RSI guidance paragraph was coherently replaced to describe the new interfaces.

Local receipts are durable session evidence, not public independent attestations. Publication/merge and CI are verified separately in the PR and final delivery report; neither local tests nor this document predicts their outcome.
