const MAX_INLINE_CHARS = 90000;
const pick = (value, keys) => Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key, value[key]]));
const reason = "Saved contract details exceed the inline budget. Open the exact plan path or preflight receipt for omitted content; omission is not evidence of completeness.";

/** Preserve the durable save and its coaching together; never slice structured JSON. */
export function contractCreateOutput(saved) {
	const full = JSON.stringify(saved);
	if (full.length <= MAX_INLINE_CHARS) return full;
	const result = {
		...pick(saved, ["path", "revision", "persistence", "validation", "policy_preflight"]),
		plan: { plan_id: saved.plan.plan_id, ...(saved.plan.template ? { template: pick(saved.plan.template, ["template_id", "revision"]) } : {}) },
		details_omitted: true, reason,
		omitted_fields: ["plan.task", "plan.work_items", "plan.template.content", "markdown"],
	};
	if (JSON.stringify(result).length <= MAX_INLINE_CHARS) return JSON.stringify(result);
	if (result.validation?.work_items) {
		result.validation = { ...pick(result.validation, ["valid", "complete", "direction"]), work_item_count: result.validation.work_items.length, details_omitted: true };
		result.omitted_fields.push("validation.work_items");
	}
	const coaching = saved.policy_preflight;
	if (coaching) {
		result.policy_preflight = {
			...pick(coaching, ["status", "reason", "error_code", "validation_code", "disclaimer", "permission_effect", "receipt", "coverage", "evaluator"]),
			...(coaching.response ? { response: pick(coaching.response, ["model", "usage", "elapsedMs"]) } : {}),
			...(coaching.interpretation ? { interpretation: {
				...pick(coaching.interpretation, ["disposition", "reason_codes", "comparison_coverage", "confidence_interpretation", "permission_effect", "achievement_credit", "automatic_promotion", "disclaimer"]),
				opportunity_count: coaching.interpretation.opportunities?.length ?? 0,
				opportunity_ids: coaching.interpretation.opportunities?.map(item => item.id) ?? [],
				details_omitted: true,
			} } : {}),
			details_omitted: true,
		};
		result.omitted_fields.push("policy_preflight.response.answers", "policy_preflight.interpretation.opportunity_details");
	}
	// A real preflight's remaining fields are bounded identity/status/coverage data.
	// Keep these intact even if a future backend expands them: no invalid JSON and
	// no silent loss of an adverse status, immutable receipt, or coverage limitation.
	return JSON.stringify(result);
}
