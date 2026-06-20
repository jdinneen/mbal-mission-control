/**
 * MBAL search proxy — Cloudflare Worker.
 *
 * Holds your Anthropic API key as a SECRET (never shipped to the browser) and answers
 * search questions from the published data, grounded + capped so a public site can't
 * run up your bill.
 *
 * Cost guards (keep you near the $20 budget):
 *   - model = Claude Haiku (cheapest capable model)
 *   - max output tokens capped (MAX_OUTPUT)
 *   - input context truncated (MAX_CONTEXT chars)
 *   - optional per-IP + global daily rate limit if you bind a KV namespace "RL"
 *   - HARD BACKSTOP: set a $20 monthly spend limit in the Anthropic console.
 *
 * Deploy: see README.md. Set the secret with:  wrangler secret put ANTHROPIC_API_KEY
 */

const MODEL = "claude-haiku-4-5-20251001";
const MAX_OUTPUT = 512;
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
    if (!env.ANTHROPIC_API_KEY)
      return new Response(JSON.stringify({ error: "server not configured (no API key)" }), { headers });

    let body;
    try { body = await request.json(); } catch { body = {}; }
    const question = String(body.question || "").trim().slice(0, MAX_QUESTION);
    const context = String(body.context || "").slice(0, MAX_CONTEXT);
    if (!question)
      return new Response(JSON.stringify({ error: "empty question" }), { headers });

    const ip = request.headers.get("CF-Connecting-IP") || "0";
    if (await rateLimited(env, ip))
      return new Response(JSON.stringify({ error: "rate limit reached — try again later" }), { headers });

    const system =
      "You are the search assistant for the Monterey Bay AI Lab's public Mission Control. " +
      "Answer ONLY from the DATA provided. Quote the real numbers. If the answer is not in the data, " +
      "say so plainly. Be concise and plain-English. Never invent numbers.";
    const prompt = `DATA:\n${context}\n\nQUESTION: ${question}\n\nAnswer:`;

    try {
      const r = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-api-key": env.ANTHROPIC_API_KEY,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: MODEL,
          max_tokens: MAX_OUTPUT,
          system,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      const j = await r.json();
      if (!r.ok)
        return new Response(JSON.stringify({ error: j.error?.message || "LLM error" }), { headers });
      const answer = (j.content || []).map((c) => c.text || "").join("").trim();
      return new Response(JSON.stringify({ answer, model: MODEL }), { headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), { headers });
    }
  },
};
