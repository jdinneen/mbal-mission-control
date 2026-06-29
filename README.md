# Monterey Bay AI Lab — public Mission Control site

A standalone static site (the lab's findings, models, confusion matrices, lakehouse) with a
**live search** box. The site itself is static (safe on GitHub Pages); search is answered by a
tiny **Cloudflare Worker** that holds your Anthropic key as a secret — so the key is never public.

Files:
- `index.html` — the site (loads `data.json`, calls the search proxy)
- `data.json` — published data snapshot (regenerate with `build_snapshot.py`)
- `build_snapshot.py` — pulls a fresh snapshot from the local cockpit (http://127.0.0.1:8770)
- `worker.js` + `wrangler.toml` — the search proxy

---

## 1. Preview locally
```
python -m http.server 8000      # in this folder
# open http://127.0.0.1:8000
```
Findings/models/lakehouse render from `data.json`. Raw export downloads are intentionally not published from the site. Search will say "not wired yet" until step 3.

## 2. Publish the site on GitHub Pages
You run these (they use *your* GitHub login — I never touch your credentials):
```
gh repo create mbal-mission-control --public --source . --remote origin --push
# (or: create the repo in the GitHub UI, then `git push -u origin main`)
```
Then on GitHub: **Settings → Pages → Build from branch → `main` / root**.
Your site appears at `https://YOURNAME.github.io/mbal-mission-control/`.

## 3. Deploy the search proxy (Cloudflare Worker — free tier)
First get a **free Google AI Studio key**: https://aistudio.google.com → "Get API key"
(signed in as your Google account). This is separate from GCP billing — it has its own free tier.
```
npm i -g wrangler
wrangler login                                  # your Cloudflare login
wrangler secret put GOOGLE_AI_KEY               # paste your AI Studio key — stays secret on Cloudflare
wrangler deploy
```
The worker uses Gemini Flash (`gemini-2.0-flash`). To change the model: `wrangler secret put MODEL`
(or set it under `[vars]` in `wrangler.toml`).
This prints a URL like `https://mbal-search.YOURNAME.workers.dev`.
- Lock it to your site: in `wrangler.toml` set `ALLOWED_ORIGIN = "https://YOURNAME.github.io"`, then `wrangler deploy` again.
- (Optional) turn on rate limiting: `wrangler kv namespace create RL`, paste the id into `wrangler.toml`, `wrangler deploy`.

## 4. Wire search into the site
In `index.html`, set:
```
const PROXY_URL = "https://mbal-search.YOURNAME.workers.dev";
```
Commit + push. Search is now live for visitors.

## 5. Stay within budget
The free AI Studio tier has its own rate limits and **no charge** — for a shared site that's
usually all you need (no billing required). The worker also caps model (Gemini Flash), output
tokens (512), context size, and (optionally) requests/day via the KV rate limiter.
If you later attach billing to the key, set a budget alert in Google Cloud (~$20).

## 6. Refresh the published data later
With the cockpit running locally:
```
python build_snapshot.py        # rewrites data.json from live state
git add data.json && git commit -m "refresh snapshot" && git push
```

`build_geo_downloads.py` may refresh map layers, but it must keep `downloads.json` empty and must not recreate raw CSV, Markdown, or full-snapshot download bundles.

---

**Privacy note:** `build_snapshot.py` strips local file paths, usernames, GPU/process/agent details —
the published `data.json` contains only the science summaries needed to render the site.
**Never** commit `.env`, `.dev.vars`, or your API key (the `.gitignore` already blocks them).
