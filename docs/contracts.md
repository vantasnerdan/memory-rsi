# Rewards > gates: memory as an execution contract

Lead with purpose, excellent behavior, and evidence-backed achievements. Validation
makes achievements credible; it is not a substitute for guiding decisions beforehand.
Honest capability exceptions are useful information, not an incentive to hide failure.
Do not optimize scores, tool counts, file counts, or unsupported completion claims.

## The three artifacts

- **Template:** reusable practice and evidence expectations, optimized across tasks.
- **Plan:** a concrete task with a complete pinned template snapshot and assigned work.
- **Evidence:** append-only reports, explicit reviews, and accepted exceptions.

Templates and plans are shared Markdown memory entries with structured YAML
`contract` metadata. Markdown below the frontmatter is a generated readable view;
use the plan API to change contracts, not a body-only editor. Generic `memory_update`
refuses these files to avoid discarding the contract. Ordinary memory tools can
still discover/read them; generic `memory_validate` validates their frontmatter,
while `memory_plan validate` validates their contract and achievement progress.

## Agent tools and human CLI

The DSH tools take `action`, an optional `request` **JSON object string excluding
action**, `no_git`, and `allow_non_main_branch`. The terminal CLI takes one JSON
object **including action**. Both reach the same implementation:

```sh
memory plan --base /path/to/memory --request '{"action":"templates"}'
memory plan --base /path/to/memory --request '{"action":"review","template_id":"coding"}'
memory plan --base /path/to/memory --request - < request.json
```

The CLI's `plan` and `policy` commands always return JSON. Read/review operations
may create a directory/lock sidecar, but not a plan, policy, or instruction body.

### Discover, review, and create

`templates` lists built-in `general` / `coding` and saved shared templates.
`review` returns the full template, revision, and runnable `create_example` /
`update_examples`. Read those requirements and fill the actual task:

```json
{
  "action": "create",
  "plan_id": "fix-timeout",
  "template_id": "coding",
  "template_revision": "<revision from review>",
  "task": {
    "title": "Prevent a hung timeout request",
    "purpose": "Return control reliably when a subprocess stops responding",
    "good": "Regression test reproduces the hang before the fix and proves timely cancellation after it",
    "steps": ["Trace cancellation ownership", "Implement focused cleanup", "Exercise timeout and success paths"],
    "work_items": [
      {"id":"implementation","title":"Fix and validate cancellation","owner":"lead","requirement_ids":["outcome","ast","lsp","design","gitops","verification"]}
    ]
  }
}
```

Task `achievements` and `validation` can add `{id, description, evidence}`
requirements. Task `steps` and `boundaries` add nonempty strings. All requirements
must be assigned to work items; new task requirements cannot replace pinned IDs.
The coding template emphasizes AST/LSP, separation of concerns, single
responsibility, avoiding god files/functions, behavioral checks, and authorized GitOps.

Every delegation carries the **same plan ID, latest revision, and work-item IDs**.
Subagents read that plan before acting. The DSH tool records the invoking session ID
as the plan actor; CLI callers use `AGENT_ID`. Ownership and actor labels coordinate
work and provide provenance, not authentication or access control.

### Earn achievements with evidence

Use `read` with `plan_id` before each update. Example report:

```json
{
  "action":"update", "plan_id":"fix-timeout", "revision":"<latest revision>",
  "work_item_id":"implementation", "status":"in_progress",
  "evidence":[{"id":"regression","requirement_id":"verification","summary":"Timeout regression and success-path tests pass","reference":"tests/test_timeout.py; pytest output attached to the task"}]
}
```

A subsequent, separate update records an actual review judgment:

```json
{
  "action":"update", "plan_id":"fix-timeout", "revision":"<new revision>",
  "work_item_id":"implementation",
  "reviews":[{"evidence_id":"regression","verdict":"accepted","note":"Inspected the assertion and observed pre-fix failure / post-fix success"}]
}
```

The review is auditable evidence of a judgment, not automated verification that an
artifact is true. No points or tool-call counters are awarded. The same actor may
review in a separate operation; independent reviewers are encouraged, not enforced.

Unavailable capabilities use `exceptions: [{id, requirement_id, reason, alternative}]`,
then `exception_reviews: [{exception_id, verdict, note}]` in a separate update.
Completion needs reviewed evidence or accepted exceptions for all assigned
requirements, with no unreviewed exceptions. `validate` reports missing requirements
and progress without treating unfinished work as invalid. Finally update `status`
to `complete` for finished work items. Honest exceptions remain visible.

Optimistic revisions and a cooperative interprocess lock prevent lost updates.
Re-read after conflicts and merge your scoped contribution. Goal, requirements,
assignments, and work-item topology are immutable after creation in this version;
create an explicitly linked successor plan for a scope/ownership change rather
than silently weakening an active contract.

### Improve the template, not every plan

`save_template` takes `template_id`, `revision`, and `template` (the exact structure
returned by review). Use JSON `null` as revision for a new named template; use the
reviewed revision to update an existing template or deliberately override a built-in.
Existing plans retain their full old snapshot. Propose shared improvements explicitly;
seek human review before weakening requirements. This is guidance, not a hidden
approval mechanism or automated classifier.

## Editable system-prompt policy

Canonical source: `<base>/shared/policies/agent-policy.md`, plain UTF-8 Markdown
rather than a generic frontmatter entry. The shipped `default_policy.md` applies
until a canonical file is explicitly saved. Humans may edit the canonical file,
or use the same policy API as agents:

```sh
memory policy --base /path/to/memory --request '{"action":"read"}'
# Pass the full next body, current SHA revision, actor, and reason:
memory policy --base /path/to/memory --request - < policy-update.json
```

```json
{"action":"update","body":"# Rewards > gates\n\nOur team's policy...\n","expected_revision":"sha256:<from read>","actor":"human","reason":"Reviewed team policy improvement"}
```

`history` lists saved revisions and operation events; add `revision` to inspect an
exact snapshot. `rollback` takes a historical `revision`, current `expected_revision`,
`actor`, and `reason`. Rollback is a new audited operation, not destructive history
rewriting. Direct human edits become rollback snapshots at the next API update;
reads alone do not record them. Git can separately track edits made outside the API.

DSH uses one absolute memory base resolved at plugin load using the CLI's normal
precedence (explicit base, environment, config file, cwd/default). The tools and
prompt reader share it. Reload the plugin after changing that base/config.
Policy text itself is re-read on every prompt assembly. Limits are 32,768 Unicode
code points / 131,072 UTF-8 bytes; oversized, invalid, or nonregular sources produce
visible fallback guidance rather than injecting a truncated policy.

This changes the plugin-owned prompt contribution, not the entire DSH prompt,
prior conversation messages, permissions, or shipped presets. Higher-priority
platform instructions still apply. No agent-supplied `actor: human` is proof of
human consent; approval/authentication must come from the host or actual human review.

### Append managed instructions to AGENTS-like files

An operator configures explicit `instructionFiles` paths in the DSH plugin config.
No instruction files are modified by default. For humans, the equivalent CLI
allowlist is repeatable `--instruction-file /project/AGENTS.md`.

1. `sync` with `target` previews a diff without changing the target body.
2. Inspect the proposed change and preserve both returned revisions.
3. Repeat `sync` with `apply:true`, `expected_revision`,
   `expected_target_revision`, `actor`, and `reason`.

The managed `<!-- memory-rsi:policy:begin -->` / `end` block includes canonical
source, revision, and policy text. Existing human content outside it is preserved.
Re-sync after policy updates; copies deliberately do not pretend to update themselves.
Malformed/duplicate markers and stale target revisions are refused. Only configured
paths are accepted; symlink targets are rejected. This is suitable for `AGENTS.md`,
`CLAUDE.md`, or an identity document **you have confirmed the relevant agent loads**;
there is no promise that every agent product recognizes every filename.

## Persistence and operational limits

New contract/policy writes are atomic and distinguish file success from Git commit
and remote sync. No remote means successful local-only persistence. No upstream
means sync is not configured. Failed pushes do not erase saved work or trigger a
pull/rebase of unrelated user changes. Commits include only owned paths; instruction
files outside the memory repository are not silently committed into another project.
`no_git` skips publication. Resolve staged-owned-file conflicts before retrying.

Locks are POSIX/cooperative, not protection against malicious concurrent filesystem
replacement or editors ignoring locks. Policy history involves several atomic files,
not a cross-file transaction: a crash may leave an orphan snapshot or missing audit
event, while canonical replacement remains whole. History records and hashes are
integrity checks, not tamper-proof authentication.
