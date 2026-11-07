import { bytes } from "./rsi-assessment-data.js";

const MAX_FEEDBACK_ARTIFACTS = 16;
const MAX_FEEDBACK_BYTES = 32 * 1024;
const validId = value => typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/u.test(value);
const pick = (value, keys) => Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key, value[key]]));

function feedbackClass(witness) {
	const type = witness?.report?.type;
	if (type === "observation-telemetry") return "tool-outcome-metadata";
	if (["observation-note", "observation"].includes(type)) return "user-or-agent-steering-summary";
	if (type === "evidence") return witness.report.latest_review === "accepted" ? "reviewed-plan-test-or-review-evidence" : "unreviewed-plan-evidence";
	if (type === "exception") return "plan-exception";
	if (type === "planned") return "planned-work";
	return "other-source-linked-report";
}

function projectIssue(issue, artifact, artifactComplete) {
	const witnesses = Array.isArray(issue.witnesses) ? issue.witnesses.map(witness => ({
		map: pick(witness.map, ["id", "revision"]), source: pick(witness.source, ["kind", "id", "revision"]),
		report: pick(witness.report, ["type", "id", "requirement_id", "work_item_id", "latest_review", "review_count", "tool"]),
		evidence_status: witness.evidence_status, signal: witness.signal,
		witness: witness.witness ? pick(witness.witness, ["id", "path", "start", "end", "quote", "literal", "citation_kind"]) : null,
	})) : [];
	const counts = issue.counts ?? {};
	const coverage = issue.coverage ?? {};
	const uncertainties = Array.isArray(issue.uncertainties) ? issue.uncertainties.filter(value => typeof value === "string") : [];
	const counterevidence = Array.isArray(issue.counterevidence) ? issue.counterevidence.map(value => {
		const source = pick(value.source, ["kind", "id", "revision"]);
		const linked = witnesses.find(witness => witness.witness?.id === value.witness_id && witness.source?.kind === source.kind && witness.source?.id === source.id && witness.source?.revision === source.revision);
		const reviewedObserved = feedbackClass(linked) === "reviewed-plan-test-or-review-evidence" && linked?.evidence_status === "observed";
		return { map: pick(value.map, ["id", "revision"]), source, witness_id: value.witness_id ?? null, signal: value.signal,
			evidence_status: linked?.evidence_status ?? "unknown", reviewed_observed: reviewedObserved };
	}) : [];
	const witnessClasses = [...new Set(witnesses.map(feedbackClass))].sort();
	const blockingUncertainties = uncertainties;
	// Selected concrete observations can nominate a change; absent corroboration is
	// carried as uncertainty, never mistaken for proof or permission.
	const eligible = !["investigate", "retain"].includes(issue.operation)
		&& witnesses.some(witness => ["observed", "unreviewed"].includes(witness.evidence_status) && witness.witness
			&& ["evidence", "observation-note", "observation"].includes(witness.report?.type));

	return {
		feedback_id: `${artifact.id}:issue:${issue.key ?? "unknown"}`, artifact: { id: artifact.id, revision: artifact.revision, kind: artifact.kind },
		key: issue.key ?? null, mechanism: issue.mechanism ?? null, topic_excerpt: issue.topic_excerpt ?? null,
		destination: issue.destination ?? "uncertain", operation: issue.operation ?? "investigate", target_section: issue.target_section ?? null,
		generality: issue.generality ?? "uncertain", sufficiency: issue.sufficiency ?? "insufficient",
		independent_source_count: issue.independent_source_count ?? 0, witnesses, witness_classes: witnessClasses, counterevidence,
		counts, coverage, uncertainties, eligibility_blockers: blockingUncertainties, eligible_for_change_review: eligible, automatic_promotion: false,
	};
}

function projectTrial(artifact) {
	const interpretation = artifact.data?.interpretation ?? {};
	const summary = artifact.data?.state?.summary ?? artifact.data?.summary ?? null;
	return {
		feedback_id: `${artifact.id}:trial-review`, artifact: { id: artifact.id, revision: artifact.revision, kind: artifact.kind },
		kind: "paired-trial-review", summary, interpretation: pick(interpretation, ["disposition", "flags", "uncertain", "measurement_scope"]),
		eligible_for_change_review: false, corroborating_only: true, automatic_promotion: false,
	};
}

function insightCompleteness(artifact) {
	const coverage = artifact.data?.coverage ?? {};
	const groupsTotal = coverage.groups_total;
	const groupsSelected = coverage.groups_selected;
	const deferredGroups = Array.isArray(coverage.deferred_groups) ? coverage.deferred_groups.length : null;
	const complete = artifact.data?.status === "complete" && Number.isSafeInteger(groupsTotal) && groupsTotal > 0
		&& groupsSelected === groupsTotal && deferredGroups === 0;
	return { complete, status: artifact.data?.status ?? "unknown", groups_total: groupsTotal ?? null, groups_selected: groupsSelected ?? null, deferred_groups: deferredGroups };
}

/** Load only explicitly selected, immutable feedback artifacts; never scan transcripts or tool payloads. */
export async function loadInstructionFeedback(local, ids = [], args = {}, exec = {}) {
	if (!Array.isArray(ids) || ids.length > MAX_FEEDBACK_ARTIFACTS || new Set(ids).size !== ids.length || ids.some(id => !validId(id))) throw new Error(`feedback_ids must contain at most ${MAX_FEEDBACK_ARTIFACTS} distinct artifact IDs`);
	const items = [], refs = [], classes = new Set(), artifacts = [];
	for (const id of ids) {
		const { artifact } = await local({ action: "read", id }, args, exec);
		if (!artifact || !["insight", "trial_review"].includes(artifact.kind)) throw new Error("feedback_ids must select insight or trial_review artifacts");
		if (artifact.freshness?.stale !== false) throw new Error("Selected feedback is stale or its exact lineage is unavailable; remine or repeat the trial review");
		refs.push({ id: artifact.id, revision: artifact.revision });
		const completeness = artifact.kind === "insight" ? insightCompleteness(artifact) : { complete: true, status: "not-applicable" };
		artifacts.push({ id: artifact.id, revision: artifact.revision, kind: artifact.kind, completeness });
		if (artifact.kind === "insight") for (const issue of artifact.data?.issues ?? []) {
			const projected = projectIssue(issue, artifact, completeness.complete);
			items.push(projected);
			for (const witness of projected.witnesses) classes.add(feedbackClass(witness));
		} else {
			items.push(projectTrial(artifact));
			classes.add("paired-trial-review");
		}
	}
	const remote = { items, provenance: "Explicit immutable insight/trial selections. Insights summarize source-linked observation or reviewed-plan mappings; trial reviews summarize caller-reported paired measurements. Neither proves causality or grants permission." };
	if (bytes(remote) > MAX_FEEDBACK_BYTES) throw new Error("Selected feedback exceeds the staged audit context budget; select fewer insight or trial-review artifacts. No feedback was clipped.");
	return {
		remote, refs, manifest: { artifacts, classes: [...classes].sort(), items_total: items.length,
			complete_artifacts: artifacts.filter(artifact => artifact.completeness.complete).length,
			incomplete_artifacts: artifacts.filter(artifact => !artifact.completeness.complete).length,
			eligible_items: items.filter(item => item.eligible_for_change_review).length,
			unreviewed_or_insufficient_items: items.filter(item => !item.eligible_for_change_review).length,
			selection: ids.length ? "explicit-artifact-ids" : "none", raw_transcripts_read: false, tool_arguments_or_outputs_read: false, chain_of_thought_read: false },
	};
}
