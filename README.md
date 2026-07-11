# Monterey Bay AI Lab public Mission Control site

A standalone static site for public findings, models, confusion matrices, and the governed
lakehouse inventory. The public build is static: it loads `data.json`, uses curated local
answers from `answered.json`, and does not call a live model endpoint by default.

Files:
- `index.html` — the site (loads `data.json`; `PROXY_URL` stays empty in the public build)
- `data.json` — published data snapshot (regenerate with `build_snapshot.py`)
- `build_snapshot.py` — pulls a fresh snapshot from the local cockpit (http://127.0.0.1:8770)
- `worker.js` + `wrangler.toml` — optional search endpoint template, disabled unless wired intentionally

---

## 1. Preview locally
```
python -m http.server 8000      # in this folder
# open http://127.0.0.1:8000
```
Findings/models/lakehouse render from `data.json`. Raw export downloads are intentionally
not published from the site. The Ask view uses curated local answers unless `PROXY_URL` is
configured after a security review.

## 2. Publish the site on GitHub Pages
You run these (they use *your* GitHub login — I never touch your credentials):
```
gh repo create mbal-mission-control --public --source . --remote origin --push
# (or: create the repo in the GitHub UI, then `git push -u origin main`)
```
Then on GitHub: **Settings → Pages → Build from branch → `main` / root**.
Your site appears at `https://YOURNAME.github.io/mbal-mission-control/`.

## 3. Optional search endpoint
The public site intentionally ships with no live endpoint:
```
const PROXY_URL = "";
```

Only wire an endpoint after all of these are true:
- the API key is stored as a deployment secret such as `GOOGLE_AI_KEY`;
- CORS allows only the published site origin;
- per-IP and global request caps are enabled;
- the endpoint receives only the same public summaries already in `data.json`.

### Cloudflare Worker template
First provision a Google AI Studio key: https://aistudio.google.com → "Get API key".
```
npm i -g wrangler
wrangler login                                  # your Cloudflare login
wrangler secret put GOOGLE_AI_KEY               # paste your AI Studio key — stays secret on Cloudflare
wrangler deploy
```
The worker uses Gemini Flash (`gemini-2.0-flash`). To change the model: `wrangler secret put MODEL`
(or set it under `[vars]` in `wrangler.toml`).
This prints a URL like `https://mbal-search.YOURNAME.workers.dev`.
- Lock it to your site: in `wrangler.toml` set `ALLOWED_ORIGIN = "https://YOURNAME.github.io"`.
- Enable request caps: `wrangler kv namespace create RL`, paste the id into `wrangler.toml`.

## 4. Wire a new search endpoint into the site
Only change this after the endpoint controls above are active. In `index.html`, set:
```
const PROXY_URL = "https://mbal-search.YOURNAME.workers.dev";
```
Preview and publish only through the guarded script:
```
./publish.sh --dry-run "change search endpoint" index.html
./publish.sh "change search endpoint" index.html
```

## 5. Refresh the published data later
With the cockpit running locally:
```
python build_snapshot.py        # rewrites data.json from live state
./publish.sh --dry-run "refresh snapshot" data.json
./publish.sh "refresh snapshot" data.json
```

`build_geo_downloads.py` may refresh map layers, but it must keep `downloads.json` empty and must not recreate raw CSV, Markdown, or full-snapshot download bundles.

---

**Privacy note:** `build_snapshot.py` strips local file paths, usernames, GPU/process/agent details —
the published `data.json` contains only the science summaries needed to render the site.
**Never** commit `.env`, `.dev.vars`, or your API key (the `.gitignore` already blocks them).

## Publishing (read this — do not push by hand)

Deploy ONLY via `publish.sh`, and name every file intended for the release:
```
./publish.sh --dry-run "explain the change" index.html
./publish.sh "explain the change" index.html
```
The first command is a dry run: a preview that runs the privacy and integrity checks but changes
nothing. The file list is mandatory. The script refuses pre-existing staged work, unselected tracked
changes, and older local commits that are not already on `origin/main`. It stages only the named
files plus `version.json`; unrelated untracked files remain untouched. Every selected file that
looks like text is also scanned for local paths, email addresses, and likely secret values,
including decoded strings inside JSON and GeoJSON map data.

GitHub Pages debounces rapid pushes and can silently **skip** deploying a commit, and its content
delivery network (CDN), a distributed cache that serves nearby visitors, can retain an older copy.
Pushing directly and eyeballing the page can therefore look done while the live site is stale.
`publish.sh` stamps a unique id into `version.json`, pushes once, then polls the **live**
`version.json` until that id appears (nudging Pages if it skipped the deploy). It only exits success
after the canonical site serves that exact receipt and every selected file has the same byte
fingerprint as the committed file (or returns not-found when the release deleted it).
