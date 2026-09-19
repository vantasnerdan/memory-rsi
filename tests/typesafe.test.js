import test from "node:test";
import assert from "node:assert/strict";
import { getEventListeners } from "node:events";
import { typesafeConfig, evaluateTypesafe } from "../lib/typesafe.js";

const API_KEY = "test-credential-never-log";
const basic = () => ({ state: "A reviewed test result", questions: { supported: { type: "noul", instructions: "Is the claim supported?" } } });
const responseBody = () => ({ model: "jev-1.13", answers: { supported: { type: "noul", noul: 0.91 } }, usage: { input_tokens: 24, output_tokens: 7 } });
const jsonResponse = (body = responseBody(), options) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" }, ...options });
const options = fetchImpl => ({ apiKey: API_KEY, fetchImpl });
const validationCodes = new Set([
	"RESPONSE_SHAPE", "RESPONSE_TYPE", "DISTRIBUTION_SHAPE", "DISTRIBUTION_SUM",
	"SCORE_RANGE", "SCORE_WEIGHT", "SCORE_LEGEND", "CHOICE", "CONFIDENCE", "NOUL",
].map(reason => `TYPESAFE_INVALID_${reason}`));
const code = expected => error => {
	assert.equal(error.name, "TypesafeError");
	assert.equal(error.code, expected);
	assert.doesNotMatch(String(error) + JSON.stringify(error), /test-credential-never-log|SECRET_BODY|SECRET_REASON/);
	assert.equal(error.cause, undefined);
	if (expected === "TYPESAFE_RESPONSE_INVALID") {
		assert.ok(Object.hasOwn(error, "validation_code"));
		assert.ok(validationCodes.has(error.validation_code));
	} else assert.equal(Object.hasOwn(error, "validation_code"), false);
	return true;
};

function mixedRequest() {
	return {
		state: { source: "Evidence", reviewed: true, items: [1, null, "€"] },
		questions: {
			supported: { type: "noul", instructions: "Is the claim supported?", criteria: { true: "Evidence supports it", false: "Not supported" } },
			route: { type: "choice", instructions: "Choose one", criteria: { accept: null, review: { condition: "Uncertain" }, reject: ["Unsafe", "Unsupported"] } },
			quality: { type: "score", instructions: { question: "How complete?" }, criteria: ["Missing", { summary: "Partial", examples: ["Tests only"] }, ["Complete"]] },
		},
	};
}
function mixedResponse(request = mixedRequest()) {
	return {
		model: "jev-1.13",
		answers: {
			supported: { type: "noul", noul: 0.5 },
			route: { type: "choice", choice: "review", probabilities: { accept: 0.2, review: 0.6, reject: 0.2 }, confidence: 0.37 },
			quality: { type: "score", score: 1.25, probabilities: { 0: 0.125, 1: 0.5, 2: 0.375 }, legend: Object.fromEntries(request.questions.quality.criteria.map((level, index) => [String(index), level])), confidence: 0.29 },
		},
		usage: { input_tokens: 87, output_tokens: 31 },
	};
}

test("configuration uses dedicated System One defaults without resolving credentials", () => {
	assert.deepEqual(typesafeConfig({ apiKey: API_KEY, ignored: true }), {
		typesafeEndpoint: "https://api.typesafe.ai/v1/systemone", typesafeModel: "jev-latest", typesafeApiKeyEnv: "TYPESAFE_API_KEY",
		typesafeTimeoutMs: 20000, typesafeMaxRequestBytes: 131072, typesafeMaxResponseBytes: 262144, typesafeRetries: 1,
	});
	const configured = typesafeConfig({ typesafeEndpoint: "https://trusted.example:9443/v1/systemone", typesafeModel: "jev-pinned", typesafeRetries: 0 });
	assert.equal(configured.typesafeEndpoint, "https://trusted.example:9443/v1/systemone");
	assert.equal(configured.typesafeModel, "jev-pinned");
	assert.ok(Object.isFrozen(configured));
	assert.ok(!JSON.stringify(configured).includes(API_KEY));
});

test("endpoint validation rejects credentials, non-HTTPS, query, fragment and accidental chat paths", () => {
	for (const endpoint of [
		"http://api.typesafe.ai/v1/systemone", "file:///v1/systemone", "/v1/systemone", "not a URL",
		"https://test-credential-never-log@api.typesafe.ai/v1/systemone", "https://@api.typesafe.ai/v1/systemone",
		"https://user:test-credential-never-log@api.typesafe.ai/v1/systemone", "https://api.typesafe.ai/v1/systemone?key=test-credential-never-log",
		"https://api.typesafe.ai/v1/systemone?", "https://api.typesafe.ai/v1/systemone#", "https://api.typesafe.ai/v1/systemone#secret",
		"https://api.typesafe.ai/v1/chat/completions", "https://api.typesafe.ai/chat/completions", "https://api.typesafe.ai/v1/systemone/",
		"https://api.typesafe.ai/v1/../v1/systemone", "https://api.typesafe.ai/v1/%73ystemone", "https://api.typesafe.ai\\v1\\systemone",
		" https://api.typesafe.ai/v1/systemone", "https://api.typesafe.ai/\nv1/systemone", null, {},
	]) assert.throws(() => typesafeConfig({ typesafeEndpoint: endpoint }), code("TYPESAFE_CONFIG"));
});

test("invalid configuration cannot disable finite resource and retry ceilings", () => {
	for (const [key, values] of Object.entries({
		typesafeTimeoutMs: [0, -1, 1.5, "20000", NaN, Infinity, 120001, null],
		typesafeMaxRequestBytes: [0, 1048577, Infinity], typesafeMaxResponseBytes: [0, 2097153],
		typesafeRetries: [-1, 0.5, 4, Infinity, "1"], typesafeApiKeyEnv: ["", "../secret", "SECRET_REASON\n"],
		typesafeModel: ["", "SECRET_REASON\n", "a".repeat(257), 1],
	})) for (const value of values) assert.throws(() => typesafeConfig({ [key]: value }), code("TYPESAFE_CONFIG"));
});

test("configuration rejects accessors without invoking them and sanitizes reflective failures", () => {
	let invoked = false;
	const config = Object.defineProperty({}, "typesafeEndpoint", { get() { invoked = true; throw new Error(API_KEY); } });
	assert.throws(() => typesafeConfig(config), code("TYPESAFE_CONFIG"));
	assert.equal(invoked, false);
	assert.throws(() => typesafeConfig(new Proxy({}, { getOwnPropertyDescriptor() { throw new Error(`SECRET_REASON ${API_KEY}`); } })), code("TYPESAFE_CONFIG"));
	for (const invalid of [null, [], new Date()]) assert.throws(() => typesafeConfig(invalid), code("TYPESAFE_CONFIG"));
});

test("unexpected request reflection errors cannot expose input or credentials", async () => {
	const request = new Proxy(basic(), { ownKeys() { throw new Error(`SECRET_REASON ${API_KEY}`); } });
	await assert.rejects(evaluateTypesafe(request, {}, options(() => assert.fail("must not fetch"))), code("TYPESAFE_REQUEST"));
});

test("typed POST uses only configured endpoint, model, state and questions; returns full raw judgments", async () => {
	const request = mixedRequest();
	const original = mixedResponse(request);
	let calls = 0;
	const result = await evaluateTypesafe(request, { typesafeModel: "jev-pinned" }, options(async (url, init) => {
		calls++;
		assert.equal(url, "https://api.typesafe.ai/v1/systemone");
		assert.equal(init.method, "POST");
		assert.equal(init.redirect, "manual");
		assert.equal(init.headers.Authorization, `Bearer ${API_KEY}`);
		assert.equal(init.headers["Content-Type"], "application/json");
		assert.equal(init.headers.Accept, "application/json");
		assert.ok(init.signal instanceof AbortSignal);
		const body = JSON.parse(init.body);
		assert.deepEqual(body, { ...request, model: "jev-pinned" });
		assert.ok(!init.body.includes(API_KEY));
		assert.ok(!Object.hasOwn(body, "messages"));
		return jsonResponse(original);
	}));
	assert.equal(calls, 1);
	assert.deepEqual({ model: result.model, answers: result.answers, usage: result.usage }, original);
	assert.ok(Number.isFinite(result.elapsedMs) && result.elapsedMs >= 0);
	assert.equal(result.answers.supported.noul, 0.5); // Uncertainty is not silently turned into a boolean.
});

test("only documented typed response fields survive vendor extensions and credential echoes", async () => {
	const expected = mixedResponse();
	const value = structuredClone(expected);
	value.message = `SECRET_BODY ${API_KEY}`;
	value.api_key = API_KEY;
	value.debug = { authorization: `Bearer ${API_KEY}` };
	for (const answer of Object.values(value.answers)) {
		answer.explanation = `SECRET_BODY ${API_KEY}`;
		answer.authorization = API_KEY;
		answer.debug = { nested: [API_KEY] };
	}
	value.answers.supported.confidence = API_KEY; // Noul has no confidence field.
	value.usage.cached_tokens = 12;
	value.usage.message = `SECRET_BODY ${API_KEY}`;
	const result = await evaluateTypesafe(mixedRequest(), {}, options(async () => jsonResponse(value)));
	assert.deepEqual({ model: result.model, answers: result.answers, usage: result.usage }, expected);
	assert.doesNotMatch(JSON.stringify(result), /test-credential-never-log|SECRET_BODY|explanation|authorization|cached_tokens/);
});

test("returned model identifiers are bounded and cannot expose the actual API credential", async () => {
	for (const model of [API_KEY, `jev-${API_KEY}`, "x".repeat(257), "jev SECRET_BODY", "jev\nSECRET_BODY"]) {
		const value = responseBody(); value.model = model;
		await assert.rejects(evaluateTypesafe(basic(), {}, options(async () => jsonResponse(value))), code("TYPESAFE_RESPONSE_INVALID"));
	}
	const maximum = responseBody(); maximum.model = "j".repeat(256);
	const result = await evaluateTypesafe(basic(), {}, options(async () => jsonResponse(maximum)));
	assert.equal(result.model, maximum.model);
});

test("request/validation snapshot cannot change while fetch is pending", async () => {
	const request = mixedRequest();
	const original = mixedResponse(request);
	const result = await evaluateTypesafe(request, {}, options(async () => {
		request.questions.quality.criteria[1] = "Changed after request";
		delete request.questions.route.criteria.accept;
		request.questions.supported.type = "choice";
		return jsonResponse(original);
	}));
	assert.deepEqual(result.answers, original.answers);
});

test("structured/null descriptions and special own-property IDs are supported safely", async () => {
	const questions = JSON.parse('{"__proto__":{"type":"choice","instructions":null,"criteria":{"__proto__":null,"constructor":null}},"constructor":{"type":"score","instructions":[],"criteria":[null,{"a":1,"b":2}]}}');
	const answers = JSON.parse('{"__proto__":{"type":"choice","choice":"__proto__","confidence":1,"probabilities":{"__proto__":1,"constructor":0}},"constructor":{"type":"score","score":1,"confidence":1,"probabilities":{"0":0,"1":1},"legend":{"0":null,"1":{"b":2,"a":1}}}}');
	const result = await evaluateTypesafe({ state: [], questions }, {}, options(async () => jsonResponse({ model: "jev-latest", answers, usage: { input_tokens: 0, output_tokens: 0 } })));
	assert.deepEqual(result.answers, answers);
	assert.equal({}.polluted, undefined);
});

test("request rejects unknown top-level endpoint/model/messages and malformed questions before fetch", async () => {
	const requests = [null, [], {}, { ...basic(), endpoint: "https://evil.example/v1/systemone" }, { ...basic(), model: "chat" }, { ...basic(), messages: [] },
		{ ...basic(), state: null }, { ...basic(), state: 4 }, { ...basic(), state: true }, { ...basic(), questions: {} },
		{ ...basic(), questions: [] }, { ...basic(), questions: { x: null } }, { ...basic(), questions: { x: { type: "chat", instructions: "x" } } },
	];
	for (const question of [
		{ type: "noul" }, { type: "noul", instructions: 1 }, { type: "noul", instructions: "x", extra: true },
		{ type: "noul", instructions: "x", criteria: [] }, { type: "noul", instructions: "x", criteria: { yes: "yes" } },
		{ type: "choice", instructions: "x", criteria: {} }, { type: "choice", instructions: "x", criteria: ["one", "two"] },
		{ type: "choice", instructions: "x", criteria: { a: 1 } },
		{ type: "choice", instructions: "x", criteria: Object.fromEntries(Array.from({ length: 256 }, (_, i) => [String(i), null])) },
		{ type: "score", instructions: "x", criteria: ["only"] }, { type: "score", instructions: "x", criteria: Array(11).fill(null) },
		{ type: "score", instructions: "x", criteria: ["a", 1] }, { type: "score", instructions: "x", criteria: { 0: "a", 1: "b" } },
	]) requests.push({ state: "test", questions: { x: question } });
	for (const request of requests) await assert.rejects(evaluateTypesafe(request, {}, options(() => assert.fail("must not fetch"))), code("TYPESAFE_REQUEST"));
});

test("lossy/non-JSON/cyclic/deep input is rejected without invoking toJSON or nested getters", async () => {
	const cycle = {}; cycle.self = cycle;
	let deep = "leaf"; for (let i = 0; i < 70; i++) deep = { nested: deep };
	let invocations = 0;
	const getter = Object.defineProperty({}, "secret", { enumerable: true, get() { invocations++; return "getter invoked"; } });
	const states = [cycle, deep, { value: NaN }, { value: Infinity }, { value: undefined }, { value: 1n }, { value: Symbol("secret") },
		{ value() {} }, new Date(), new Map(), Array(2), getter, { toJSON() { invocations++; return "toJSON invoked"; } }, { [Symbol("secret")]: 1 }];
	for (const state of states) await assert.rejects(evaluateTypesafe({ ...basic(), state }, {}, options(() => assert.fail("must not fetch"))), code("TYPESAFE_REQUEST"));
	const request = Object.defineProperty(basic(), "state", { enumerable: true, get() { invocations++; return "getter invoked"; } });
	await assert.rejects(evaluateTypesafe(request, {}, options(() => assert.fail("must not fetch"))), code("TYPESAFE_REQUEST"));
	assert.equal(invocations, 0);
});

test("request byte limits count UTF-8 and JSON escaping, including model and keys", async () => {
	for (const state of ["€".repeat(300), "\u0000".repeat(300), "a".repeat(1000)]) {
		await assert.rejects(evaluateTypesafe({ ...basic(), state }, { typesafeMaxRequestBytes: 500 }, options(() => assert.fail("must not fetch"))), code("TYPESAFE_REQUEST_TOO_LARGE"));
	}
	const request = { ...basic(), state: "€" };
	const bytes = Buffer.byteLength(JSON.stringify({ state: request.state, questions: request.questions, model: "jev-latest" }));
	await evaluateTypesafe(request, { typesafeMaxRequestBytes: bytes }, options(async () => jsonResponse()));
	await assert.rejects(evaluateTypesafe(request, { typesafeMaxRequestBytes: bytes - 1 }, options(() => assert.fail("must not fetch"))), code("TYPESAFE_REQUEST_TOO_LARGE"));
});

test("credentials resolve at call time from named env or explicit option, never from configuration", async t => {
	const name = "MEMORY_RSI_TYPESAFE_TEST_KEY";
	const before = process.env[name];
	t.after(() => { if (before === undefined) delete process.env[name]; else process.env[name] = before; });
	const config = typesafeConfig({ typesafeApiKeyEnv: name, apiKey: "wrong" });
	process.env[name] = "first-env-value";
	const seen = [];
	const fetchImpl = async (_url, init) => { seen.push(init.headers.Authorization); return jsonResponse(); };
	await evaluateTypesafe(basic(), config, { fetchImpl });
	process.env[name] = "second-env-value";
	await evaluateTypesafe(basic(), config, { fetchImpl });
	await evaluateTypesafe(basic(), config, { fetchImpl, apiKey: API_KEY });
	assert.deepEqual(seen, ["Bearer first-env-value", "Bearer second-env-value", `Bearer ${API_KEY}`]);
	delete process.env[name];
	await assert.rejects(evaluateTypesafe(basic(), config, { fetchImpl }), code("TYPESAFE_AUTH"));
	for (const apiKey of ["", null, 1, "SECRET_REASON\n", "has space", "a".repeat(4097)]) {
		await assert.rejects(evaluateTypesafe(basic(), config, { apiKey, fetchImpl: () => assert.fail("must not fetch") }), code("TYPESAFE_AUTH"));
	}
});

test("missing fetch reports a safe unavailable transport error", async () => {
	await assert.rejects(evaluateTypesafe(basic(), {}, { apiKey: API_KEY, fetchImpl: null }), code("TYPESAFE_NETWORK"));
});

const malformedResponses = {
	"top level null": () => null,
	"missing model": value => { delete value.model; },
	"empty model": value => { value.model = "  "; },
	"missing answers": value => { delete value.answers; },
	"answers array": value => { value.answers = []; },
	"missing answer id": value => { delete value.answers.supported; },
	"extra answer id": value => { value.answers.unrequested = { type: "noul", noul: 0.9 }; },
	"answer null": value => { value.answers.supported = null; },
	"type mismatch": value => { value.answers.supported.type = "choice"; },
	"negative noul": value => { value.answers.supported.noul = -0.01; },
	"too large noul": value => { value.answers.supported.noul = 1.01; },
	"string noul": value => { value.answers.supported.noul = "0.5"; },
	"null noul": value => { value.answers.supported.noul = null; },
	"missing noul": value => { delete value.answers.supported.noul; },
	"missing usage": value => { delete value.usage; },
	"missing token count": value => { delete value.usage.output_tokens; },
	"negative tokens": value => { value.usage.input_tokens = -1; },
	"fractional tokens": value => { value.usage.output_tokens = 0.1; },
	"string tokens": value => { value.usage.input_tokens = "1"; },
	"unsafe tokens": value => { value.usage.input_tokens = Number.MAX_SAFE_INTEGER + 1; },
	"unknown choice": value => { value.answers.route.choice = "unknown"; },
	"choice not most probable": value => { value.answers.route.choice = "accept"; },
	"missing choice probability": value => { delete value.answers.route.probabilities.reject; },
	"extra choice probability": value => { value.answers.route.probabilities.other = 0; },
	"probabilities array": value => { value.answers.route.probabilities = [0.2, 0.6, 0.2]; },
	"negative choice probability": value => { value.answers.route.probabilities.accept = -0.2; },
	"too large probability": value => { value.answers.route.probabilities.review = 1.1; },
	"nonnumeric probability": value => { value.answers.route.probabilities.review = "0.6"; },
	"distribution not normalized": value => { value.answers.route.probabilities.review = 0.7; },
	"missing confidence": value => { delete value.answers.route.confidence; },
	"negative confidence": value => { value.answers.quality.confidence = -0.1; },
	"too large confidence": value => { value.answers.route.confidence = 1.1; },
	"null confidence": value => { value.answers.route.confidence = null; },
	"negative score": value => { value.answers.quality.score = -0.1; },
	"above range score": value => { value.answers.quality.score = 2.1; },
	"string score": value => { value.answers.quality.score = "1.25"; },
	"inconsistent weighted score": value => { value.answers.quality.score = 0.7; },
	"missing legend": value => { delete value.answers.quality.legend; },
	"altered legend": value => { value.answers.quality.legend[0] = "Incorrect"; },
	"altered structured legend": value => { value.answers.quality.legend[1].summary = "Incorrect"; },
	"extra legend index": value => { value.answers.quality.legend[3] = "Extra"; },
	"missing legend index": value => { delete value.answers.quality.legend[0]; },
	"noncanonical score index": value => { value.answers.quality.probabilities["00"] = value.answers.quality.probabilities[0]; delete value.answers.quality.probabilities[0]; },
	"missing score probability": value => { delete value.answers.quality.probabilities[0]; },
	"extra score probability": value => { value.answers.quality.probabilities[3] = 0; },
};
for (const [name, mutate] of Object.entries(malformedResponses)) test(`rejects malformed response: ${name}`, async () => {
	const request = mixedRequest();
	const value = mixedResponse(request);
	// Separate request and response data: fixtures intentionally share structured legend entries.
	const clone = structuredClone(value);
	const replacement = mutate(clone);
	let calls = 0;
	await assert.rejects(evaluateTypesafe(request, {}, options(async () => { calls++; return jsonResponse(replacement === undefined ? clone : replacement); })), code("TYPESAFE_RESPONSE_INVALID"));
	assert.equal(calls, 1); // Protocol failures must not retry.
});

const diagnosticCases = {
	RESPONSE_SHAPE: value => { value.model = API_KEY; },
	RESPONSE_TYPE: value => { value.answers.supported.type = `SECRET_BODY ${API_KEY}`; },
	DISTRIBUTION_SHAPE: value => { value.answers.route.probabilities[API_KEY] = 0; },
	DISTRIBUTION_SUM: value => { value.answers.route.probabilities.review = 0.7; },
	SCORE_RANGE: value => { value.answers.quality.score = `SECRET_BODY ${API_KEY}`; },
	SCORE_WEIGHT: value => { value.answers.quality.score = 0.7; },
	SCORE_LEGEND: value => { value.answers.quality.legend[0] = `SECRET_BODY ${API_KEY}`; },
	CHOICE: value => { value.answers.route.choice = API_KEY; },
	CONFIDENCE: value => { value.answers.route.confidence = API_KEY; },
	NOUL: value => { value.answers.supported.noul = API_KEY; },
};
for (const [reason, mutate] of Object.entries(diagnosticCases)) test(`safe static validation diagnostic: ${reason}`, async () => {
	const request = mixedRequest();
	const value = structuredClone(mixedResponse(request));
	value.validation_code = `TYPESAFE_INVALID_${API_KEY}`;
	value.message = `SECRET_BODY ${API_KEY}`;
	value.usage.validation_code = API_KEY;
	for (const answer of Object.values(value.answers)) answer.validation_code = `SECRET_REASON ${API_KEY}`;
	mutate(value);
	let calls = 0;
	await assert.rejects(evaluateTypesafe(request, {}, options(async () => { calls++; return jsonResponse(value); })), error => {
		code("TYPESAFE_RESPONSE_INVALID")(error);
		assert.equal(error.validation_code, `TYPESAFE_INVALID_${reason}`);
		assert.equal(error.message, "Typesafe returned an invalid typed response");
		assert.deepEqual(Object.keys(error).sort(), ["code", "name", "validation_code"]);
		return true;
	});
	assert.equal(calls, 1);
});

test("diagnostics do not relax existing distribution or score precision constraints", async () => {
	for (const probability of [0.59, 0.61]) {
		const value = mixedResponse(); value.answers.route.probabilities.review = probability;
		await assert.rejects(evaluateTypesafe(mixedRequest(), {}, options(async () => jsonResponse(value))), error => {
			code("TYPESAFE_RESPONSE_INVALID")(error);
			assert.equal(error.validation_code, "TYPESAFE_INVALID_DISTRIBUTION_SUM");
			return true;
		});
	}
	const value = mixedResponse(); value.answers.quality.score = 1.263;
	await assert.rejects(evaluateTypesafe(mixedRequest(), {}, options(async () => jsonResponse(value))), error => {
		code("TYPESAFE_RESPONSE_INVALID")(error);
		assert.equal(error.validation_code, "TYPESAFE_INVALID_SCORE_WEIGHT");
		return true;
	});
});

test("provider-thrown diagnostic fields cannot impersonate owned validation errors", async () => {
	await assert.rejects(evaluateTypesafe(basic(), {}, options(async () => {
		throw Object.assign(new Error(`SECRET_BODY ${API_KEY}`), {
			code: "TYPESAFE_RESPONSE_INVALID", validation_code: "TYPESAFE_INVALID_DISTRIBUTION_SUM",
		});
	})), code("TYPESAFE_NETWORK"));
});

test("accepts float rounding, ties, and raw probabilities without renormalization", async () => {
	const request = mixedRequest();
	const value = mixedResponse(request);
	value.answers.route.probabilities = { accept: 0.3333, review: 0.3333, reject: 0.3333 };
	value.answers.quality.score = 1.25 + 0.004;
	const result = await evaluateTypesafe(request, {}, options(async () => jsonResponse(value)));
	assert.deepEqual(result.answers, value.answers);
});

test("rejects malformed JSON, duplicate/escaped keys, nonfinite numbers and invalid UTF-8 privately", async () => {
	const original = JSON.stringify(responseBody());
	for (const body of ["SECRET_BODY not json", "null", "[]", "{", original + " trailing",
		original.replace('"noul":0.91', '"noul":1e999'),
		original.replace('"noul":0.91', '"noul":0.91,"noul":0.1'),
		original.replace('"noul":0.91', '"noul":0.91,"\\u006eoul":0.1'),
		original.replace('"model":"jev-1.13"', '"model":"jev-1.13","model":"other"'),
		"[".repeat(70) + "0" + "]".repeat(70), new Uint8Array([0xff, 0xfe]),
	]) await assert.rejects(evaluateTypesafe(basic(), {}, options(async () => new Response(body))), code("TYPESAFE_RESPONSE_INVALID"));
});

test("valid UTF-8 split across streamed chunks is preserved", async () => {
	const expected = responseBody(); expected.model = "jev-€";
	const bytes = new TextEncoder().encode(JSON.stringify(expected));
	let index = 0;
	const body = new ReadableStream({ pull(controller) { if (index < bytes.length) controller.enqueue(bytes.slice(index, ++index)); else controller.close(); } });
	const result = await evaluateTypesafe(basic(), {}, options(async () => new Response(body)));
	assert.equal(result.model, expected.model);
});

test("advertised response oversize is rejected without reading body", async () => {
	let reads = 0; let cancelled = 0;
	const body = { getReader() { reads++; assert.fail("must not read"); }, cancel() { cancelled++; } };
	await assert.rejects(evaluateTypesafe(basic(), { typesafeMaxResponseBytes: 128 }, options(async () => ({ status: 200, headers: new Headers({ "content-length": "1000000" }), body }))), code("TYPESAFE_RESPONSE_TOO_LARGE"));
	assert.equal(reads, 0); assert.ok(cancelled >= 1);
});

test("stream byte cap holds with missing/lying Content-Length and cancels transport", async () => {
	for (const header of [undefined, "1"]) {
		let cancelled = false; let receivedSignal; let chunks = 0;
		const body = new ReadableStream({ pull(controller) { chunks++; controller.enqueue(new Uint8Array(64)); }, cancel() { cancelled = true; } });
		await assert.rejects(evaluateTypesafe(basic(), { typesafeMaxResponseBytes: 100 }, options(async (_url, init) => {
			receivedSignal = init.signal;
			return new Response(body, { headers: header ? { "content-length": header } : {} });
		})), code("TYPESAFE_RESPONSE_TOO_LARGE"));
		assert.ok(cancelled); assert.ok(receivedSignal.aborted); assert.ok(chunks <= 3);
	}
});

test("exact response byte boundary succeeds; one byte less fails", async () => {
	const text = JSON.stringify(responseBody()); const bytes = Buffer.byteLength(text);
	await evaluateTypesafe(basic(), { typesafeMaxResponseBytes: bytes }, options(async () => new Response(text)));
	await assert.rejects(evaluateTypesafe(basic(), { typesafeMaxResponseBytes: bytes - 1 }, options(async () => new Response(text))), code("TYPESAFE_RESPONSE_TOO_LARGE"));
});

test("unbounded text-only fallback is never called", async () => {
	await assert.rejects(evaluateTypesafe(basic(), {}, options(async () => ({ status: 200, text() { assert.fail("unbounded text read"); }, json() { assert.fail("unbounded json read"); } }))), code("TYPESAFE_RESPONSE_INVALID"));
});

for (const status of [301, 302, 303, 307, 308]) test(`HTTP ${status} redirect is rejected without forwarding credentials`, async () => {
	let calls = 0; let cancelled = false;
	await assert.rejects(evaluateTypesafe(basic(), {}, options(async (_url, init) => {
		calls++; assert.equal(init.redirect, "manual");
		return new Response(new ReadableStream({ cancel() { cancelled = true; } }), { status, headers: { location: "https://evil.example/SECRET_REASON" } });
	})), code("TYPESAFE_REDIRECT"));
	assert.equal(calls, 1); assert.ok(cancelled);
});

test("already-followed redirect from injected fetch is also rejected", async () => {
	await assert.rejects(evaluateTypesafe(basic(), {}, options(async () => ({ status: 200, redirected: true }))), code("TYPESAFE_REDIRECT"));
});

for (const status of [429, 529, 502, 503, 504]) test(`retryable HTTP ${status} backs off, disposes error body, and retries only once by default`, async () => {
	let calls = 0; let cancellations = 0; const signals = []; const bodies = [];
	const result = await evaluateTypesafe(basic(), {}, options(async (_url, init) => {
		signals.push(init.signal); bodies.push(init.body); calls++;
		if (calls === 1) return { status, headers: new Headers({ "retry-after": "0" }), body: { getReader() { assert.fail("must not read error body"); }, cancel() { cancellations++; } } };
		return jsonResponse();
	}));
	assert.equal(calls, 2); assert.equal(cancellations, 1);
	assert.equal(signals[0], signals[1]); assert.equal(bodies[0], bodies[1]);
	assert.ok(result.elapsedMs >= 200);
});

test("exhausted transient response stops at configured total attempts with only numeric status", async () => {
	let calls = 0;
	await assert.rejects(evaluateTypesafe(basic(), { typesafeRetries: 2 }, options(async () => { calls++; return new Response(`SECRET_BODY ${API_KEY}`, { status: 503 }); })), error => {
		code("TYPESAFE_HTTP")(error); assert.equal(error.status, 503); return true;
	});
	assert.equal(calls, 3);
});

test("zero retries and nonretryable statuses fail promptly and privately", async () => {
	for (const status of [400, 401, 403, 404, 408, 422, 500, 501, 505, 429]) {
		let calls = 0;
		await assert.rejects(evaluateTypesafe(basic(), status === 429 ? { typesafeRetries: 0 } : {}, options(async () => { calls++; return new Response(`SECRET_BODY ${API_KEY}`, { status, statusText: "SECRET_REASON" }); })), error => {
			code("TYPESAFE_HTTP")(error); assert.equal(error.status, status); return true;
		});
		assert.equal(calls, 1);
	}
});

test("network and stream errors are sanitized without leaking causes or retrying", async () => {
	for (const fetchImpl of [
		() => { throw new Error(`SECRET_REASON ${API_KEY}`, { cause: new Error("SECRET_BODY") }); },
		async () => new Response(new ReadableStream({ pull(controller) { controller.error(new Error(`SECRET_BODY ${API_KEY}`)); } })),
	]) {
		let calls = 0;
		await assert.rejects(evaluateTypesafe(basic(), {}, options((...args) => { calls++; return fetchImpl(...args); })), code("TYPESAFE_NETWORK"));
		assert.equal(calls, 1);
	}
});

test("pre-aborted and invalid signals are rejected without fetch or leaked reasons", async () => {
	const controller = new AbortController(); controller.abort(new Error(`SECRET_REASON ${API_KEY}`));
	await assert.rejects(evaluateTypesafe(basic(), {}, { ...options(() => assert.fail("must not fetch")), signal: controller.signal }), code("TYPESAFE_CANCELLED"));
	assert.equal(getEventListeners(controller.signal, "abort").length, 0);
	await assert.rejects(evaluateTypesafe(basic(), {}, { ...options(() => assert.fail("must not fetch")), signal: {} }), code("TYPESAFE_REQUEST"));
});

test("caller cancellation aborts cooperative fetch and discards its private abort error", async () => {
	const controller = new AbortController(); let receivedSignal;
	const pending = evaluateTypesafe(basic(), {}, { signal: controller.signal, ...options((_url, init) => {
		receivedSignal = init.signal;
		return new Promise((_resolve, reject) => { init.signal.addEventListener("abort", () => reject(new Error(`SECRET_REASON ${API_KEY}`)), { once: true }); controller.abort("SECRET_REASON"); });
	}) });
	await assert.rejects(pending, code("TYPESAFE_CANCELLED"));
	assert.ok(receivedSignal.aborted); assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});

test("deadline bounds non-cooperative hanging fetch and disposes its eventual response", async () => {
	let resolveFetch; let receivedSignal; let cancelled = false;
	const started = performance.now();
	await assert.rejects(evaluateTypesafe(basic(), { typesafeTimeoutMs: 25 }, options((_url, init) => {
		receivedSignal = init.signal; return new Promise(resolve => { resolveFetch = resolve; });
	})), code("TYPESAFE_TIMEOUT"));
	assert.ok(performance.now() - started < 1000); assert.ok(receivedSignal.aborted);
	resolveFetch({ status: 200, body: { cancel() { cancelled = true; } } });
	await new Promise(resolve => setImmediate(resolve));
	assert.ok(cancelled);
});

test("timeout covers stalled body reads and does not await stalled cancellation", async () => {
	let cancelled = false; let receivedSignal;
	const body = new ReadableStream({ pull() { return new Promise(() => {}); }, cancel() { cancelled = true; return new Promise(() => {}); } });
	const started = performance.now();
	await assert.rejects(evaluateTypesafe(basic(), { typesafeTimeoutMs: 25 }, options(async (_url, init) => { receivedSignal = init.signal; return new Response(body); })), code("TYPESAFE_TIMEOUT"));
	assert.ok(cancelled); assert.ok(receivedSignal.aborted); assert.ok(performance.now() - started < 1000);
});

test("caller cancellation covers stalled body reads", async () => {
	const controller = new AbortController(); let cancelled = false;
	const body = new ReadableStream({ pull() { controller.abort(`SECRET_REASON ${API_KEY}`); return new Promise(() => {}); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
	await assert.rejects(evaluateTypesafe(basic(), {}, { signal: controller.signal, ...options(async () => new Response(body)) }), code("TYPESAFE_CANCELLED"));
	assert.ok(cancelled); assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});

test("synchronously ready/empty streams cannot starve the monotonic deadline", async () => {
	let cancelled = false;
	const body = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array()); }, cancel() { cancelled = true; } });
	await assert.rejects(evaluateTypesafe(basic(), { typesafeTimeoutMs: 20 }, options(async () => new Response(body))), code("TYPESAFE_TIMEOUT"));
	assert.ok(cancelled);
});

test("overall deadline bounds server Retry-After and is not reset between attempts", async () => {
	let calls = 0;
	await assert.rejects(evaluateTypesafe(basic(), { typesafeTimeoutMs: 40, typesafeRetries: 3 }, options(async () => {
		calls++; return new Response("SECRET_BODY", { status: 429, headers: { "retry-after": "999999999999999999999" } });
	})), code("TYPESAFE_TIMEOUT"));
	assert.equal(calls, 1);
	calls = 0;
	await assert.rejects(evaluateTypesafe(basic(), { typesafeTimeoutMs: 320, typesafeRetries: 3 }, options(async () => {
		calls++; return new Response("SECRET_BODY", { status: 503 });
	})), code("TYPESAFE_TIMEOUT"));
	assert.equal(calls, 2);
});

test("retry backoff is capped even for enormous Retry-After", async () => {
	let calls = 0;
	const result = await evaluateTypesafe(basic(), { typesafeTimeoutMs: 5000 }, options(async () => {
		calls++;
		if (calls === 1) return new Response("SECRET_BODY", { status: 429, headers: { "retry-after": "999999999999999999999" } });
		return jsonResponse();
	}));
	assert.equal(calls, 2); assert.ok(result.elapsedMs >= 1900 && result.elapsedMs < 4000);
});

test("cancellation during backoff prevents another credentialed attempt", async t => {
	const controller = new AbortController(); let calls = 0; let timer;
	t.after(() => clearTimeout(timer));
	await assert.rejects(evaluateTypesafe(basic(), {}, { signal: controller.signal, ...options(async () => {
		calls++; timer = setTimeout(() => controller.abort("SECRET_REASON"), 10);
		return new Response("SECRET_BODY", { status: 529 });
	}) }), code("TYPESAFE_CANCELLED"));
	assert.equal(calls, 1); assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});

test("successful evaluation releases caller abort listeners", async () => {
	const controller = new AbortController();
	await evaluateTypesafe(basic(), {}, { signal: controller.signal, ...options(async () => jsonResponse()) });
	assert.equal(getEventListeners(controller.signal, "abort").length, 0);
	controller.abort("SECRET_REASON");
});
