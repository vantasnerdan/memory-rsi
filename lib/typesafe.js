import { performance } from "node:perf_hooks";

// Dedicated System One v1 transport, not an OpenAI/chat adapter.
// Protocol: https://docs.typesafe.ai/api.md and /primitives/{choice,score,advanced}.md.
const DEFAULTS = Object.freeze({
	typesafeEndpoint: "https://api.typesafe.ai/v1/systemone",
	typesafeModel: "jev-latest",
	typesafeApiKeyEnv: "TYPESAFE_API_KEY",
	typesafeTimeoutMs: 20000,
	typesafeMaxRequestBytes: 131072,
	typesafeMaxResponseBytes: 262144,
	typesafeRetries: 1,
});
const RETRY_STATUSES = new Set([429, 529, 502, 503, 504]);
const MAX_DEPTH = 64;
const PROBABILITY_TOLERANCE = 0.001;
const MAX_BACKOFF_MS = 2000;

class TypesafeError extends Error {
	constructor(code, message, status) {
		super(message);
		this.name = "TypesafeError";
		this.code = code;
		if (status !== undefined) this.status = status;
	}
}
const failure = (code, message, status) => new TypesafeError(code, message, status);
const invalidRequest = () => failure("TYPESAFE_REQUEST", "Typesafe requires a valid JSON state and typed questions");
const INVALID_RESPONSE_REASONS = new Set([
	"RESPONSE_SHAPE", "RESPONSE_TYPE", "DISTRIBUTION_SHAPE", "DISTRIBUTION_SUM",
	"SCORE_RANGE", "SCORE_WEIGHT", "SCORE_LEGEND", "CHOICE", "CONFIDENCE", "NOUL",
]);
function invalidResponse(reason = "RESPONSE_SHAPE") {
	const error = failure("TYPESAFE_RESPONSE_INVALID", "Typesafe returned an invalid typed response");
	// Only our static enum leaves the validator, never provider keys, values or messages.
	error.validation_code = INVALID_RESPONSE_REASONS.has(reason) ? `TYPESAFE_INVALID_${reason}` : "TYPESAFE_INVALID_RESPONSE_SHAPE";
	return error;
}
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const entry = value => value === null || typeof value === "string" || typeof value === "object";
const probability = value => Number.isFinite(value) && value >= 0 && value <= 1;
const exactKeys = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));

/** Operator configuration only; credentials are never read or stored here. */
export function typesafeConfig(raw = {}) {
	const bad = () => failure("TYPESAFE_CONFIG", "Invalid Typesafe transport configuration");
	let config;
	try {
		if (!record(raw) || ![Object.prototype, null].includes(Object.getPrototypeOf(raw))) throw bad();
		config = Object.fromEntries(Object.entries(DEFAULTS).map(([key, fallback]) => {
			const property = Object.getOwnPropertyDescriptor(raw, key);
			if (property && !Object.hasOwn(property, "value")) throw bad();
			return [key, property?.value === undefined ? fallback : property.value];
		}));
	} catch { throw bad(); }
	const endpoint = config.typesafeEndpoint;
	if (typeof endpoint !== "string") throw bad();
	let url;
	try { url = new URL(endpoint); } catch { throw bad(); }
	// Reject even empty query/hash/userinfo and URL parser normalization of paths.
	if (!/^https:\/\/[^/?#\\\s@]+\/v1\/systemone$/.test(endpoint)
		|| url.protocol !== "https:" || url.username || url.password || url.search || url.hash
		|| url.pathname !== "/v1/systemone") throw bad();
	config.typesafeEndpoint = url.href;
	if (typeof config.typesafeModel !== "string" || !/^[\x21-\x7e]{1,256}$/.test(config.typesafeModel)) throw bad();
	if (typeof config.typesafeApiKeyEnv !== "string" || !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(config.typesafeApiKeyEnv)) throw bad();
	// Hard ceilings keep misconfiguration from removing the transport's bounds.
	for (const [key, min, max] of [
		["typesafeTimeoutMs", 1, 120000],
		["typesafeMaxRequestBytes", 1, 1048576],
		["typesafeMaxResponseBytes", 1, 2097152],
		["typesafeRetries", 0, 3],
	]) {
		if (!Number.isSafeInteger(config[key]) || config[key] < min || config[key] > max) throw bad();
	}
	return Object.freeze(config);
}

/** Serialize only lossless plain JSON, with bounded allocation and no toJSON/getters. */
function requestJSON(value, maxBytes) {
	const parts = [];
	const ancestors = new Set();
	let size = 0;
	const tooLarge = () => failure("TYPESAFE_REQUEST_TOO_LARGE", "Typesafe request exceeds its byte limit");
	const append = text => {
		size += Buffer.byteLength(text);
		if (size > maxBytes) throw tooLarge();
		parts.push(text);
	};
	const string = text => {
		if (Buffer.byteLength(text) > maxBytes - size) throw tooLarge();
		append(JSON.stringify(text));
	};
	const visit = (item, depth) => {
		if (depth > MAX_DEPTH) throw invalidRequest();
		if (item === null) { append("null"); return; }
		if (typeof item === "string") { string(item); return; }
		if (typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item))) { append(JSON.stringify(item)); return; }
		if (typeof item !== "object" || ancestors.has(item)) throw invalidRequest();
		const array = Array.isArray(item);
		if (!array && Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null) throw invalidRequest();
		if (Object.getOwnPropertySymbols(item).length) throw invalidRequest();
		const keys = Object.keys(item);
		if (array && keys.length !== item.length) throw invalidRequest();
		ancestors.add(item);
		append(array ? "[" : "{");
		for (let index = 0; index < keys.length; index++) {
			const key = keys[index];
			if (array && key !== String(index)) throw invalidRequest();
			const descriptor = Object.getOwnPropertyDescriptor(item, key);
			if (!descriptor || !Object.hasOwn(descriptor, "value")) throw invalidRequest();
			if (index) append(",");
			if (!array) { string(key); append(":"); }
			visit(descriptor.value, depth + 1);
		}
		append(array ? "]" : "}");
		ancestors.delete(item);
	};
	visit(value, 0);
	return parts.join("");
}

function validateQuestions(questions) {
	if (!record(questions) || !Object.keys(questions).length) throw invalidRequest();
	for (const [id, question] of Object.entries(questions)) {
		if (!id || !record(question) || !Object.hasOwn(question, "instructions") || !entry(question.instructions)
			|| Object.keys(question).some(key => !["type", "instructions", "criteria"].includes(key))) throw invalidRequest();
		const criteria = question.criteria;
		switch (question.type) {
			case "noul":
				if (criteria !== undefined && (!record(criteria) || Object.entries(criteria).some(([key, value]) => !["true", "false"].includes(key) || !entry(value)))) throw invalidRequest();
				break;
			case "choice":
				if (!record(criteria) || !Object.keys(criteria).length || Object.keys(criteria).length > 255
					|| Object.entries(criteria).some(([key, value]) => !key || !entry(value))) throw invalidRequest();
				break;
			case "score":
				if (!Array.isArray(criteria) || criteria.length < 2 || criteria.length > 10 || !criteria.every(entry)) throw invalidRequest();
				break;
			default: throw invalidRequest();
		}
	}
}

function prepareRequest(request, config) {
	try {
		if (!exactKeys(request, ["state", "questions"])) throw invalidRequest();
		// Snapshot without invoking even top-level accessors, then add the configured model.
		const snapshot = JSON.parse(requestJSON(request, config.typesafeMaxRequestBytes));
		if (snapshot.state === null || !entry(snapshot.state)) throw invalidRequest();
		validateQuestions(snapshot.questions);
		const body = requestJSON({ ...snapshot, model: config.typesafeModel }, config.typesafeMaxRequestBytes);
		return { body, questions: snapshot.questions };
	} catch (error) {
		if (error instanceof TypesafeError) throw error;
		throw invalidRequest();
	}
}

/** Reject duplicate keys (including escaped aliases) and excessive JSON depth. */
function parseResponse(text) {
	try {
		const stack = [];
		for (let index = 0; index < text.length; index++) {
			const token = text[index];
			if (token === '"') {
				const start = index++;
				while (index < text.length && text[index] !== '"') {
					if (text[index] === "\\") index++;
					index++;
				}
				const frame = stack.at(-1);
				if (frame?.expectKey) {
					const key = JSON.parse(text.slice(start, index + 1));
					if (frame.keys.has(key)) throw invalidResponse();
					frame.keys.add(key);
					frame.expectKey = false;
				}
			} else if (token === "{" || token === "[") {
				stack.push(token === "{" ? { keys: new Set(), expectKey: true } : {});
				if (stack.length > MAX_DEPTH) throw invalidResponse();
			} else if (token === "}" || token === "]") stack.pop();
			else if (token === "," && stack.at(-1)?.keys) stack.at(-1).expectKey = true;
		}
		return JSON.parse(text, (_key, value) => {
			if (typeof value === "number" && !Number.isFinite(value)) throw invalidResponse();
			return value;
		});
	} catch { throw invalidResponse(); }
}

function sameJSON(left, right) {
	if (left === right) return true;
	if (left === null || right === null || typeof left !== "object" || typeof right !== "object"
		|| Array.isArray(left) !== Array.isArray(right)) return false;
	const keys = Object.keys(left);
	return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && sameJSON(left[key], right[key]));
}

function validateDistribution(value, keys) {
	if (!exactKeys(value, keys) || !keys.every(key => probability(value[key]))) throw invalidResponse("DISTRIBUTION_SHAPE");
	if (Math.abs(keys.reduce((sum, key) => sum + value[key], 0) - 1) > PROBABILITY_TOLERANCE) throw invalidResponse("DISTRIBUTION_SUM");
}

function validateResponse(response, questions, apiKey) {
	if (!record(response) || typeof response.model !== "string" || !response.model.trim() || response.model.length > 256 || /[\s\x00-\x1f\x7f]/.test(response.model) || response.model.includes(apiKey)
		|| !exactKeys(response.answers, Object.keys(questions)) || !record(response.usage)
		|| ![response.usage.input_tokens, response.usage.output_tokens].every(value => Number.isSafeInteger(value) && value >= 0)) throw invalidResponse();
	const answers = [];
	for (const [id, question] of Object.entries(questions)) {
		const answer = response.answers[id];
		if (!record(answer)) throw invalidResponse("RESPONSE_SHAPE");
		if (answer.type !== question.type) throw invalidResponse("RESPONSE_TYPE");
		if (question.type === "noul") {
			if (!probability(answer.noul)) throw invalidResponse("NOUL");
			answers.push([id, { type: "noul", noul: answer.noul }]);
			continue;
		}
		if (!probability(answer.confidence)) throw invalidResponse("CONFIDENCE");
		if (question.type === "choice") {
			const keys = Object.keys(question.criteria);
			validateDistribution(answer.probabilities, keys);
			if (typeof answer.choice !== "string" || !Object.hasOwn(question.criteria, answer.choice)
				|| keys.some(key => answer.probabilities[key] > answer.probabilities[answer.choice] + 1e-6)) throw invalidResponse("CHOICE");
			answers.push([id, { type: "choice", choice: answer.choice, confidence: answer.confidence,
				probabilities: Object.fromEntries(keys.map(key => [key, answer.probabilities[key]])) }]);
		} else {
			const keys = question.criteria.map((_, index) => String(index));
			validateDistribution(answer.probabilities, keys);
			if (!exactKeys(answer.legend, keys) || !keys.every(key => sameJSON(answer.legend[key], question.criteria[Number(key)]))) throw invalidResponse("SCORE_LEGEND");
			if (!Number.isFinite(answer.score) || answer.score < 0 || answer.score > keys.length - 1) throw invalidResponse("SCORE_RANGE");
			const weighted = keys.reduce((sum, key) => sum + Number(key) * answer.probabilities[key], 0);
			// Allow two-decimal score rounding plus the distribution's tolerance; never normalize raw values.
			if (Math.abs(answer.score - weighted) > 0.01 + (keys.length - 1) * PROBABILITY_TOLERANCE) throw invalidResponse("SCORE_WEIGHT");
			answers.push([id, { type: "score", score: answer.score, confidence: answer.confidence,
				probabilities: Object.fromEntries(keys.map(key => [key, answer.probabilities[key]])),
				legend: Object.fromEntries(keys.map(key => [key, answer.legend[key]])) }]);
		}
	}
	// Drop vendor extensions/free text, including any untrusted credential echoes.
	// Preserve every documented typed value without rounding or recalibrating it.
	return { model: response.model, answers: Object.fromEntries(answers),
		usage: { input_tokens: response.usage.input_tokens, output_tokens: response.usage.output_tokens } };
}

/** One monotonic deadline covers fetch, every read, and all retry delays. */
function operation(signal, timeoutMs, started) {
	if (signal !== undefined && !(signal instanceof AbortSignal)) throw failure("TYPESAFE_REQUEST", "Invalid Typesafe cancellation signal");
	const controller = new AbortController();
	const deadline = started + timeoutMs;
	const abort = code => {
		if (!controller.signal.aborted) controller.abort(failure(code, code === "TYPESAFE_TIMEOUT" ? "Typesafe request timed out" : "Typesafe request cancelled"));
	};
	const onAbort = () => abort("TYPESAFE_CANCELLED");
	signal?.addEventListener("abort", onAbort, { once: true });
	if (signal?.aborted) onAbort();
	const timer = setTimeout(() => abort("TYPESAFE_TIMEOUT"), Math.max(0, deadline - performance.now()));
	const check = () => {
		if (performance.now() >= deadline) abort("TYPESAFE_TIMEOUT");
		if (controller.signal.aborted) throw controller.signal.reason;
	};
	return {
		signal: controller.signal,
		check,
		wait(promise) {
			return new Promise((resolve, reject) => {
				const cleanup = () => controller.signal.removeEventListener("abort", cancelled);
				const cancelled = () => { cleanup(); reject(controller.signal.reason); };
				// Always observe the underlying promise, even if it ignores cancellation.
				Promise.resolve(promise).then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
				controller.signal.addEventListener("abort", cancelled, { once: true });
				try { check(); } catch (error) { cleanup(); reject(error); }
			});
		},
		close(failed) {
			clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);
			if (failed && !controller.signal.aborted) abort("TYPESAFE_CANCELLED");
		},
	};
}

// Body cancellation must not itself delay timeout/error delivery or leak a rejection.
function cancelBody(response) {
	try { Promise.resolve(response?.body?.cancel()).catch(() => {}); } catch { /* already locked/closed */ }
}

async function readResponse(response, maxBytes, op) {
	const length = response.headers?.get("content-length");
	if (length && /^\d+$/.test(length) && Number(length) > maxBytes) {
		cancelBody(response);
		throw failure("TYPESAFE_RESPONSE_TOO_LARGE", "Typesafe response exceeds its byte limit");
	}
	if (!response.body || typeof response.body.getReader !== "function") throw invalidResponse();
	const reader = response.body.getReader();
	const chunks = [];
	let size = 0;
	let complete = false;
	try {
		while (true) {
			op.check();
			const { done, value } = await op.wait(reader.read());
			op.check();
			if (done) { complete = true; break; }
			if (!(value instanceof Uint8Array)) throw invalidResponse();
			size += value.byteLength;
			if (size > maxBytes) throw failure("TYPESAFE_RESPONSE_TOO_LARGE", "Typesafe response exceeds its byte limit");
			// Copy only the view, not an arbitrarily large underlying ArrayBuffer.
			if (value.byteLength) chunks.push(Buffer.from(value));
		}
		try { return new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, size)); }
		catch { throw invalidResponse(); }
	} finally {
		if (!complete) {
			try { Promise.resolve(reader.cancel()).catch(() => {}); } catch { /* best effort */ }
		}
		try { reader.releaseLock(); } catch { /* pending cancelled read */ }
	}
}

function retryDelay(response, attempt) {
	const exponential = Math.min(MAX_BACKOFF_MS, 250 * 2 ** attempt);
	const header = response.headers?.get("retry-after");
	if (!header) return exponential;
	const seconds = /^\d+(?:\.\d+)?$/.test(header) ? Number(header) : NaN;
	const milliseconds = Number.isNaN(seconds) ? Date.parse(header) - Date.now() : seconds * 1000;
	return Number.isNaN(milliseconds) ? exponential : Math.min(MAX_BACKOFF_MS, Math.max(exponential, milliseconds));
}

async function backoff(milliseconds, op) {
	let timer;
	try { await op.wait(new Promise(resolve => { timer = setTimeout(resolve, milliseconds); })); }
	finally { clearTimeout(timer); }
	op.check();
}

async function evaluateRequest(body, questions, config, apiKey, fetchImpl, op) {
	for (let attempt = 0; attempt <= config.typesafeRetries; attempt++) {
		op.check();
		// A late response from a non-cooperative injected fetch is also disposed.
		const pending = Promise.resolve().then(() => {
			op.check();
			return fetchImpl(config.typesafeEndpoint, {
				method: "POST", redirect: "manual", signal: op.signal,
				headers: { "Content-Type": "application/json", "Accept": "application/json", "Authorization": `Bearer ${apiKey}` },
				body,
			});
		}).then(response => {
			if (op.signal.aborted) { cancelBody(response); op.check(); }
			return response;
		});
		const response = await op.wait(pending);
		try {
			op.check();
			if (!Number.isInteger(response?.status) || response.status < 100 || response.status > 599) throw invalidResponse();
			if (response.redirected || (response.status >= 300 && response.status < 400)) {
				throw failure("TYPESAFE_REDIRECT", "Typesafe redirects are not allowed");
			}
			if (response.status < 200 || response.status >= 300) {
				cancelBody(response); // Never read or expose error bodies, even on retry.
				if (RETRY_STATUSES.has(response.status) && attempt < config.typesafeRetries) {
					await backoff(retryDelay(response, attempt), op);
					continue;
				}
				throw failure("TYPESAFE_HTTP", `Typesafe request failed (HTTP ${response.status})`, response.status);
			}
			const payload = parseResponse(await readResponse(response, config.typesafeMaxResponseBytes, op));
			const result = validateResponse(payload, questions, apiKey);
			op.check();
			return result;
		} catch (error) {
			cancelBody(response);
			throw error;
		}
	}
}

/**
 * Evaluate explicit application state with typed Noul/Choice/Score questions.
 * No file access, credential persistence, model-generated text, or policy decisions.
 * Returns { model, answers, usage, elapsedMs }; probabilities and usage stay raw.
 * Safe errors expose code/message, numeric HTTP status, or an owned static validation_code.
 * Validation diagnostics never include question IDs, probability values or provider text.
 */
export async function evaluateTypesafe(request, rawConfig = {}, { signal, apiKey, fetchImpl = globalThis.fetch } = {}) {
	const started = performance.now();
	const config = typesafeConfig(rawConfig);
	const op = operation(signal, config.typesafeTimeoutMs, started);
	let failed = true;
	try {
		op.check();
		const { body, questions } = prepareRequest(request, config);
		const key = apiKey === undefined ? process.env[config.typesafeApiKeyEnv] : apiKey;
		if (typeof key !== "string" || !/^[\x21-\x7e]{1,4096}$/.test(key)) {
			throw failure("TYPESAFE_AUTH", "Typesafe API credential is missing or invalid");
		}
		if (typeof fetchImpl !== "function") throw failure("TYPESAFE_NETWORK", "Typesafe fetch transport is unavailable");
		const result = await evaluateRequest(body, questions, config, key, fetchImpl, op);
		failed = false;
		return { model: result.model, answers: result.answers, usage: result.usage, elapsedMs: Math.max(0, performance.now() - started) };
	} catch (error) {
		// Never forward fetch messages, cause, abort reasons, URLs, bodies, or credentials.
		if (op.signal.aborted) throw op.signal.reason;
		if (error instanceof TypesafeError) throw error;
		throw failure("TYPESAFE_NETWORK", "Typesafe request transport failed");
	} finally { op.close(failed); }
}
