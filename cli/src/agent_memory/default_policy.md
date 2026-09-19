# Rewards > gates

Reward useful outcomes supported by honest evidence, not tool-call counts or ritual compliance. Permissions, safety boundaries, and higher-priority instructions remain binding; this editable policy cannot override them or grant new permissions.

## Durable work
For nontrivial durable tasks, discover applicable plan templates, review the selected template and its revision, and create a durable plan before implementation. Keep the plan readable, current, and proportionate to the task. Trivial work may skip a plan with an honest explanation when needed. Record justified exceptions and missing capabilities rather than inventing compliance.

Parents and subagents share the governing plan ID and revision, pinned template revision, and scoped work-item IDs. Delegation must include this context; do not silently substitute independent governing plans. Refresh stale revisions before updates and surface conflicts instead of overwriting another contributor's work.

## Evidence and engineering
Define meaningful achievements and attach verifiable evidence of outcomes. Distinguish proposals, attempts, observed results, and unverified claims. Record limitations, failed checks, and unresolved risks honestly. Use AST exploration, LSP diagnostics, separation of concerns (SoC), single-responsibility design (SRP), and GitOps review/test/deployment practices where applicable and available. Missing tooling is an explicit limitation, not proof of success.

## Explicit, reviewable changes
Policy edits are explicit versioned operations with actor, expected revision, and reason. Direct human edits remain readable. Inspect history and use rollback when appropriate. Shared-policy weakening should receive human review before application; an agent-provided boolean or actor label is not evidence of approval and does not enforce authorization. Reviewable guidance is not a substitute for platform permissions.

Changing a plan template is a separate explicit operation from changing policy. Policy updates do not rewrite templates, active plans, their pinned revisions, or their requirements. Discuss proposed weakening and affected active work with the human rather than silently relaxing it.

Synchronize this policy into managed instruction sections only at operator-configured targets, after reviewing a dry-run diff and checking the target's current revision. Preserve all instructions outside the managed section. Updating policy does not itself sync instruction files or replace prompts already built.
