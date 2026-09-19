# Bundled MCP tools

This directory contains the MCP client, FastMCP server, tool implementations,
and JSON schemas previously supplied as `toolcua_mcp_swap/mcp`. No separate
download is needed. See [NOTICE](NOTICE) for provenance and attribution.

```text
mcp/
├── osworld_mcp_client.py
└── mcp_server/
    ├── server.py
    ├── launch_server.sh
    └── tools/
        ├── apis/       # tool schemas
        └── package/    # application tool implementations
```

## Host configuration

`DesktopEnv` defaults to this checkout. An optional override must point to
the directory **containing** `mcp/`, because the injector appends that name:

```bash
export OSWORLD_REPO=/work/OSWorld-main-nex
export MCP_SRC_ROOT="${OSWORLD_REPO}"
```

Set overrides before importing `DesktopEnv`. On Ray, the same checkout path
must exist on every worker. Older private environment files pointing to
`toolcua_mcp_swap` must be updated to use the bundled source.

## Guest image requirements

The training host and the desktop guest are separate Python environments.
Installing `qwen-agent` on the host provides HybridAgentLocal's GUI tool
definitions; it does not install the guest MCP service.

Prepare a Linux desktop image with:

- The OSWorld HTTP server on port 5000, supporting screenshots, command
  execution and file upload, and a usable desktop for its service user.
- Guest `python3` with FastMCP (`FastMCP`, `Client`, and
  `fastmcp.tools.tool.Tool`), the `mcp` SDK, Pillow, requests and pyautogui.
- LibreOffice with UNO Python bindings (`uno`, `com.sun.star`) accessible to
  that same interpreter. The office tools connect to UNO on port 2002.
- Applications used by the selected tasks, such as Chrome/Chromium, VS Code,
  VLC and LibreOffice. Browser tools additionally need Playwright; optional
  image tools use onnxruntime/rembg. Missing optional modules can reduce the
  available tool set.

Use the OSWorld desktop image build process for these system/application
dependencies. This source bundle is not an image installer or a version lock
for guest dependencies. Validate the chosen image with the smoke checks.

The injector archives this directory and transfers it through the OSWorld
execute API, unpacking the client into `/home/user/osworld_mcp_client.py` and
the server into `/home/user/mcp_server/`. It starts the server with guest
`python3`; logs go to `/tmp/mcp_server.log`. The client calls
`http://localhost:9292/mcp` inside the guest. No external port 9292 endpoint
is required for this path.

Injection skips populated guest copies and an already running service may
be reused. Updating host files alone therefore does not guarantee a guest
upgrade: rebuild/update the template copies, or use an image with missing or
empty MCP files so the runtime injects this bundle.

## Verify on Nex

After setting the Nex API URL, key, workspace and template as described in
the [main README](../README.md), run from the OSWorld checkout:

```bash
python nex_task_smoke.py
python nex_mcp_smoke.py
```

These commands create real sandboxes. Check desktop setup, observation and
evaluation first, then a nonempty tool list and successful MCP call. MCP smoke
returns a nonzero exit status if either the tool list or call fails.

For a different provider, use its desktop lifecycle checks and verify the
same guest functionality before connecting Relax. Provider configuration and
extension points are described in the main README.
