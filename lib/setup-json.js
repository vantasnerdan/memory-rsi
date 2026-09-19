/** Native syntax validation plus duplicate-key rejection before setup side effects. */
export function setupFields(text = "{}") {
	if (typeof text !== "string" || Buffer.byteLength(text) > 2 * 1024 * 1024) throw new Error("Setup request must be JSON text at most 2 MiB; narrow large migration selections.");
	const value = JSON.parse(text);
	if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Setup request must be a JSON object");
	const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|[^{}\[\]:,\s]+/g) || [];
	let offset = 0;
	function visit(depth) {
		if (depth > 100) throw new Error("Setup request nesting exceeds 100 levels");
		const token = tokens[offset++];
		if (token === "{") {
			const keys = new Set();
			while (tokens[offset] !== "}") {
				const key = JSON.parse(tokens[offset++]);
				if (keys.has(key)) throw new Error(`Duplicate JSON field: ${key}`);
				keys.add(key); offset++; visit(depth + 1);
				if (tokens[offset] !== ",") break;
				offset++;
			}
			offset++;
		} else if (token === "[") {
			while (tokens[offset] !== "]") {
				visit(depth + 1);
				if (tokens[offset] !== ",") break;
				offset++;
			}
			offset++;
		}
	}
	visit(0);
	if (Object.hasOwn(value, "action")) throw new Error("Setup request must exclude action; supply the action parameter separately");
	return value;
}
