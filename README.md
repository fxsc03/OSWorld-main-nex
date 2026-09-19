<p align="center">
  <img src="https://huggingface.co/datasets/xlangai/assets/resolve/main/github_banner_v2.png" alt="Banner">
</p>

<p align="center">
  <a href="https://os-world.github.io/">Website</a> •
  <a href="https://arxiv.org/abs/2404.07972">Paper</a> •
  <a href="https://timothyxxx.github.io/OSWorld/">Doc</a> •
  <a href="https://github.com/xlang-ai/OSWorld/tree/main/evaluation_examples">Data</a> •
  <a href="https://os-world.github.io/explorer.html">Data Viewer</a> •
  <a href="https://discord.gg/4Gnw7eTEZR">Discord</a> •
  <a href="https://drive.google.com/file/d/1XlEy49otYDyBlA3O9NbR0BpPfr2TXgaD/view?usp=drive_link">Cache</a>
</p>

<p align="center">
    <a href="https://img.shields.io/badge/PRs-Welcome-red">
        <img src="https://img.shields.io/badge/PRs-Welcome-red">
    </a>
    <a href="https://img.shields.io/github/last-commit/xlang-ai/OSWorld?color=green">
        <img src="https://img.shields.io/github/last-commit/xlang-ai/OSWorld?color=green">
    </a>
    <a href="https://opensource.org/licenses/Apache-2.0">
        <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg">
    </a>
    <a href="https://badge.fury.io/py/desktop-env">
        <img src="https://badge.fury.io/py/desktop-env.svg">
    </a>
    <a href="https://pepy.tech/project/desktop-env">
        <img src="https://static.pepy.tech/badge/desktop-env">
    </a>
    <br/>
</p>


## 📢 Updates
- 2025-07-28: Introducing **OSWorld-Verified**! We have made major updates, fixed several issues reported by the community, with more support for AWS (can reduce evaluation time to within 1 hour through parallelization!), and making the benchmark signals more effective. Check out more in the [report](https://xlang.ai/blog/osworld-verified). We have run new model results in the latest version and updated them on the [official website](https://os-world.github.io/). Please compare your OSWorld results with the new benchmark results when running the latest version.
- 2025-05-01: If you need pre-downloaded files for init state setup, we downloaded for you [here](https://drive.google.com/file/d/1XlEy49otYDyBlA3O9NbR0BpPfr2TXgaD/view?usp=drive_link).
- 2024-10-22: We supported Docker🐳 for hosting virtual machines on virtualized platforms. Check below for detailed instructions!
- 2024-06-15: We refactor the code of environment part to decompose VMware Integration, and start to support other platforms such as VirtualBox, AWS, Azure, etc. Hold tight!
- 2024-04-11: We released our [paper](https://arxiv.org/abs/2404.07972), [environment and benchmark](https://github.com/xlang-ai/OSWorld), and [project page](https://os-world.github.io/). Check it out!

## Nex + Relax integration

This checkout is the Nex-enabled OSWorld fork used by the Relax OSWorld
agentic training recipe. Use this repository instead of the upstream
`xlang-ai/OSWorld` checkout when the training environment must create Nex
sandboxes and expose OSWorld MCP tools to a Relax-managed agent.

The integration has two layers:

```text
Relax agentic rollout
    ├── starts one agent process per session
    ├── serves the model through /v1/chat/completions
    └── consumes evaluator rewards and per-turn trajectory exports
             |
             v
OSWorld-main-nex (this repository)
    ├── creates and releases the Nex sandbox
    ├── resets the OSWorld task and injects MCP files
    ├── exposes screenshots, pyautogui, MCP, and evaluator APIs
    └── runs HybridAgentLocal's prompt and action parsing
```

The only code that changes the LLM destination is Relax's adapter
`examples/osworld_agentic/app/hybrid_agent.py`. It subclasses this fork's
`mm_agents.hybrid_agent_local.HybridAgentLocal` and sends each request to
Relax's OpenAI-compatible endpoint. This repository remains responsible for
the desktop environment and the agent's GUI/MCP action protocol; it does not
contain Relax's trainer.

### Sandbox platform support

The supplied Relax recipe defaults to **Nex**. The model runs through Relax;
Nex provides the desktop sandbox. The request flow is:

```text
Relax session -> OSWorldEnv -> DesktopEnv -> Nex provider -> Nex REST API
                                  |                         (create/renew/delete)
                                  +-> guest OSWorld HTTP server (:5000)
                                         +-> desktop / evaluator
                                         +-> MCP client -> FastMCP (:9292)
```

`NEX_API_BASE` is the platform control-plane base URL including `/api/v1`.
The client authenticates with `x-api-key`, creates an instance through
`POST /workspaces/{workspace}/sandbox/instances`, discovers each guest port
through `GET .../instances/{id}/endpoint?port=N`, renews the instance through
`POST .../instances/{id}/renew`, and deletes it on normal close. A Nex reset
recreates the instance from `NEX_TEMPLATE` (or `NEX_TEMPLATE_ID`);
`snapshot_name` does not select the Nex template.

The provider forwards guest ports 5000 (OSWorld), 9222 (Chromium CDP),
8006 (VNC), and 8080 (VLC) through local proxies. MCP calls run inside the
guest through the OSWorld execute API, so port 9292 does not need another
public Nex endpoint. The guest image must already contain the desktop,
OSWorld HTTP server, applications, and MCP dependencies; see [MCP setup](mcp/README.md).

Other platforms can use the same Relax model and training integration:

| Platform | Required work |
| --- | --- |
| Nex | Configure API URL, key, workspace, template, networking, and quota; run the supplied smoke checks. |
| Existing OSWorld providers: `vmware`, `virtualbox`, `docker`, `aws`, `azure`, `aliyun`, `volcengine` | Prepare the provider's VM/image, credentials and networking; set `provider_name` and `snapshot_name` in Relax's `examples/osworld_agentic/app/osworld_config.yaml`; validate its desktop and MCP behavior. |
| A new sandbox API | Implement `Provider` and `VMManager` under `desktop_env/providers/<name>/`, register them in `desktop_env/providers/__init__.py`, and add the name to the appropriate clean/dirty provider set in `DesktopEnv`. |

For a new provider, implement creation/allocation, readiness and reachable guest
endpoints, reset, shutdown, and any TTL renewal required by that platform.
Follow the `Provider`/`VMManager` interfaces in `desktop_env/providers/base.py`.
The guest must support OSWorld screenshots, command execution, file upload,
task setup and evaluation, plus the MCP runtime for hybrid actions.

Relax already reads `provider_name` from YAML. Its current wrapper does not
forward provider-specific options such as `region`, `path_to_vm`, or
`client_password`. If the selected provider needs these, extend the config
flow through `app/agent.py` and `app/env_osworld.py` to `DesktopEnv` in Relax.
Changing `NEX_API_BASE` alone only works for a service implementing the same
Nex API contract. Other providers are not validated by the Nex smoke scripts;
check create/reset/screenshot/step/evaluate/close, then MCP list/call, then a
small Relax run before scaling concurrency.

For GUI-only use, set `OSWORLD_DISABLE_MCP=1`. In the supplied Relax training
script, edit the runtime environment block to set that flag to `1` and
`OSWORLD_FORCE_MCP` to `0`, and keep `env.local.sh` consistent. That block
currently fixes them to `0` and `1`, so a shell export alone is insufficient.

### Which OSWorld repository to use

Clone the Nex fork, not the upstream repository:

```bash
git clone https://github.com/fxsc03/OSWorld-main-nex.git
cd OSWorld-main-nex
git checkout main
```

The checkout must contain all of the following paths:

```text
desktop_env/providers/nex/
desktop_env/providers/nex/client.py
desktop_env/providers/nex/provider.py
mm_agents/hybrid_agent_local.py
agents/tool_retriever.py
prompts/policy_hybrid.py
tools/tools_registry.json
mcp/osworld_mcp_client.py
mcp/mcp_server/server.py
mcp/README.md
nex_env_check.py
nex_task_smoke.py
nex_mcp_smoke.py
```

Do not replace this checkout with a PyPI `desktop-env` package or a plain
upstream OSWorld clone. Those versions may not have the Nex provider, MCP
injection, or the `HybridAgentLocal` implementation expected by Relax.
The `agents/`, `prompts/`, and `tools/` directories are included in this
fork; users do not need to copy those directories from a separate agent
repository.

### End-to-end clone and environment setup

The runnable training setup uses this Nex OSWorld fork together with the
Relax branch that contains `examples/osworld_agentic/`. Replace
`<RELAX_REPO_URL>` with the Relax Git URL available to your organization:

```bash
mkdir -p /work
cd /work

git clone https://github.com/fxsc03/OSWorld-main-nex.git OSWorld-main-nex
git clone <RELAX_REPO_URL> Relax
cd Relax
git checkout feat/fengxiaoshi/osworld
cd /work
```

The MCP source is included in this repository under `mcp/`; no separate
`toolcua_mcp_swap` download is required. The training machine also needs the
Qwen3-VL checkpoint, Megatron-LM source, and an existing Ray cluster.

First prepare the GPU training environment using the cloned Relax repository's
`docs/en/guide/installation.md` (or `docs/zh/guide/installation.md`). PyTorch,
Megatron, SGLang and their CUDA dependencies must already be compatible.
In that environment, install the application dependencies on every Ray node:

```bash
python -m pip install -r /work/Relax/requirements.txt
python -m pip install -r /work/OSWorld-main-nex/requirements.txt qwen-agent
```

The managed agent uses the Ray worker's `python`. Activating a venv only on
the submission machine does not change an already running remote worker's
interpreter. These pip commands alone do not build the GPU training stack.

Check the two repository imports before using Nex:

```bash
cd /work/OSWorld-main-nex
python -c "from mm_agents.hybrid_agent_local import HybridAgentLocal; print('HybridAgentLocal OK')"
python nex_env_check.py
```

Configure the Nex and shared paths in the shell that will submit the Ray job:

```bash
export OSWORLD_REPO=/work/OSWorld-main-nex
export MCP_SRC_ROOT=/work/OSWorld-main-nex
export NEX_API_BASE="http://<your-nex-api-host>/api/v1"
export NEX_API_KEY="ak-<your-key>"
export NEX_WORKSPACE_ID="workspace-<your-workspace>"
export NEX_TEMPLATE="osworld-guest"
```

Run the Nex lifecycle checks before a training job. The task and MCP smoke
tests create real sandboxes and consume Nex quota:

```bash
cd /work/OSWorld-main-nex
python nex_task_smoke.py \
    --task evaluation_examples/examples/libreoffice_calc/1954cced-e748-45c4-9c26-9855b97fbc5e.json
python nex_mcp_smoke.py \
    --task evaluation_examples/examples/libreoffice_calc/1954cced-e748-45c4-9c26-9855b97fbc5e.json
```

After these checks pass, continue with the Relax recipe. Its environment file
must point `OSWORLD_REPO` and `MCP_SRC_ROOT` to `/work/OSWorld-main-nex`, where
the bundled `mcp/` directory is located, and `MODEL_DIR` to the Qwen3-VL checkpoint. The final command
is run from the Relax checkout:

```bash
cd /work/Relax
printf '%s\n' '/examples/osworld_agentic/env.local.sh' >> "$(git rev-parse --git-path info/exclude)"
cp -n examples/osworld_agentic/env.sh examples/osworld_agentic/env.local.sh
# Edit env.local.sh: MEGATRON, MODEL_DIR, DATA_DIR, SAVE_DIR, RAY_JOB_ADDRESS,
# OSWORLD_REPO, MCP_SRC_ROOT, NEX_API_BASE, NEX_API_KEY, NEX_WORKSPACE_ID,
# and NEX_TEMPLATE.
source examples/osworld_agentic/env.local.sh
source examples/osworld_agentic/env.sh
python examples/osworld_agentic/scripts/prepare_data.py \
    --input-dir "${OSWORLD_REPO}/evaluation_examples" \
    --output-dir "${DATA_DIR}/osworld" \
    --train-manifest examples/osworld_agentic/scripts/train_tasks.txt \
    --eval-manifest examples/osworld_agentic/scripts/train_tasks.txt
bash examples/osworld_agentic/run_qwen3vl_8B_osworld_outcome.sh
```

Keep the API key in the ignored `env.local.sh` or another private credential
file. Do not put it in this README, a committed `.env`, parquet data, or the
Ray runtime environment JSON.

### Nex credentials and API settings

The Nex client is centralized in
`desktop_env/providers/nex/client.py`. It uses the Nex REST API with an
`x-api-key` header; it does not use a Bearer token. Configure these variables
in the shell that starts OSWorld or in a private `.env`/`env.local.sh` file:

| Variable | Required | Meaning |
| --- | --- | --- |
| `NEX_API_KEY` | Yes | Your Nex API key, normally in the `ak-...` form. |
| `NEX_TEMPLATE` | Yes unless `NEX_TEMPLATE_ID` is set | Name of the Nex sandbox template, for example `osworld-guest`. |
| `NEX_TEMPLATE_ID` | Alternative | Template ID; takes precedence over `NEX_TEMPLATE`. |
| `NEX_WORKSPACE_ID` | Recommended | Workspace that owns the template and sandbox quota. If omitted, the client resolves it from `/api-keys/current`. |
| `NEX_API_BASE` | Usually no | Nex API base URL, including `/api/v1`. The code default is `http://nex.devops.xiaohongshu.com/api/v1`; use the address provided by your Nex deployment if it differs. |
| `NEX_SANDBOX_TIMEOUT` | No | Sandbox TTL in seconds. Default: `3600`; the provider renews active sandboxes. |
| `NEX_EGRESS_ACTION` | No | Sandbox egress policy, default `allow`. Set it according to your workspace policy. |

Example configuration with placeholders:

```bash
export NEX_API_BASE="http://<your-nex-api-host>/api/v1"
export NEX_API_KEY="ak-<your-key>"
export NEX_WORKSPACE_ID="workspace-<your-workspace>"
export NEX_TEMPLATE="osworld-guest"
export NEX_SANDBOX_TIMEOUT=3600
```

The value of `NEX_API_BASE` is the control-plane URL used for workspace,
template, create, renew, and delete requests. It is not the guest desktop
URL. Guest endpoints are discovered after sandbox creation, one per guest
port, by the provider. Never put a real API key in this README, a committed
`.env` file, a parquet file, or a Ray runtime environment JSON.

The workspace must have permission to use the selected template and enough
quota for the requested number of concurrent sandboxes. A training run with
`ROLLOUT_BATCH_SIZE=12` and `N_SAMPLES_PER_PROMPT=8` can request many sessions;
the Nex quota, rather than GPU count, is often the first concurrency limit.
Account for all sessions per group when sizing quota (the default round has
12 × 8 = 96 sessions). Relax's `OSWORLD_MAX_CONCURRENT_GROUPS` limits local
task groups via host-local locks; it is not a cluster-wide sandbox quota.
Adjust that value in the training script's runtime environment block together
with batch size and samples per prompt.

### Python environment and MCP source

For standalone OSWorld checks, a dedicated Python 3.12 environment can be
used as below. For Relax training, install these dependencies in the prepared
Ray worker training environment described above. The dependency file
contains important NumPy 1.x compatibility pins for OpenCV, Gymnasium, and
the evaluator stack:

```bash
cd /path/to/OSWorld-main-nex
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
# Required by mm_agents/agent_function_call.py (used by HybridAgentLocal).
python -m pip install qwen-agent
```

The environment check should be clean before starting a sandbox:

```bash
python nex_env_check.py
```

In particular, do not let pip upgrade NumPy to 2.x while using the pinned
OpenCV/evaluator wheels. An `_ARRAY_API not found` or `numpy.core.multiarray`
error means the environment is inconsistent; reinstall the requirements in a
fresh environment before debugging Nex.

Verify the HybridAgentLocal import before starting Relax:

```bash
python -c "from mm_agents.hybrid_agent_local import HybridAgentLocal; from agents.tool_retriever import ToolRetriever; print('HybridAgentLocal dependencies OK')"
```

`HybridAgentLocal` has three kinds of dependencies. The Python packages come
from `requirements.txt` plus `qwen-agent`; the prompt, BM25 retriever, tool
registry, and MCP server source are shipped in this repository. The MCP source
is injected when the guest copies are missing or empty. Existing populated
guest files may be reused; updating this checkout does not force an already
populated image to refresh its MCP files.

MCP is loaded from the OSWorld checkout by default. `MCP_SRC_ROOT` may still
be overridden when testing a compatible alternate MCP implementation:

```text
/work/
├── OSWorld-main-nex/      # contains mcp/
└── Relax/
```

```bash
export OSWORLD_REPO=/work/OSWorld-main-nex
export MCP_SRC_ROOT=/work/OSWorld-main-nex
```

`MCP_SRC_ROOT` points to the repository root containing `mcp/`, not to
`mcp/` itself; the injector appends `/mcp`. Existing `env.local.sh` overrides
must also be updated. See [MCP setup and guest dependencies](mcp/README.md).

`MCP_SRC_ROOT` must be visible to every process that imports `DesktopEnv`,
including Ray workers and the managed agent subprocess. The Relax launcher
passes `OSWORLD_REPO` and `MCP_SRC_ROOT` into the Ray runtime environment.
For GUI-only sessions, follow the flag settings in "Sandbox platform support"
above, including the fixed runtime environment values in the Relax script.

### Verify Nex before connecting Relax

Run the checks in this order. These commands create real Nex sandboxes and
may consume workspace quota.

```bash
cd /work/OSWorld-main-nex
# Use the prepared Python environment.
export NEX_API_BASE="http://<your-nex-api-host>/api/v1"
export NEX_API_KEY="ak-<your-key>"
export NEX_WORKSPACE_ID="workspace-<your-workspace>"
export NEX_TEMPLATE="osworld-guest"
export MCP_SRC_ROOT=/work/OSWorld-main-nex

# 1. Import and dependency check; no sandbox is created.
python nex_env_check.py

# 2. DesktopEnv lifecycle: create, setup, screenshot, evaluate, close.
python nex_task_smoke.py \
    --task evaluation_examples/examples/libreoffice_calc/1954cced-e748-45c4-9c26-9855b97fbc5e.json

# 3. MCP lifecycle: inject the server, list tools, call one read-only tool.
python nex_mcp_smoke.py \
    --task evaluation_examples/examples/libreoffice_calc/1954cced-e748-45c4-9c26-9855b97fbc5e.json
```

The first smoke test should report that setup, observation, and evaluator
are working. A score of `0` is expected because the smoke test does not solve
the task. The MCP smoke test should report a non-empty tool list and a
successful read-only tool call. If either smoke test fails, fix the Nex,
template, quota, dependency, or MCP configuration before starting Relax.

### Relax-side configuration

The Relax repository contains the training recipe under
`examples/osworld_agentic/`. Copy its environment template to a private file
and fill in both repository paths:

```bash
cd /work/Relax
printf '%s\n' '/examples/osworld_agentic/env.local.sh' >> "$(git rev-parse --git-path info/exclude)"
cp -n examples/osworld_agentic/env.sh examples/osworld_agentic/env.local.sh
chmod 600 examples/osworld_agentic/env.local.sh
```

At minimum, set:

```bash
export MEGATRON=/work/Megatron-LM
export MODEL_DIR=/work/models
export DATA_DIR=/work/rl_data
export SAVE_DIR=/work/checkpoints/osworld_agentic_run1
export RAY_JOB_ADDRESS=http://<ray-head>:8265

export OSWORLD_REPO=/work/OSWorld-main-nex
export OSWORLD_CACHE_DIR=/work/OSWorld-main-nex/cache
export MCP_SRC_ROOT=/work/OSWorld-main-nex

export NEX_API_BASE="http://<your-nex-api-host>/api/v1"
export NEX_API_KEY="ak-<your-key>"
export NEX_WORKSPACE_ID="workspace-<your-workspace>"
export NEX_TEMPLATE="osworld-guest"
```

The Relax recipe starts `examples/osworld_agentic/run_agent_app.sh` once per
session. That launcher sets `PYTHONPATH` to the Relax checkout and this
OSWorld checkout, then starts `app.agent`. The agent creates one
`DesktopEnv(provider_name="nex")`, runs the task, and writes a session result
that Relax consumes.

The model request path is:

```text
HybridAgentLocal.call_llm()
    -> POST ${RELAX_BASE_URL}/v1/chat/completions
    -> Relax AgenticChatAPIService
    -> SGLang rollout engine
```

`RELAX_BASE_URL` and `RELAX_SESSION_ID` are injected by Relax. Users should
not point `OPENAI_BASE_URL` at the Nex API. Nex is the desktop environment;
Relax is the model-serving endpoint for the training session.

Prepare the OSWorld task parquet and start the 8-GPU example from the Relax
repository:

```bash
cd /work/Relax
source examples/osworld_agentic/env.local.sh
source examples/osworld_agentic/env.sh

python examples/osworld_agentic/scripts/prepare_data.py \
    --input-dir "${OSWORLD_REPO}/evaluation_examples" \
    --output-dir "${DATA_DIR}/osworld" \
    --train-manifest examples/osworld_agentic/scripts/train_tasks.txt \
    --eval-manifest examples/osworld_agentic/scripts/train_tasks.txt

bash examples/osworld_agentic/run_qwen3vl_8B_osworld_outcome.sh
```

The training recipe expects the model at
`${MODEL_DIR}/Qwen3-VL-8B-Thinking/`, a shared filesystem for Relax,
Megatron, OSWorld, MCP, data, and model files, and a Ray cluster whose
workers can import both repositories.

### What this fork changed for Relax

The Nex fork provides the environment contract that the Relax adapter needs:

1. **Nex REST client** (`desktop_env/providers/nex/client.py`)
   - Uses the Nex `x-api-key` authentication header.
   - Resolves a workspace and template, creates sandboxes, discovers guest
     endpoints, renews active instances, and deletes them.
   - Retries quota-related creates with backoff, waits for asynchronous deletes
     to release quota, and serializes concurrent creates with a file lock.

2. **OSWorld Nex provider** (`desktop_env/providers/nex/provider.py`)
   - Implements the normal OSWorld provider lifecycle on top of a Nex sandbox.
   - Maps the guest server, Chromium CDP, VNC, and VLC ports through local
     proxies so existing OSWorld controllers can keep using local endpoints.
   - Treats the Nex template as the initial snapshot and recreates a sandbox
     when OSWorld requests a reset.
   - Renews the sandbox while a task is active and cleans up the proxies and
     instance on close.

3. **DesktopEnv Nex/MCP path** (`desktop_env/desktop_env.py`)
   - Recognizes `provider_name="nex"` as a clean, remote environment.
   - Bundles MCP client/server source under `mcp/` and defaults to this
     checkout for injection. Missing/empty guest copies are populated before
     tool discovery; the guest must supply the runtime dependencies.
   - Returns the same screenshot, application state, tool list, `step`,
     `call_mcp_tool`, `evaluate`, and `close` surface used by the Relax adapter.
   - Adds Nex startup settling and desktop-error-window handling needed for a
     reliable first observation.

4. **Hybrid GUI + MCP agent** (`mm_agents/hybrid_agent_local.py`)
   - Keeps the existing NousFnCall protocol for GUI and MCP actions.
   - Ships the prompt builder, BM25 tool retriever, and tool registry in the
     same OSWorld checkout, so the Relax launcher does not depend on an
     untracked sibling `agents/`, `prompts/`, or `tools/` directory.
   - Preserves parsed action information in trajectory history when a model
     response has no explicit conclusion, so later turns can see the action
     that was actually executed.
   - Keeps the tool retriever and prompt construction inside the OSWorld
     process; Relax only supplies the model endpoint and consumes the export.

The fork deliberately does not put Ray, Relax argument parsing, reward
shaping, or checkpoint logic into OSWorld. Those responsibilities stay in
Relax's `examples/osworld_agentic` adapter and training entrypoint. This
separation lets users upgrade the training recipe without turning the Nex
provider into a Relax-specific package.

### Common failure modes

- **`NEX_API_KEY` or template errors**: verify the key, workspace, template
  name, and that the key has sandbox permissions. `NEX_API_KEY` is sent as
  `x-api-key`, not `Authorization: Bearer ...`.
- **HTTP 422 quota errors**: lower concurrent task groups or request more Nex
  quota. Do not increase Ray/GPU concurrency first; Nex sandbox quota is
  independent of GPU capacity.
- **Blank first screenshot**: wait for `nex_task_smoke.py` to finish its
  desktop readiness check and verify the selected template has a working
  Ubuntu desktop.
- **MCP tool list is empty**: check that `MCP_SRC_ROOT` contains the bundled
  `mcp/` directory, that the path is visible inside the worker process, and
  rerun `nex_mcp_smoke.py`. Set `MCP_SRC_ROOT` only when using a compatible
  alternate implementation.
- **`ModuleNotFoundError: qwen_agent`**: activate the OSWorld virtual
  environment and run `python -m pip install qwen-agent`, then rerun the
  HybridAgentLocal import check above.
- **`tools_registry.json not found`**: use a current clone of this Nex fork;
  the registry is at `tools/tools_registry.json` and no external copy is
  required.
- **`_ARRAY_API not found` or `numpy.core.multiarray`**: the environment has
  NumPy 2 with NumPy 1-built OpenCV/evaluator wheels. Recreate the venv and
  install this checkout's `requirements.txt`.
- **Relax session gets 404/422 from the model endpoint**: check the Relax
  adapter's `/v1` suffix and do not substitute `NEX_API_BASE` for
  `RELAX_BASE_URL`.

## 💾 Standard OSWorld installation (non-Relax)

The following upstream-style installation section is for ordinary OSWorld
evaluation. For Nex + Relax training, follow [Nex + Relax integration](#nex--relax-integration)
above so that the Nex fork, MCP source, and Relax adapter are configured
together.

### VMware/VirtualBox (Desktop, Laptop, Bare Metal Machine)
Suppose you are operating on a system that has not been virtualized (e.g. your desktop, laptop, bare metal machine), meaning you are not utilizing a virtualized environment like AWS, Azure, or k8s.
If this is the case, proceed with the instructions below. However, if you are on a virtualized platform, please refer to the [Docker](https://github.com/xlang-ai/OSWorld?tab=readme-ov-file#docker-server-with-kvm-support-for-the-better) section.

1. First, clone this repository and `cd` into it. Then, install the dependencies listed in `requirements.txt`. It is recommended that you use the latest version of Conda to manage the environment, but you can also choose to manually install the dependencies. Please ensure that the version of Python is >= 3.10.
```bash
# Clone the Nex-enabled OSWorld repository
git clone https://github.com/fxsc03/OSWorld-main-nex.git OSWorld-main-nex

# Change directory into the cloned repository
cd OSWorld-main-nex

# Optional: Create a Conda environment for OSWorld
# conda create -n osworld python=3.10
# conda activate osworld

# Install required dependencies
pip install -r requirements.txt
```

Alternatively, you can install the environment without any benchmark tasks:
```bash
pip install desktop-env
```

2. Install [VMware Workstation Pro](https://www.vmware.com/products/workstation-pro/workstation-pro-evaluation.html) (for systems with Apple Chips, you should install [VMware Fusion](https://support.broadcom.com/group/ecx/productdownloads?subfamily=VMware+Fusion)) and configure the `vmrun` command.  The installation process can refer to [How to install VMware Workstation Pro](desktop_env/providers/vmware/INSTALL_VMWARE.md). Verify the successful installation by running the following:
```bash
vmrun -T ws list
```
If the installation along with the environment variable set is successful, you will see the message showing the current running virtual machines.
> **Note:** We also support using [VirtualBox](https://www.virtualbox.org/) if you have issues with VMware Pro. However, features such as parallelism and macOS on Apple chips might not be well-supported.

All set! Our setup script will automatically download the necessary virtual machines and configure the environment for you.

### Docker (Server with KVM Support for Better Performance)
If you are running on a non-bare metal server, or prefer not to use VMware and VirtualBox platforms, we recommend using our Docker support.

#### Prerequisite: Check if your machine supports KVM
We recommend running the VM with KVM support. To check if your hosting platform supports KVM, run
```
egrep -c '(vmx|svm)' /proc/cpuinfo
```
on Linux. If the return value is greater than zero, the processor should be able to support KVM.
> **Note**: macOS hosts generally do not support KVM. You are advised to use VMware if you would like to run OSWorld on macOS.

#### Install Docker
If your hosting platform supports a graphical user interface (GUI), you may refer to [Install Docker Desktop on Linux](https://docs.docker.com/desktop/install/linux/) or [Install Docker Desktop on Windows](https://docs.docker.com/desktop/install/windows-install/) based on your OS. Otherwise, you may [Install Docker Engine](https://docs.docker.com/engine/install/).

#### Running Experiments
Add the following arguments when initializing `DesktopEnv`: 
- `provider_name`: `docker`
- `os_type`: `Ubuntu` or `Windows`, depending on the OS of the VM
> **Note**: If the experiment is interrupted abnormally (e.g., by interrupting signals), there may be residual docker containers which could affect system performance over time. Please run `docker stop $(docker ps -q) && docker rm $(docker ps -a -q)` to clean up.

### AWS
Using cloud services for parallel evaluation can significantly accelerate evaluation efficiency (can reduce evaluation time to within 1 hour through parallelization!) and can even be used as infrastructure for training. 
We provide comprehensive AWS support with a Host-Client architecture that enables large-scale parallel evaluation of OSWorld tasks. 
For detailed setup instructions, see [Public Evaluation Guideline](https://github.com/xlang-ai/OSWorld/blob/main/PUBLIC_EVALUATION_GUIDELINE.md) and [AWS Configuration Guide](https://github.com/xlang-ai/OSWorld/blob/main/desktop_env/providers/aws/AWS_GUIDELINE.md). 

### Others
We are working on supporting more 👷. Please hold tight!


## 🚀 Quick Start
Run the following minimal example to interact with the environment:

```bash
# Basic usage with default settings
python quickstart.py

# Customize provider and VM path
python quickstart.py --provider_name vmware --path_to_vm "path/to/your/vm.vmx"
```

You will see all the logs of the system running normally, including the successful creation of the environment, completion of setup, and successful execution of actions. In the end, you will observe a successful right-click on the screen, which means you are ready to go.

## 🧪 Experiments
### Agent Baselines

> **⚠️ Important Configuration Requirements:**
> 
> * **Google Account Tasks**: Some tasks require Google account access and OAuth2.0 configuration. Please refer to [Google Account Guideline](ACCOUNT_GUIDELINE.md) for detailed setup instructions.
> * **Proxy Configuration**: Some tasks may require proxy settings to function properly (this depends on the strength of website defenses against your network location). Please refer to your system's proxy configuration documentation.
> * **Impact of Missing Configuration**: If these configurations are not properly set up, the corresponding tasks will fail to execute correctly, leading to lower evaluation scores.


If you wish to run the baseline agent used in our paper, you can execute the following command as an example under the GPT-4o pure-screenshot setting:

Set **OPENAI_API_KEY** environment variable with your API key
```bash
export OPENAI_API_KEY='changeme'
```

Optionally, set **OPENAI_BASE_URL** to use a custom OpenAI-compatible API endpoint
```bash
export OPENAI_BASE_URL='http://your-custom-endpoint.com/v1'  # Optional: defaults to https://api.openai.com
```

Single-threaded execution (deprecated, using `vmware` provider as example)
```bash
python run.py \
    --provider_name vmware \
    --path_to_vm Ubuntu/Ubuntu.vmx \
    --headless \
    --observation_type screenshot \
    --model gpt-4o \
    --sleep_after_execution 3 \
    --max_steps 15 \
    --result_dir ./results \
    --client_password password
```

Parallel execution (example showing switching provider to `docker`)
```bash
python run_multienv.py \
    --provider_name docker \
    --headless \
    --observation_type screenshot \
    --model gpt-4o \
    --sleep_after_execution 3 \
    --max_steps 15 \
    --num_envs 10 \
    --client_password password
```

The results, which include screenshots, actions, and video recordings of the agent's task completion, will be saved in the `./results` (or other `result_dir` you specified) directory in this case. 
You can then run the following command to obtain the result:
```bash
python show_result.py
```

## Evaluation
### Local Evaluation
Please start by reading through the [agent interface](https://github.com/xlang-ai/OSWorld/blob/main/mm_agents/README.md) and the [environment interface](https://github.com/xlang-ai/OSWorld/blob/main/desktop_env/README.md).
Correctly implement the agent interface and import your customized version in the `run.py` or `run_multienv.py` file.
Afterward, you can execute a command similar to the one in the previous section to run the benchmark on your agent.

### Public Evaluation
If you want your results to be verified and displayed on the verified leaderboard, you need to schedule a meeting with us (current maintainer: tianbaoxiexxx@gmail.com, yuanmengqi732@gmail.com) to run your agent code on our side and have us report the results. 
You need to upload and allow us to disclose your agent implementation under the OSWorld framework (you may choose not to expose your model API to the public), along with a report that allows the public to understand what's happening behind the scenes.
Alternatively, if you are from a trusted institution, you can share your monitoring data and trajectories with us. 
Please carefully follow the [Public Evaluation Guideline](https://github.com/xlang-ai/OSWorld/blob/main/PUBLIC_EVALUATION_GUIDELINE.md) to get results.


## ❓ FAQ
### What is the username and password for the virtual machines?
The username and password for the virtual machines are as follows (for provider `vmware`, `virtualbox` and `docker`): we set the account credentials for Ubuntu as `user` / `password`. 
For cloud service providers like `aws`, to prevent attacks due to weak passwords, we default to `osworld-public-evaluation`. 
If you make further modifications, remember to set the client_password variable and pass it to DesktopEnv and Agent (if supported) when running experiments. 
Some features like setting up proxy require the environment to have the client VM password to obtain sudo privileges, and for some OSWorld tasks, the agent needs the password to obtain sudo privileges to complete them.

### How to setup the account and credentials for Google and Google Drive?

See [Account Guideline](ACCOUNT_GUIDELINE.md).

### How can I configure a proxy for the VM (if I'm behind the GFW, or I don't want some of my tasks to be identified as bot and get lower scores)?

If you want to set it up yourself, please refer to [Proxy Guideline](PROXY_GUIDELINE.md).
We also provide a pre-configured solution based on dataimpulse, please refer to [proxy-setup section in PUBLIC_EVALUATION_GUIDELINE](https://github.com/xlang-ai/OSWorld/blob/main/PUBLIC_EVALUATION_GUIDELINE.md#22-proxy-setup).

### Open Source Contributors

Thanks to all the contributors!

<a href="https://github.com/xlang-ai/OSWorld/graphs/contributors">
  <img src="https://stg.contrib.rocks/image?repo=xlang-ai/OSWorld" />
</a>


## 📄 Citation
If you find this environment useful, please consider citing our work:
```
@misc{OSWorld,
      title={OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments}, 
      author={Tianbao Xie and Danyang Zhang and Jixuan Chen and Xiaochuan Li and Siheng Zhao and Ruisheng Cao and Toh Jing Hua and Zhoujun Cheng and Dongchan Shin and Fangyu Lei and Yitao Liu and Yiheng Xu and Shuyan Zhou and Silvio Savarese and Caiming Xiong and Victor Zhong and Tao Yu},
      year={2024},
      eprint={2404.07972},
      archivePrefix={arXiv},
      primaryClass={cs.AI}
}
```

## Acknowledgement for OSWorld-Verified
Special thanks to the following institutions that provided feedback and participated in the fixes (as well as institutions that provided feedback during the process): [MoonShot AI, a.k.a. Kimi](https://www.moonshot.ai/)，[Human Data](https://www.hud.so/), [OpenAI](https://openai.com/), [ByteDance Seed TARS](https://seed-tars.com/), [Anthropic](https://www.anthropic.com/), [Simular](https://www.simular.ai/), [HKU Data Intelligence Lab](https://sites.google.com/view/chaoh)

Special thanks to the following students who participated in the specific fixes: [Mengqi Yuan](https://yuanmengqi.github.io/), [Danyang Zhang](https://zdy023.github.io/), [Xinzhuang Xiong](https://thisisxxz.com/),  [Zhennan Shen](https://scholar.google.com/citations?user=JPwg5MwAAAAJ&hl=en), [Zilong Zhou](https://github.com/adlsdztony), Yanxu Chen, [Jiaqi Deng](https://millank0817.github.io/), [Tianbao Xie](https://tianbaoxie.com/), Junda Chen, [Jixuan Chen](https://chenjix.github.io/), [Haoyuan Wu](https://www.linkedin.com/in/haoyuan-wu-240878291/).

Special thanks to the following students who participated in running the re-evaluation: [Mengqi Yuan](https://yuanmengqi.github.io/), [Zilong Zhou](https://github.com/adlsdztony), [Xinyuan Wang](https://xinyuanwangcs.github.io/), [Bowen Wang](https://bowenbryanwang.github.io/).
