/**
 * Run the relay worker's own logic with GitHub stubbed out.
 *
 *   node tests/check_relay.mjs
 *
 * The relay is the only piece of the refresh path that holds a secret, so it is the
 * one that most deserves to be executed rather than read. This harness imports
 * `relay/worker.js` and drives its `fetch` handler: it checks the origin gate, the
 * missing-secret refusal, the cooldown read from GitHub's own run list, the pinned
 * workflow and ref, and that GitHub's error body never travels back to the page.
 *
 * The worker keeps a per-isolate timestamp, so a test that needs a clean slate
 * imports its own instance (a distinct URL is a distinct module instance in Node).
 * Nothing here reaches the network: global `fetch` is replaced with a stub.
 */
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const WORKER_URL = pathToFileURL(resolve("relay/worker.js")).href;
const ORIGIN = "https://zurplox.github.io";
const DISPATCH_URL = "https://api.github.com/repos/Zurplox/garmin-bio-dashboard/actions/workflows/daily_sync.yml/dispatches";
const RUNS_MATCH = "/runs?per_page=1";

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
}

let instanceSeq = 0;
/** A worker instance whose per-isolate cooldown has not been touched. */
async function freshWorker() {
  instanceSeq += 1;
  return (await import(`${WORKER_URL}?isolate=${instanceSeq}`)).default;
}

/** Install a stub GitHub and return what the worker actually asked it to do. */
function stubGitHub({ runsAt = null, dispatchStatus = 204, dispatchBody = "" } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, method: (init.method || "GET").toUpperCase(), headers: init.headers || {}, body: init.body });
    if (url.includes(RUNS_MATCH)) {
      if (runsAt === null) return new Response("{}", { status: 500 });
      const runs = runsAt === 0 ? [] : [{ run_started_at: new Date(runsAt).toISOString(), created_at: new Date(runsAt).toISOString() }];
      return new Response(JSON.stringify({ workflow_runs: runs }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url === DISPATCH_URL) {
      // A 204 must carry no body at all, which is what GitHub returns on success.
      return dispatchStatus === 204
        ? new Response(null, { status: 204 })
        : new Response(dispatchBody, { status: dispatchStatus });
    }
    return new Response("unexpected", { status: 500 });
  };
  return calls;
}

function request(origin = ORIGIN, method = "POST") {
  return new Request("https://relay.example.workers.dev/", {
    method,
    headers: origin ? { Origin: origin, "Content-Type": "application/json" } : { "Content-Type": "application/json" },
    body: method === "POST" ? JSON.stringify({ action: "sync" }) : undefined,
  });
}

const ENV = { GITHUB_TOKEN: "test-token", ALLOWED_ORIGIN: ORIGIN };

// --- preflight -------------------------------------------------------------
let worker = await freshWorker();
stubGitHub();
let resp = await worker.fetch(request(ORIGIN, "OPTIONS"), ENV);
check("preflight is 204 with the dashboard origin echoed",
  resp.status === 204 && resp.headers.get("Access-Control-Allow-Origin") === ORIGIN, String(resp.status));

// --- the page's origin is the only caller ----------------------------------
worker = await freshWorker();
stubGitHub();
resp = await worker.fetch(request("https://evil.example"), ENV);
check("another origin is refused", resp.status === 403, String(resp.status));

worker = await freshWorker();
stubGitHub();
resp = await worker.fetch(request(""), ENV);
check("a request with no Origin is refused", resp.status === 403, String(resp.status));

// --- a missing secret is not silently a success ----------------------------
worker = await freshWorker();
stubGitHub();
resp = await worker.fetch(request(), { ALLOWED_ORIGIN: ORIGIN });
check("a missing GITHUB_TOKEN reports 500 rather than pretending", resp.status === 500, String(resp.status));

// --- the token never comes back --------------------------------------------
worker = await freshWorker();
stubGitHub({ dispatchStatus: 401, dispatchBody: '{"message":"Bad credentials","token":"test-token"}' });
resp = await worker.fetch(request(), ENV);
const leaked = await resp.text();
check("GitHub's error body is not forwarded",
  resp.status === 502 && !leaked.includes("test-token") && !leaked.includes("Bad credentials"), leaked.slice(0, 60));

// --- a recent run is held back ---------------------------------------------
worker = await freshWorker();
stubGitHub({ runsAt: Date.now() - 30_000 });
resp = await worker.fetch(request(), ENV);
let payload = await resp.json();
check("a run 30s ago is refused with 429 and a wait",
  resp.status === 429 && payload.retry_in_seconds > 0, JSON.stringify(payload));

// --- and an old one is not --------------------------------------------------
worker = await freshWorker();
const calls = stubGitHub({ runsAt: Date.now() - 3_600_000 });
resp = await worker.fetch(request(), ENV);
payload = await resp.json();
check("a run an hour ago does not block a new dispatch",
  resp.status === 202 && payload.dispatched === true, JSON.stringify(payload));

const dispatchCall = calls.find(c => c.url === DISPATCH_URL);
check("the dispatch is a POST to the pinned workflow", Boolean(dispatchCall) && dispatchCall.method === "POST");
check("the dispatch is pinned to main and ignores the request body",
  dispatchCall?.body === JSON.stringify({ ref: "main" }), String(dispatchCall?.body));
check("the dispatch carries the token from the environment",
  String(dispatchCall?.headers.Authorization).includes("test-token"));

// --- an in-isolate burst is caught even if the lookup fails -----------------
worker = await freshWorker();
stubGitHub({ runsAt: null });
const burstFirst = await worker.fetch(request(), ENV);
const burstSecond = await worker.fetch(request(), ENV);
check("with the runs lookup down, the first call dispatches and the next is refused",
  burstFirst.status === 202 && burstSecond.status === 429, `${burstFirst.status}/${burstSecond.status}`);

// --- the window can be configured, including to zero ------------------------
worker = await freshWorker();
stubGitHub({ runsAt: null });
const zeroEnv = { ...ENV, MIN_INTERVAL_SECONDS: "0" };
const zeroFirst = await worker.fetch(request(), zeroEnv);
const zeroSecond = await worker.fetch(request(), zeroEnv);
check("MIN_INTERVAL_SECONDS=0 lets a second call through",
  zeroFirst.status === 202 && zeroSecond.status === 202, `${zeroFirst.status}/${zeroSecond.status}`);

// --- a GET is not a dispatch ------------------------------------------------
worker = await freshWorker();
stubGitHub();
resp = await worker.fetch(request(ORIGIN, "GET"), ENV);
check("a GET is refused", resp.status === 405, String(resp.status));

// --- report -----------------------------------------------------------------
const failed = results.filter(r => !r.ok);
for (const r of results) {
  console.log(`${r.ok ? "  ok" : "FAIL"}  ${r.name}${r.detail && !r.ok ? "  -> " + r.detail : ""}`);
}
console.log(`\n${results.length - failed.length}/${results.length} relay checks passed`);
process.exit(failed.length ? 1 : 0);
