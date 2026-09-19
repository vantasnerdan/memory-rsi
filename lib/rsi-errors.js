// Only owned, allowlisted primitives may cross the assessment error boundary.
const CODES = new Set([
	"TYPESAFE_CONFIG", "TYPESAFE_REQUEST", "TYPESAFE_REQUEST_TOO_LARGE",
	"TYPESAFE_RESPONSE_INVALID", "TYPESAFE_RESPONSE_TOO_LARGE", "TYPESAFE_TIMEOUT",
	"TYPESAFE_CANCELLED", "TYPESAFE_REDIRECT", "TYPESAFE_HTTP", "TYPESAFE_AUTH",
	"TYPESAFE_NETWORK", "RSI_INPUT_BUDGET", "LEARNING_INPUT_BUDGET",
	"TYPESAFE_INVALID_DISTRIBUTION_SUM", "TYPESAFE_INVALID_DISTRIBUTION_SHAPE",
	"TYPESAFE_INVALID_SCORE_WEIGHT", "TYPESAFE_INVALID_SCORE_LEGEND", "TYPESAFE_INVALID_SCORE_RANGE",
	"TYPESAFE_INVALID_CHOICE", "TYPESAFE_INVALID_CONFIDENCE", "TYPESAFE_INVALID_NOUL",
	"TYPESAFE_INVALID_RESPONSE_SHAPE", "TYPESAFE_INVALID_RESPONSE_TYPE",
]);

export function safeAssessmentCode(value, key = "code") {
	try {
		if (value === null || !["object", "function"].includes(typeof value)) return undefined;
		const descriptor = Object.getOwnPropertyDescriptor(value, key);
		const code = descriptor && Object.hasOwn(descriptor, "value") ? descriptor.value : undefined;
		return typeof code === "string" && CODES.has(code) ? code : undefined;
	} catch {
		// Proxies and malformed dependencies must not leak thrown messages either.
		return undefined;
	}
}
