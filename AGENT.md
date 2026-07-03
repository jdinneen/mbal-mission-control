# MBAL Agent Operating Guide

Scope: repo-scoped work in `mbal-mission-control`. Keep this guide short, current, and operational so agents spend fewer calls rediscovering the same facts.

## Startup

- Use this file as the first-pass operating map for repo work. Do not broadly search for other guidance unless the task points to a subdirectory.
- For non-trivial repo work, run `node scripts/agent-context.mjs` before exploring many files.
- For simple questions or one-command checks, skip broad context gathering and answer directly.
- Prefer `rg` for search and documented scripts over ad hoc file-by-file discovery.
- Higher-priority system, developer, security, and explicit user instructions win over this file.

## Agent Surfaces

- Codex CLI, Codex desktop, and Codex app load `AGENTS.md` automatically.
- Claude Code loads `CLAUDE.md`; keep it identical to this file so it does not need a second read.
- Agents that only know `AGENT.md` load the same guide from that file.
- Claude Desktop and Claude Science do not reliably auto-load local Markdown. Paste or import `AGENT.md` into Project Instructions or project knowledge there; use connectors, skills, and app settings for tools, credentials, and compute.
- Permissions, credentials, and storage policy belong in app settings, MCP/connector config, deployment settings, shell environment, or `.claude/settings.json`, not in Markdown.

## Project Map

- `index.html` is the static public Mission Control site.
- `data.json` is the published static snapshot; refresh it with `python build_snapshot.py` only when the local cockpit at `http://127.0.0.1:8770` is running.
- `qa_eval.mjs` is the fast local site integrity check.
- `index.html` currently points `PROXY_URL` at the live Google Cloud Run search proxy: `https://mbal-search-957730565502.us-central1.run.app`.
- `worker.js` and `wrangler.toml` are a Cloudflare Worker alternate path. Do not assume they are live unless `PROXY_URL` is changed.
- `build_geo_downloads.py` refreshes map layers and must not recreate raw public download bundles.
- `downloads.json` is a disabled manifest: `downloads_enabled` stays `false` and `items` stays empty.
- `gpu_fold_run/` is a local RTX 5090 validation sidecar. Do not run or edit GPU workflow outputs unless the user asks.
- `reports/` and `videos/` are public artifacts consumed by the site.

## Commands

- Context summary: `node scripts/agent-context.mjs`
- Main check: `node scripts/check.mjs`
- Windows fallback check: `powershell -ExecutionPolicy Bypass -File scripts/check.ps1`
- Direct site QA: `node qa_eval.mjs`
- Local preview: `python -m http.server 8000`
- Snapshot refresh: `python build_snapshot.py`
- Map refresh: `python build_geo_downloads.py`
- Live proxy check: `rg "const PROXY_URL" index.html`
- Cloudflare alternate deploy: `wrangler deploy` only after intentionally changing `PROXY_URL` and docs.
- GPU validation: from WSL inside this repo, `cd gpu_fold_run && bash run_fold.sh`

## Secrets And Credentials

- Never read, print, or commit `.env`, `.env.*`, `.dev.vars`, `.wrangler/`, private keys, token stores, or credential JSON.
- Use named secrets and settings only: `GOOGLE_AI_KEY`, optional `MODEL`, optional `ALLOWED_ORIGIN`, and optional Cloudflare KV binding `RL`.
- The live proxy key lives in the deployment platform secret store. Do not put API keys in docs, source, screenshots, or final answers.
- Claude Code permission rules reduce prompts and block some file-tool reads; they do not replace OS/app secret storage or shell sandboxing.

## Editing Rules

- Keep the site static and GitHub Pages safe.
- Do not add raw CSV, Markdown, or full-snapshot public downloads.
- Keep `downloads.json` disabled with no `items` unless the user explicitly changes the publication policy.
- Keep science claims evidence-gated: separate supported results from hypotheses, validation needed, deployment proof needed, and wording polish.
- Define local MBAL terms in plain English the first time they appear, especially shorthand such as "station memory", "lakehouse", "lead-time watchlist", or model-family names.
- Do not invent deployment status, model skill, biological validation, or regulatory claims.
- Avoid broad refactors; change the smallest files that satisfy the task.
- On untrusted branches, inspect changes to `scripts/` and `.claude/settings.json` before relying on allowed commands.
- Avoid broad searches over heavy artifacts such as `data.json`, `videos/`, `*.geojson`, and `gpu_fold_run/out/` unless directly relevant.
- Preserve user or generated work in untracked folders, especially `gpu_fold_run/`.

## Verification

- After editing `index.html`, `data.json`, `downloads.json`, proxy docs, `worker.js`, `wrangler.toml`, or build scripts, run `node scripts/check.mjs`.
- If a check cannot run because a local service, key, GPU, or external login is missing, say exactly what was not run and why.
- For visual/site changes, preview locally when practical and inspect the affected screen or artifact.

## Subagents And Reviewers

- Use subagents or reviewers only when they materially reduce risk or the user asks for an adversarial pass.
- A useful adversarial pass checks for instruction drift, secret exposure, raw-download leakage, unsupported scientific claims, stale commands, and unnecessary bloat.
- Do not launch parallel agents that may edit the same files.
