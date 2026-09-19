import { DISCLAIMER } from "./rsi-rubric.js";

const pick = (value, keys) => Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key, value[key]]));
const identity = value => pick(value, ["id", "kind", "revision", "plan_id", "source_id", "unit", "status", "error_code", "validation_code", "cache_hit", "race_reused"]);
function publication(value) {
	if (!value) return undefined;
	return { saved: value.saved, git: pick(value.git, ["status", "error_code", "validation_code", "committed", "pushed", "commit", "commit_sha"]), details_omitted: true };
}
function receipt(value) {
	return value ? { ...identity(value), persistence: publication(value.persistence) } : value;
}
function coverage(value) {
	if (!value) return value;
	const result = { ...value };
	if (value.discovery_errors) result.discovery_errors = value.discovery_errors.map(item => pick(item, ["kind", "id", "code"]));
	if (value.sources) result.sources = value.sources.map(item => pick(item, ["kind", "id", "revision", "units_total", "start_unit", "units_completed", "error", "omitted_context_units", "projection_version", "projection_modes", "source_reviewed_in_full", "projection_exclusions_present"]));
	return result;
}
const reason = "Detail exceeds the inline budget; identities and continuation are preserved. Read a saved artifact by ID (issue_index selects one insight issue) or open its complete file. Omission is not evidence of completeness.";

/** Compact owned business JSON without dropping completed work or pagination. */
export function rsiOutput(result) {
	const text = JSON.stringify(result);
	if (text.length <= 90000) return text;
	const common = { details_omitted: true, reason, disclaimer: DISCLAIMER };
	if (Array.isArray(result.receipts)) return JSON.stringify({ ...common,
		...pick(result, ["status", "error_code", "validation_code", "resume", "next_after", "has_more", "calls", "cache_hits", "usage", "warnings"]),
		receipts: result.receipts.map(receipt), coverage: coverage(result.coverage) });
	if (result.receipt && Array.isArray(result.issues)) return JSON.stringify({ ...common,
		...pick(result, ["status", "error_code", "validation_code", "calls", "usage", "warnings", "author_brief"]), receipt: receipt(result.receipt), coverage: coverage(result.coverage),
		issues: result.issues.map((issue, issue_index) => ({ issue_index, ...pick(issue, ["key", "status", "error_code", "validation_code", "mechanism", "destination", "operation", "target_section", "generality", "sufficiency", "independent_source_count", "coverage", "uncertainties"]) })) });
	if (result.artifact) return JSON.stringify({ ...common, ...pick(result, ["ok", "action", "persistence"]), artifact: { ...identity(result.artifact), path: result.artifact.path, bindings: result.artifact.bindings, freshness: result.artifact.freshness } });
	if (Array.isArray(result.artifacts) || Array.isArray(result.sources)) {
		const key = Array.isArray(result.artifacts) ? "artifacts" : "sources";
		return JSON.stringify({ ...common, ...pick(result, ["ok", "action", "kind", "next_after", "has_more", "scanned", "scanned_bytes", "invalid_file_names", "order", "review_count_guidance"]),
			[key]: result[key].map(value => ({ ...identity(value), ...pick(value, ["title", "complete", "review_counts"]) })),
			errors: result.errors?.map(item => pick(item, ["kind", "id", "code"])) });
	}
	if (result.brief) return JSON.stringify({ ...common, policy: result.policy,
		cases: result.cases.map(entry => pick(entry, ["plan_id", "revision"])),
		sections: result.sections?.map(entry => pick(entry, ["section_id", "title", "revision", "start_line", "end_line"])),
		issues: result.issues?.map(identity), brief: result.brief, next: result.next, network_called: false });
	return JSON.stringify({ ...common, ...pick(result, ["status", "error_code", "validation_code", "warnings", "next_after", "has_more", "resume", "calls", "usage"]), receipt: receipt(result.receipt), coverage: coverage(result.coverage) });
}
