# MCP-Linux
Give an AI full control of a Linux machine through [MCP](https://modelcontextprotocol.io), and expose it to the internet with Cloudflare Tunnel.

Design follows [Remosh](https://github.com/Calou-Code-Platform/remosh): same base image (`ccp-base-debian`), same `run.sh` / flags / `cfd` / auto-startup conventions — but instead of SSH, the container serves an **MCP server** (Streamable HTTP).

> **This container is intentionally unrestricted.** The AI runs as `root`, with no sandbox. With `--privileged` it has the same power as root on the Docker host. Anyone holding your MCP token can do anything. Treat the token like a root password.

## Tools
| Tool | What it does |
| :--- | :--- |
| `exec` | Run a shell command (`bash -c`) and wait. Supports `cwd`, `env`, `stdin`, `timeout`. |
| `job_start` / `job_status` / `job_list` / `job_kill` | Run long-lived commands in the background, poll their logs, stop them. |
| `read_file` | Read a file (line paging; binary → base64). |
| `write_file` | Write / append a file (text or base64, optional `mode`, creates parent dirs). |
| `edit_file` | Exact string replacement in a text file. |
| `list_dir` | List a directory, optionally recursive. |
| `system_info` | OS, CPU, memory, disk, available tools. |

## Environment Variables
| Variable | Description | Default Value |
| :--- | :--- | :--- |
| `token` | MCP access token (sent as `Authorization: Bearer <token>`). If empty, a random one is generated once and printed in `docker logs`. | *random* |
| `cloudflared` | (Optional) Your Cloudflared Tunnel token. | *None* |
| `port` | Port the MCP server listens on inside the container. | `8000` |

## Exposed Ports
* **`8000`** : MCP endpoint at `/mcp`, health check at `/health` (no auth).

## Quick Start
```bash
docker run -d \
  --name=mcp-linux \
  --restart always \
  --privileged \
  -e token=your_long_random_token \
  -e cloudflared=your_cloudflared_tunnel_token \
  -p 8000:8000 \
  -v mcp-flags:/ccp/flags \
  -v mcp-root:/root \
  ghcr.io/chouhouse/calou__mcp-linux:latest
```
Or use the included `docker-compose.yml`.

Cloudflare side: Zero Trust → Networks → Tunnels → create a tunnel, copy its token into `cloudflared`, and add a **Public hostname** pointing to service `http://localhost:8000`. Your MCP URL is then `https://<your-hostname>/mcp`.

## Connecting a client
* **Header auth** (preferred): `Authorization: Bearer <token>`
  ```bash
  claude mcp add --transport http linux https://<your-hostname>/mcp \
    --header "Authorization: Bearer <token>"
  ```
* **URL auth** (for clients that cannot set headers): `https://<your-hostname>/mcp?token=<token>`

Optionally put Cloudflare Access in front of the hostname for a second layer.

## Commands inside the container
* `cfd <token>` : change the Cloudflared tunnel token (tunnel is auto-restarted).
* `mcptoken` : show the MCP token. `mcptoken new` generates a new one, `mcptoken <value>` sets one. Takes effect immediately, no restart.

## Auto-Startup
Any `*.sh` in `/root/startup` is run in a background tmux session every time the container starts.

## Going even further
By default "max permission" means root + `--privileged` inside the container. To let the AI operate the **host** itself, uncomment in `docker-compose.yml`: `pid: host`, `network_mode: host`, and the `/:/host` mount (+ `docker.sock`). Then `chroot /host` gives a root shell on the machine.

## Development / tests
```bash
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt httpx
MCP_TOKEN=test MCP_PORT=8765 MCP_WORKDIR=/tmp/ws MCP_JOB_DIR=/tmp/jobs .venv/bin/python server/server.py &
MCP_URL=http://127.0.0.1:8765/mcp MCP_TOKEN=test .venv/bin/python tests/smoke_test.py
```

Restart the container with `kill 1`.
