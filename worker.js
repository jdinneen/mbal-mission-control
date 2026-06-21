/**
 * MBAL search proxy — Cloudflare Worker (Google Gemini via AI Studio key).
 *
 * Holds your Google AI Studio key as a SECRET (never shipped to the browser) and answers
 * science questions from the published data, grounded and capped for a public site.
 *
 * Guardrails:
 *   - model = Gemini Flash
 *   - max output tokens capped (MAX_OUTPUT)
 *   - input context truncated (MAX_CONTEXT chars) + question length cap
 *   - optional per-IP + global daily rate limit if you bind a KV namespace "RL"
 *
 * Deploy (see README.md):
 *   wrangler secret put GOOGLE_AI_KEY      # paste your AI Studio key — stays secret on Cloudflare
 *   wrangler deploy
 */

const MODEL = "gemini-2.0-flash";   // override with the MODEL env var if needed
const MAX_OUTPUT = 900;
const MAX_CONTEXT = 12000;
const MAX_QUESTION = 500;
const PER_IP_PER_DAY = 40;     // used only if a KV namespace "RL" is bound
const GLOBAL_PER_DAY = 1500;   // used only if a KV namespace "RL" is bound

function cors(origin, allowed) {
  const o = (allowed && allowed !== "*") ? allowed : (origin || "*");
  return {
    "Access-Control-Allow-Origin": o,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

async function rateLimited(env, ip) {
  if (!env.RL) return false; // KV optional — skip if not bound
  const day = new Date().toISOString().slice(0, 10);
  const ipKey = `ip:${day}:${ip}`;
  const gKey = `g:${day}`;
  const ipN = parseInt((await env.RL.get(ipKey)) || "0", 10);
  const gN = parseInt((await env.RL.get(gKey)) || "0", 10);
  if (ipN >= PER_IP_PER_DAY || gN >= GLOBAL_PER_DAY) return true;
  await env.RL.put(ipKey, String(ipN + 1), { expirationTtl: 90000 });
  await env.RL.put(gKey, String(gN + 1), { expirationTtl: 90000 });
  return false;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = env.ALLOWED_ORIGIN || "*";
    const headers = { ...cors(origin, allowed), "Content-Type": "application/json" };

    if (request.method === "OPTIONS") return new Response(null, { headers });
    if (request.method !== "POST")
      return new Response(JSON.stringify({ error: "POST only" }), { status: 405, headers });
    if (!env.GOOGLE_AI_KEY)
      return new Response(JSON.stringify({ error: "server not configured (no GOOGLE_AI_KEY)" }), { headers });

    let body;
    try { body = await request.json(); } catch { body = {}; }
    const question = String(body.question || "").trim().slice(0, MAX_QUESTION);
    const context = String(body.context || "").slice(0, MAX_CONTEXT);
    if (!question)
      return new Response(JSON.stringify({ error: "empty question" }), { headers });

    const ip = request.headers.get("CF-Connecting-IP") || "0";
    if (await rateLimited(env, ip))
      return new Response(JSON.stringify({ error: "rate limit reached — try again later" }), { headers });

    const model = env.MODEL || MODEL;
    const system =
      "You are the search assistant for the Monterey Bay AI Lab's public Mission Control. " +
      "Answer ONLY from the DATA provided. Stay strictly on coastal-water science: observations, models, evidence, metrics, uncertainty, and caveats. " +
      "If the answer is not in the data, or the question is outside that scientific scope, say so plainly. Never invent numbers. " +
      "Output the answer itself, not the formatting rules. Use 'Bottom line:' for 1-2 plain sentences. " +
      "When reporting findings or comparisons, add 'Matrix:' with a compact table of claim/result/verdict. " +
      "Add 'Explanation:' with the exact numbers, baselines, caveats, and how the result was checked. " +
      "Do not explain what each section is supposed to do.";
    const prompt = `DATA:\n${context}\n\nQUESTION: ${question}\n\nAnswer:`;

    const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${env.GOOGLE_AI_KEY}`;
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          system_instruction: { parts: [{ text: system }] },
          contents: [{ role: "user", parts: [{ text: prompt }] }],
          generationConfig: { maxOutputTokens: MAX_OUTPUT, temperature: 0.2 },
        }),
      });
      const j = await r.json();
      if (!r.ok)
        return new Response(JSON.stringify({ error: j.error?.message || "LLM error" }), { headers });
      const answer = (j.candidates?.[0]?.content?.parts || [])
        .map((p) => p.text || "").join("").trim();
      return new Response(JSON.stringify({ answer, model }), { headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), { headers });
    }
  },
};
