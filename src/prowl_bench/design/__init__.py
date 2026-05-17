"""Prowl Design subcommand modules.

`prowl-bench design manifest` — emit a Mycelio manifest from an OpenAPI spec.
`prowl-bench design ui`       — launch the local Postman-for-LLMs web UI.

The local UI is a copy of the hosted version at design.prowl.world,
served from localhost via the stdlib `http.server`. The "Score" button
still calls Prowl's hosted API (where the multi-LLM scoring lives) —
the local server only serves the static UI bundle.
"""
