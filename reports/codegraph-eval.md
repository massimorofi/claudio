# Code-graph invocation eval

- mode: `offline` (stdlib + `claudio_core`, no network, no agent)
- workspace under test: `evals/fixtures/sample_repo`
- catalog under test: server id `codegraph`, 6 prefixed tools, status payload tracking the fixture
- scripted code-task prompts: 7
- **12 passed, 0 failed**

Renderers exercised for real: `mcp_capability.detect` / `render_code_graph_policy`, `codegraph.readiness_from_status` / `render_readiness_block`, `planact.derive_families` / `audit_run` / `render_planact_nudge`.

| check | pass | detail |
| --- | --- | --- |
| `policy_block_present` | PASS | 1 server(s) detected, block is 2654 chars |
| `block_names_real_tools` | PASS | all 6 advertised tool names appear in the block |
| `block_names_configured_server_id` | PASS | configured id 'codegraph' quoted in the policy block |
| `readiness_is_covered` | PASS | status=covered repo='/work/massimorofi-claudio/27c5d7b7-02de-486a-bb94-b4b10c2b198a-a1/evals/fixtures/sample_repo' block=451 chars |
| `readiness_unknown_without_status` | PASS | status=unknown detail='the code-graph server reported no index status, so coverage is unknown' |
| `zero_vendor_strings` | PASS | scanned 3 rendered blocks, no vendor token |
| `prompts_vendor_free` | PASS | 7 scripted prompts, none names a vendor |
| `planact_flags_narrated_not_called` | PASS | planned=['codegraph', 'the code graph'] unfulfilled=['codegraph', 'the code graph'] nudge=814 chars |
| `planact_clears_called_run` | PASS | satisfied=['codegraph', 'the code graph'] unfulfilled=[] |
| `derived_family_uses_server_id` | PASS | families=['codegraph']; advertised tool 'mcp__codegraph__find_callers' satisfies it |
| `non_codegraph_catalog_renders_no_block` | PASS | detect -> 0 servers, policy -> 0 chars, derived families -> 0 |
| `fixture_shape` | PASS | 7 python files, 1 markdown, edges=[('app', 'routes'), ('app', 'service'), ('repository', 'config'), ('repository', 'models'), ('routes', 'models'), ('routes', 'service'), ('service', 'config'), ('service', 'models'), ('service', 'repository')] |

## Scripted prompts

| id | prompt |
| --- | --- |
| `callers-of-order-summary` | Who calls order_summary in this repo? I will query the code graph for its callers first, then read the exact source. |
| `impact-of-discount-change` | I am changing apply_discount. Let me run change impact analysis on the code graph to see the blast radius before editing. |
| `route-to-store-path` | Trace the call graph from post_checkout down to the store: I will use the graph tool to list callees at each hop. |
| `symbol-lookup` | Where is OrderRepository.find_by_customer defined? I will look it up with a symbol query on the code index rather than grepping. |
| `module-dependencies` | What depends on config.py? I will ask the code graph for its dependents and the module dependency map. |
| `coverage-check` | Before trusting an empty result I will check index coverage with the status tool, then query the code graph for the entry points. |
| `data-flow` | Follow the data flow from get_customer_orders into the repository; I will use the code graph call hierarchy first. |

## Live mode

`python3 scripts/eval_codegraph_invocation.py --live` runs the same prompts against a real session on the fixture, but only when `CLAUDIO_LIVE_EVAL=1` and a backend URL + token are configured; otherwise it writes `{"skipped": true, "reason": ...}` to `reports/codegraph-eval-live.json`. See `docs/gortex-decoupling-review.md` (Close-out) for the exact variables.
