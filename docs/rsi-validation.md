# RSI 0.4.0 validation — release validation in progress

## Governing contracts

- Original: `memory-rsi-typesafe-v04`.
- Explicit expanded-scope successor: `memory-rsi-learning-v04`.
- Read the contracts for current completion/evidence revisions; acceptance scope
  and the pinned template remain unchanged while evidence reports are appended.
- Both pin coding template
  `6a62b8c1cb460f1256385c0d484a1ef57d3008ed74c05f3d7e1ee1c247c7c79a`.
- Goal includes actual mounted plugin/API validation and reviewed delivery to main.

## Frozen source results

`npm test && PYTHONPATH=cli/src python3 -m pytest -q cli/tests && python3 scripts/check-language-servers.py && git diff --check`
completed with exit 0 (final parent job `bash-47`, Node log `/tmp/memory-rsi-final-node-tests.log`):

- **277 Node tests passed.**
- **1,118 Python tests passed.**
- Python AST parsed **39 modules**; actual pylsp/typescript-language-server symbol
  queries succeeded across **28 modules**. This is navigation evidence, not a claim
  that document symbols establish correctness or replace diagnostics/tests.
- Registered plugin + real Python CLI lifecycle covers create/preflight,
  reflect/prepare/propose/evaluate, preview/apply/rollback, unchanged pinned plans,
  session observation, cross-session isolation, paged mapping/resume/cache reuse,
  reduction, focused issue retrieval and source-linked whole-section proposals.
- Protocol/control-flow test network responses are mocked. Full source validation
  is separate from live semantic smoke and from empirical outcome improvement.
- The initial narrower implementation passed 185 Node / 1,047 Python tests; the
  larger totals above include successor functionality and adversarial regressions.

## Independent adversarial review

Reviewer independently reproduced and verified repairs for:

1. **P1 Git publication race:** commit under writer locks, push the captured exact
   SHA after release; later explicit local-only edits never enter that publication.
2. **P1 stale source laundering:** an insight could cite an outdated plan while a
   proposal with no direct plan pins appeared fresh. Bounded, memoized transitive
   plan/hash checks now reject stale/unavailable ancestry at proposal, evaluation
   recording and promotion. Historical policy-independent extraction remains usable
   through explicit re-reduction against current policy. Cooperative writer locks
   protect validation/save; non-cooperating direct edits are outside that guarantee.
3. **Wrong-revision preflight and stalled credentials:** use the exact saved plan
   revision; credential resolution and inference share a bounded deadline.
4. **Task-specific validation omission:** assigned task validation descriptions and
   evidence now appear in mapping context, with explicit oversized-context coverage.
5. **Markdown fence parsing:** a code-fence prefix with trailing content cannot
   close a fence and turn example headings into editable policy sections.
6. **Telemetry name privacy:** syntactically valid unknown tool names are not safe
   metadata. Retain only registry-confirmed names in the exact agent scope;
   unknown/unverified names use fixed anonymous buckets without sequence claims.
   Arguments, outputs, messages, raw errors and session history are never inspected.
7. **Artificial effective policy ceiling:** remove repeated policy text from reducer
   state/choice criteria. Independent fixtures preserve all 25,012 / 32,768 characters;
   requests were 33,622 / 41,536 bytes and artifacts 37,426 / 45,340 bytes. No storage
   limit was weakened and no line-count target introduced.
8. **Concurrent map writes:** two sessions racing after the same cache miss converge
   on the immutable winner. Both return its receipt; duplicate paid inference is
   honestly counted and the losing response is not falsely claimed as persisted.
   Reviewer verified through registered tools + real CLI with barriered mocked HTTP.
9. **Large result loss:** a 158,420-character cached mining response previously lost
   all work IDs/cursors. It now compacts to 36,124 characters while retaining all 128
   exact receipt identities, resume, next cursor, coverage and publication metadata.
10. **Budget/response privacy:** reject oversized evidence before paid inference;
    unavailable attempts remain explicit. Project only documented typed response
    fields; arbitrary vendor extensions and credential echoes are not retained.

Initial expanded-scope independent check: **75 tests passed**, no remaining reproduced
P1/P2 finding in the inspected successor. This is a scoped review, not a security
proof, calibrated classifier result or permission to promote policy automatically.

Development failures were not counted as passes: one early reserved pytest parameter
blocked collection; one earlier integration run overlapped source edits; one new
telemetry integration fixture omitted required rootCallId and correctly captured no
valid dispatches. Each was corrected and the frozen suites rerun.

## Live runtime evidence so far

- Original candidate packed as `0.4.0-rsi-rc1`; private CLI upgraded to 0.5.0.
- The existing web profile opts into the dedicated TypeSafe `/v1/systemone` adapter
  via the existing `TYPESAFE_API_KEY` credential reference. No key was read or printed.
- Preserving the existing Cordis runtime row ID (`memory-rsi-runtime-5902160`) while
  switching its module URI activated the tool; the earlier renamed row had not.
- Actual mounted `memory_rsi status` reported enabled/configured.
- Actual preflight of the successor contract returned **`jev-1.13.0`**, nine typed
  judgments, 4,631 input / 339 output tokens, ~845.6 ms and immutable receipt
  `preflight-70b3c00294bb4bf0a32248b60bb32afd`.
- Jev's low-confidence proportionality feedback was reviewed as uncertain coaching,
  not a gate or a target to optimize. Scope directly reflects the user's expansion;
  independent responsibilities and explicit limitations keep it reviewable.
- Removed the incompatible Jev `openai-completions` custom-model entry only after
  dedicated operation succeeded. The normal reasoning model remains unchanged.
- Temporary Host-only probe `rsiprb-1` was stopped; definitions remain available.
  Its attempted nested dispatch was unavailable in the restricted Service surface;
  direct registered tool invocation provided the actual validation instead.
- No replacement web server or shipped preset edit was used.

## Expanded candidate live checks

- Packaged `0.4.0-rsi-rc2` activated in the existing GUI; status confirms telemetry
  enabled/active/local-only. Real private CLI reports 0.5.0. A first install attempt
  without build isolation failed because Hatchling was absent; the isolated build
  installed successfully. This was a tooling prerequisite failure, not a passed check.
- All three labelled diagnostics returned `jev-1.13.0` with expected distinctions:
  useful tested work aligned; tool-count/permission-bypass and embedded-instruction
  adverse fixtures conflicted. No operational plan or policy changed.
- Live corpus returned four real contracts. Explicitly selected completed plan
  `memory-rsi-release-validation` yielded two maps in two calls; repeat mining reused
  both exact map IDs with **zero calls/tokens**. Reduction used one call and produced
  `insight-c84eaeeccb6642bab34e5c93e45bbb87`, conservatively classified local/uncertain
  with one source family and investigation rather than a new policy rule.
- Explicit observation `observation-a6b68604eb434f1d8c2b7dc0e5d315d1` saved three actual
  successful root dispatches and a labelled agent-selected review summary, excluding
  six RSI calls. No raw arguments/output/history were captured.
- Mining that observation exposed excessive bookkeeping fragmentation (147 scalar
  rows / ten units). One unit was unavailable; one explicit retry succeeded. Cause
  was not inferred because the old wrapper discarded the safe error code. This
  triggered the coherent-projection, safe-diagnostic, honest-usage and cursor-identity
  refinements verified below.

## Live policy review: baseline deliberately retained

- Prepared and defined whole-section candidate `coherent-section-review-v04`, with
  exact source insight binding and all unrelated policy bytes preserved.
- Real evaluation `evaluation-ab89caf3b5a14b1b99a5161430b8de0f` returned preserved
  obligations probability 0.90 and generally preferred the candidate wording, but
  **outcome support was insufficient (0.94)**. Length was descriptive: 2,527→2,792
  characters, no small-size target or automatic promotion.
- Reviewed exact promotion preview with `apply:false`. The selected historical
  source does not establish a recurring cross-task need or measured benefit.
  **Canonical policy remains unchanged**; a model preference is not evidence of
  improvement. The immutable candidate remains available for later review.

## Final refinement and packaged/live validation

- Independent reviewer reproduced and closed selection/page/model cursor skipping,
  error-code accessor/proxy disclosure, nested-extension loss, missing capture-state
  context and compact-output coverage loss. Eight grouped adverse cases and 92 focused
  regressions passed; these are mechanism checks, not classifier-quality estimates.
- Added safe static `validation_code` enums distinguishing typed-response constraints
  without provider text, values or keys. Primary errors and validation thresholds are
  unchanged. All 103 transport tests pass. One full run exposed a test expecting one
  descriptor query; it now asserts one query each for the two allowed diagnostic fields.
- Packaged RC3/RC4 Host modules imported successfully through the actual **named**
  Cordis exports. An initial smoke incorrectly assumed a default export and failed;
  source inspection corrected the check. All **40 installed CLI module/policy assets**
  match source hashes; private CLI version is 0.5.0.
- The same live observation now maps as **four coherent units instead of ten scalar
  batches**, without omitted context. Exclusions and incomplete full-source coverage
  remain explicit. A fourth-unit response failed typed validation; it was persisted
  as unavailable, not cached as success. One bounded resume completed that unit.
  Its earlier failing constraint cannot be recovered because raw responses were not
  retained; later failures now have safe constraint diagnostics. No tolerance changed.
- A full second pass reused **all four exact maps, zero inference calls/tokens**.
- Hierarchical live reduction combined the earlier insight's two roots with those
  four maps: **six roots, two source families, one issue, one API call**. Receipt
  `insight-c497f62f1a2e43e5bb4c1de57350194c` has current transitive lineage. Jev routed
  the mixed/uncertain evidence toward **tool fixes**, not policy growth; notes stayed
  unreviewed and successful counterexamples remained visible.
- Graph-only rebuild: 3,470 nodes / 8,349 edges; lexical `safeAssessmentCode` query
  found `lib/rsi-errors.js`. No embeddings or instruction injection. Optional native
  FTS was unavailable; the supported lexical graph query worked without installing it.

## Delivery

Source and live validation are complete. Final Git publication/CI and contract
completion evidence will be recorded after those operations actually succeed.

## Honest limits

- Three synthetic diagnostic cases are not calibration or general injection defense.
- The 1,024-root hierarchical reduction test uses mocked local I/O; serial real CLI
  traversal can be slow. Prefer bounded cohorts and heed coverage/deferred guidance.
- A corpus ID cursor is not a change feed; rescan to find modified/earlier IDs.
- `jev-latest` may change invisibly: pin a model or deliberately refresh cached maps.
- Root-only telemetry misses nested child failures and cannot infer semantic command
  success from transport success, or classify code-free denials without raw errors.
- Reviewed claims and repeated source families are not independent verified truth.
- No statistical or longitudinal agent-performance improvement is established here.
