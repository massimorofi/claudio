# Runbook: connect an external code-graph MCP server

**Audience:** the operator. **Audience for the agent-facing text:**
`claudio-backend/skills/coding/references/code-graph-discovery.md`.

Claudio ships **no code indexer**. A code-graph server (anything that indexes a
repository and answers structural questions over MCP) is an external process. You
run it wherever you like and register it as one entry under `mcp.servers`. Claudio
then learns what it can do from the MCP handshake alone — there is no manifest
file, no shared-directory contract, and no product name anywhere in Claudio's
config or prompts.

What you need to hand Claudio:

| Field | Value |
|---|---|
| `id` | any name you choose; it is quoted verbatim in the agent's prompt |
| `transport` | `streamable_http` |
| `url` | the server's MCP endpoint, e.g. `http://codegraph:7411/mcp` |
| `headers` | `Authorization: Bearer <token>` if the endpoint requires one |
| `capability` | optional; `code_graph` forces the codebase-lookup policy on |
| `enabled` | `true` to connect at agent start |

---

## 1. Run the indexer

### Option A — generic compose overlay (no host install)

`docker/mcp/codegraph.compose.yaml` is a **vendor-neutral overlay**: it runs *any*
Streamable-HTTP MCP indexer you point it at. Nothing in it names a product; the
image, port and token come from the environment, and the workspace is mounted
**read-only** (an indexer is a reader; write access invites index-time mutations
the agent cannot reproduce).

```bash
export CODEGRAPH_IMAGE=<registry>/<indexer>:<tag>   # required
export CODEGRAPH_PORT=7411                          # in-network port (default 7411)
export CODEGRAPH_TOKEN=<a-real-secret>              # required bearer token
# optional: CODEGRAPH_MCP_PATH=/mcp  CODEGRAPH_MOUNT=/workspace
#           CODEGRAPH_WORKSPACE_DIR=<host dir>  (defaults to CLAUDIO_WORKSPACE_DIR)

docker compose -f compose.yaml -f docker/mcp/codegraph.compose.yaml \
  --profile codegraph up -d
```

Notes:

- The service is behind the `codegraph` **profile**, so the default stack is
  untouched unless you pass `--profile codegraph`.
- It publishes **no host port**: only containers on `claudio-net` can reach it,
  which is exactly the agent. Reach it by service name: `http://codegraph:7411/mcp`.
- The workspace is mounted at the same path the agent uses (`/workspace` by
  default), so paths the graph returns are paths the agent can open. If you need
  more repositories, add them to the overlay's `volumes:` at their **identical
  host path** and make the indexer track them.
- The healthcheck assumes `curl` exists in the indexer image. Delete the
  `healthcheck:` block if it does not; nothing depends on it.
- The indexer's own env var names differ per product. The overlay passes the
  common shapes (`CODEGRAPH_PORT`/`MCP_PORT`, `CODEGRAPH_TOKEN`/`MCP_TOKEN`,
  `MCP_PATH`, `REPO`); add whatever else your indexer reads to `environment:`.

### Option B — a process on the host or another machine

Run it however it ships, bind it to a port Claudio's agent container can reach, and
require a bearer token if the bind is not localhost. Then use its real URL in step 2
(`host.docker.internal:7411/mcp` from inside Docker on Linux needs
`extra_hosts: ["host.docker.internal:host-gateway"]`).

---

## 2. Register it under `mcp.servers`

Edit the **live** config, `claudio-backend/config/config.yaml` (gitignored; the
container seeds it into the `claudio-config` volume on first boot). The template
`config.example.yaml` carries the same block commented out — copy it and fill it in:

```yaml
mcp:
  servers:
    - id: codegraph                 # operator-chosen; appears verbatim in the prompt
      transport: streamable_http
      command: []
      url: http://codegraph:7411/mcp
      headers:
        Authorization: Bearer <CODEGRAPH_TOKEN>
      capability: code_graph        # optional; see §4
      enabled: true
```

Rules and gotchas:

- `transport: streamable_http` is what makes Claudio open an HTTP MCP session to
  `url`. `command:` is only used by `stdio` entries; leave it `[]`.
- **`mcp.servers` is a list, so it cannot be set from the environment.** The
  `CLAUDIO__SECTION__KEY` overrides support exactly two levels; anything deeper
  (like a server's headers) must live in the YAML.
- The token in `headers` must be the one the server actually requires. Claudio
  sends the header verbatim; it does not fetch or template secrets.
- `id` is free text. It is *not* used to decide what the server can do — a server
  called `codegraph`, `graph`, or `x1` is treated identically. Only what the
  server advertises matters (§4).
- Keep `enabled: false` (or the block commented out) if you are not running an
  indexer. A fresh clone works with no indexer at all: no policy block is
  rendered, so the agent pays no tokens for tools it cannot call.

## 3. Restart the agent so it rebuilds its MCP toolset

The toolset is built at agent start, so a config edit needs a restart:

```bash
./claudio-ctl restart agent
# or, against a running API:
curl -fsS -X POST -H "Authorization: Bearer $CLAUDIO__SERVER__AUTH_TOKEN" \
  http://127.0.0.1:8100/agent/restart
```

---

## 4. Readiness is reported over MCP, not through a file

There is **no** index manifest on a shared mount and no config key pointing at one.
Everything Claudio knows about the code graph comes from the live connection:

1. **`initialize`** → the server's own `instructions` string. The server describes
   itself; Claudio renders that text rather than describing the server on its
   behalf.
2. **`tools/list`** → every tool's `name`, `description` and annotations
   (`readOnlyHint`, `destructiveHint`, `openWorldHint`). These prefixed names are
   the authoritative list the agent is given; the prompt never names a tool the
   server did not advertise.
3. **A status/coverage tool, if the server has one.** A tool whose advertised
   description says it reports index coverage or health is discovered from
   `tools/list` and called once per session, off the critical path of prompt
   assembly. Its answer is the readiness statement. **If no such tool is
   advertised, readiness is reported as *unknown*** — which is the honest answer,
   and nothing else changes.

How a server is recognised as a code-graph server (`claudio_core.mcp_capability`),
in precedence order:

| # | Rule | Source |
|---|---|---|
| 1 | `capability: code_graph` on the config entry | operator override; wins, no heuristics |
| 2 | the server's `initialize` instructions contain code-graph vocabulary | advertisement |
| 3 | ≥ 2 of its tool names/descriptions match the generic code-graph lexicon (`symbol`, `callers`, `callees`, `dependents`, `change impact`, `index coverage`, …) | advertisement |
| — | otherwise it is **not** a code-graph server, and no policy block is rendered | |

An id substring is never a signal. If your server self-describes well you can omit
`capability`; set it when the server is terse or its tool names are opaque.

---

## 5. Verify with `tools/list`

Do this **before** blaming the agent when the code-graph tools seem missing. It
answers "is the server reachable, does it accept my token, and what does it
actually advertise?" independently of Claudio.

Streamable-HTTP MCP needs two calls: `initialize` (which returns a session id in
the `mcp-session-id` response header) and then `tools/list` carrying that id. The
`Accept` header must list both media types.

```bash
URL=${URL:-http://codegraph:7411/mcp}
TOK=${TOK:-<CODEGRAPH_TOKEN>}

# 1) initialize -> capture headers, pull out the session id
hdr=$(mktemp)
curl -sS -D "$hdr" -o /tmp/mcp-init.json -X POST "$URL" \
  -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-06-18","capabilities":{},
        "clientInfo":{"name":"curl","version":"0"}}}'
sid=$(grep -i '^mcp-session-id:' "$hdr" | awk '{print $2}' | tr -d '\r')
echo "session: ${sid:-<none>}"
# the server's own instructions (what Claudio renders) are in this response:
python3 -c "import json,sys;d=json.load(open('/tmp/mcp-init.json'));print(json.dumps(d.get('result',{}).get('instructions','(no instructions)'),indent=2)[:1200])"

# 2) notifications/initialized (required by most servers before tools/list)
curl -sS -o /dev/null -X POST "$URL" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  ${sid:+-H "mcp-session-id: $sid"} \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3) tools/list -> the names and descriptions Claudio will use
curl -sS -X POST "$URL" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  ${sid:+-H "mcp-session-id: $sid"} \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | sed -n 's/^data: //p; p' \
  | python3 -c "
import json,sys
raw=sys.stdin.read().strip()
d=json.loads(raw)
tools=d.get('result',{}).get('tools',[])
print(f'{len(tools)} tools advertised')
for t in tools:
    print(' -', t['name'], '::', (t.get('description') or '')[:90].replace(chr(10),' '))
"
```

Read the output three ways, in this order:

1. **0 tools / HTTP error** → the server is down, the URL is wrong, or the token
   was rejected. Claudio would have skipped the entry too.
2. **Tools listed, but none with code-graph vocabulary** (`symbol`, `callers`,
   `dependents`, `change impact`, `index coverage`) → Claudio will connect but
   will *not* render the lookup policy unless you set `capability: code_graph`.
   That is the override doing exactly what it is for.
3. **A tool like `index_status` / `get_workspace_capabilities` / `coverage_report`**
   → that is the readiness source. Call it once and check `tracked_repos` covers
   the workspace the agent is working in; if it does not, an empty graph result
   means "not indexed", not "not present".

Inside Claudio, the same information surfaces in two places: the system prompt
sections **"Query tools advertised by `<id>`"** and the readiness block, and in
the agent log line where the MCP catalog is built. If the prompt has no code-graph
section, the server did not connect or did not qualify — check `tools/list` first.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No code-graph section in the prompt | entry `enabled: false`, or config edited but agent not restarted | set `enabled: true`, `./claudio-ctl restart agent` |
| `401` / `403` from `tools/list` | bearer token mismatch | make `headers.Authorization` exactly equal the token the server requires |
| `Connection refused` / DNS failure | wrong host; the service is on another network; you used `localhost` inside a container | use the compose **service name** (`http://codegraph:7411/mcp`), and put both services on `claudio-net` |
| Connects, but no policy block | server advertises nothing code-graph-shaped and no `capability` set | add `capability: code_graph` |
| Policy appears but readiness says **unknown** | the server advertises no status/coverage tool, or it errored | expected and honest; add/expose a status tool if you want a coverage claim |
| Graph returns nothing for code that exists | the repo is not in the server's tracked set, or the workspace is mounted at a different path than the agent sees | mount at the identical path (`CODEGRAPH_MOUNT`), re-index, then re-check the status tool |
| `--profile codegraph` starts nothing | profile not passed, or `CODEGRAPH_IMAGE`/`CODEGRAPH_TOKEN` unset (both are `:?`-required) | export them, or add them to `.env` |

## 7. What this replaced

The old deployment built the indexer inside Claudio's repo (`docker/gortex/`),
started it as a Claudio compose service, and passed index coverage to the agent
through a JSON file written onto the shared `/workspace` mount. That file was an
out-of-band channel: invisible to capability negotiation, stale by design, and a
hard dependency on one product's entrypoint. It is gone. Readiness now comes from
`tools/list` plus a status tool call (§4), and the indexer's lifecycle is the
operator's. See `docs/gortex-decoupling-review.md` (findings F2, F5, F6, F7, F8 and
target contract T1/T3/T4) for the reasoning.
