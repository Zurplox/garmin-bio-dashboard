# Sync relay

The dashboard is a **public static page**, so it cannot hold the GitHub token that
starts a sync. Two things make that concrete rather than fussy:

* this repository has **secret scanning** and **push protection** enabled, so a
  committed token is detected — and GitHub revokes a leaked token in a public
  repository, which would silently break Refresh days later;
* anything in the page is readable by every visitor, and `Actions: write` on this
  repo is enough for a stranger to spend your Actions minutes.

The relay moves the token to where it can stay private: a Cloudflare Worker, where
it is an encrypted secret that the page never sees. The page only knows the
worker's URL. Once `SYNC_RELAY_URL` in `index.html` points at it, the token field
and its gear disappear, and no device has to be keyed in again.

## What the worker does

One thing: start `daily_sync.yml` on `main`.

* the workflow file and the ref are **pinned in the worker**, never taken from the
  request body;
* only `ALLOWED_ORIGIN` may call it, so another site cannot use it;
* a second request inside `MIN_INTERVAL_SECONDS` (default 300) is refused with
  `429`, checked against GitHub's own view of the last run — so finding the URL
  does not give a stranger a way to hammer your Actions;
* the token is never returned, logged or echoed; an error returns a status and a
  hint, never GitHub's body.

## Deploy it (about two minutes)

1. Create the token: GitHub → Settings → Developer settings → Personal access
   tokens → **Fine-grained** → repository access: only
   `Zurplox/garmin-bio-dashboard` → permission **Actions: read and write**. Nothing
   else is needed; this token cannot read your vault or your code.

2. Deploy the worker. Either paste `worker.js` into the Cloudflare dashboard
   (Workers & Pages → Create → Worker), or from this folder:

   ```bash
   npx wrangler deploy worker.js --name meridian-sync-relay
   ```

3. Store the token as a secret — never in a file, never in the repo:

   ```bash
   npx wrangler secret put GITHUB_TOKEN --name meridian-sync-relay
   # paste the PAT when prompted
   ```

   Optional plain-text variables:

   ```bash
   npx wrangler secret put ALLOWED_ORIGIN --name meridian-sync-relay   # default https://zurplox.github.io
   npx wrangler secret put MIN_INTERVAL_SECONDS --name meridian-sync-relay  # default 300
   ```

4. Put the worker URL in `index.html`:

   ```js
   const SYNC_RELAY_URL = "https://meridian-sync-relay.<your-subdomain>.workers.dev";
   ```

5. Evidence it works, without wasting a sync: open the page, press **Refresh**
   once. The toast should read *Triggering a sync…* and then either report the new
   vault or that the run is still going. If the relay refuses, the toast names the
   reason (`403` origin, `429` too soon, `502` GitHub refused).

With the relay live there is no gear button in the header, because there is
nothing to configure. The token path stays in the code as the fallback for a
checkout that has not deployed a relay — it is used only when
`SYNC_RELAY_URL` is empty.

## Why not just commit the token?

Because it does not survive. GitHub's secret scanning flags a committed credential
in a public repo and revokes GitHub-issued tokens, so the button would work for a
few minutes and then quietly stop. `tests/test_pipeline.py::SecretGuardTests` also
fails the build if a credential appears anywhere in the tree, by design.
