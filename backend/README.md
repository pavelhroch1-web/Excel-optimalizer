# Field Force Optimizer backend (Fáze 0)

A thin FastAPI wrapper around the existing, unchanged `desktop_client/engines/`
Python engines. It owns no business logic - every request downloads the real
`.xlsx` from this GitHub repo, runs an already-verified engine against it,
and (for the one write action) commits the result back to GitHub. No local
disk is relied on between requests - the repo itself is the database.

## One-time setup (you, not Claude - these need your own accounts/logins)

1. **Create a GitHub personal access token** (Settings → Developer settings →
   Fine-grained tokens) scoped to just this repo, with **Contents:
   read and write** permission. Copy the token.

2. **Create a free Render.com account**, connect it to your GitHub account.

3. **New Web Service** on Render:
   - Repository: this repo
   - Root directory: `backend`
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Instance type: Free
   - Environment variables:
     - `APP_PASSWORD` = a password you choose (this is your login)
     - `GITHUB_TOKEN` = the token from step 1
     - `GITHUB_REPO` = `pavelhroch1-web/excel-optimalizer` (or your fork's `owner/repo`)
     - `ALLOWED_ORIGIN` = `https://pavelhroch1-web.github.io` (your Pages origin,
       so only your frontend can call this API - locks CORS down instead of `*`)

4. Deploy. Render gives you a URL like `https://field-force-optimizer.onrender.com`.

5. Open `web/config.js` in this repo, set
   `window.FFO_API_BASE = "https://field-force-optimizer.onrender.com";`,
   commit and push. GitHub Pages redeploys automatically
   (`.github/workflows/deploy-pages.yml`).

6. In repo Settings → Pages, set Source = "GitHub Actions" (one-time).

That's it - `https://pavelhroch1-web.github.io/excel-optimalizer/` is then
the live app.

## Notes

- **Free-tier cold starts**: Render's free web services sleep after 15
  minutes idle and take ~30-60s to wake on the next request. Fine for a
  once-a-week planning session; the first click after a break will just be
  slow, not broken.
- **Every "Generovat tour plán" is a real git commit** to `workbook/*.xlsx`
  in this repo - free version history/backups, visible in the repo's commit
  log same as any other change.
- **Nothing here touches business logic.** If Planning Engine needs a fix,
  it's fixed in `desktop_client/engines/planning_engine.py` (and mirrored in
  `office-scripts/PlanningEngine.ts`) exactly as before - this backend just
  calls whatever's there.
