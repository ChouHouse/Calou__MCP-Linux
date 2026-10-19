"""MCP-Linux: expose a whole Linux box to an MCP client over Streamable HTTP.

The server intentionally has no sandbox. It runs as whatever user starts it
(root inside the container) and every tool acts with that user's full rights.
Access control is a single shared secret (Bearer token), see ``TokenAuth``.
"""

import asyncio
import base64
import hmac
import json
import os
import platform
import secrets
import shutil
import signal
import time
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

PORT = int(os.environ.get("MCP_PORT", "8000"))
TOKEN_FILE = Path(os.environ.get("MCP_TOKEN_FILE", "/ccp/flags/token"))
WORKDIR = os.environ.get("MCP_WORKDIR", "/root/workspace")
SHELL = os.environ.get("MCP_SHELL", "/bin/bash")
MAX_OUTPUT = int(os.environ.get("MCP_MAX_OUTPUT", "100000"))
MAX_READ = int(os.environ.get("MCP_MAX_READ", str(2 * 1024 * 1024)))
JOB_DIR = Path(os.environ.get("MCP_JOB_DIR", "/var/log/mcp-linux/jobs"))

mcp = FastMCP(
    "MCP-Linux",
    instructions=(
        "Full root access to a Linux machine. Use `exec` to run shell commands, "
        "`job_start` for long-running processes, and `read_file` / `write_file` / "
        "`edit_file` for files. There is no sandbox: be careful with destructive commands."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    json_response=True,
)


# --------------------------------------------------------------------------- helpers

def _clip(data: bytes, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    """Decode and keep head + tail when output exceeds ``limit`` characters."""
    text = data.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n...[{omitted} chars omitted]...\n{text[-half:]}", True


def _resolve(path: str) -> Path:
    p = Path(os.path.expanduser(path))
    return p if p.is_absolute() else Path(WORKDIR) / p


def _kill_group(proc: asyncio.subprocess.Process, sig: int = signal.SIGKILL) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


# --------------------------------------------------------------------------- exec

@mcp.tool()
async def exec(
    command: str,
    cwd: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> dict:
    """Run a shell command (bash -c) and wait for it to finish.

    Args:
        command: The shell command line. Pipes, redirects, heredocs all work.
        cwd: Working directory. Defaults to /root/workspace.
        timeout: Seconds before the whole process group is killed. Use job_start for anything longer.
        env: Extra environment variables for this command.
        stdin: Text piped to the command's standard input.
    """
    workdir = str(_resolve(cwd)) if cwd else WORKDIR
    os.makedirs(workdir, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        SHELL, "-c", command,
        cwd=workdir,
        env={**os.environ, **(env or {})},
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    started = time.monotonic()
    timed_out = False
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout
        )
    except asyncio.TimeoutError:
        timed_out = True
        _kill_group(proc)
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        _kill_group(proc)
        raise

    stdout, out_cut = _clip(out)
    stderr, err_cut = _clip(err)
    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "truncated": out_cut or err_cut,
        "duration_s": round(time.monotonic() - started, 3),
        "cwd": workdir,
    }


# --------------------------------------------------------------------------- background jobs

_jobs: dict[str, dict] = {}


@mcp.tool()
async def job_start(command: str, cwd: str | None = None, env: dict[str, str] | None = None) -> dict:
    """Start a long-running command in the background (dev servers, builds, watchers).

    Output goes to a log file; poll it with job_status. Returns a job id.
    """
    workdir = str(_resolve(cwd)) if cwd else WORKDIR
    os.makedirs(workdir, exist_ok=True)
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    job_id = secrets.token_hex(4)
    log_path = JOB_DIR / f"{job_id}.log"
    log = open(log_path, "wb")
    proc = await asyncio.create_subprocess_exec(
        SHELL, "-c", command,
        cwd=workdir,
        env={**os.environ, **(env or {})},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=log,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    _jobs[job_id] = {"proc": proc, "command": command, "cwd": workdir,
                     "log": log_path, "started": time.time()}
    return {"job_id": job_id, "pid": proc.pid, "log_file": str(log_path)}


def _job_info(job_id: str, tail: int | None = None) -> dict:
    job = _jobs[job_id]
    proc = job["proc"]
    info = {
        "job_id": job_id,
        "pid": proc.pid,
        "command": job["command"],
        "running": proc.returncode is None,
        "exit_code": proc.returncode,
        "age_s": round(time.time() - job["started"], 1),
        "log_file": str(job["log"]),
    }
    if tail is not None:
        data = job["log"].read_bytes()
        lines = data.splitlines(keepends=True)[-tail:] if tail > 0 else []
        info["output"], info["truncated"] = _clip(b"".join(lines))
    return info


@mcp.tool()
async def job_status(job_id: str, tail: int = 100) -> dict:
    """Get a background job's state and the last ``tail`` lines of its output."""
    if job_id not in _jobs:
        return {"error": f"unknown job {job_id}"}
    return _job_info(job_id, tail)


@mcp.tool()
async def job_list() -> list[dict]:
    """List all background jobs started since the server booted."""
    return [_job_info(j) for j in _jobs]


@mcp.tool()
async def job_kill(job_id: str, force: bool = False) -> dict:
    """Stop a background job (SIGTERM, or SIGKILL with force=true) including its children."""
    if job_id not in _jobs:
        return {"error": f"unknown job {job_id}"}
    _kill_group(_jobs[job_id]["proc"], signal.SIGKILL if force else signal.SIGTERM)
    await asyncio.sleep(0.2)
    return _job_info(job_id)


# --------------------------------------------------------------------------- files

@mcp.tool()
async def read_file(path: str, offset: int = 0, limit: int | None = None, as_base64: bool = False) -> dict:
    """Read a file. Text files are returned as text with optional line paging
    (``offset`` = first line, 0-based; ``limit`` = number of lines). Binary files, or
    ``as_base64=true``, return base64 of the raw bytes."""
    p = _resolve(path)
    size = p.stat().st_size
    if as_base64:
        raw = p.read_bytes()[:MAX_READ]
        return {"path": str(p), "size": size, "encoding": "base64",
                "content": base64.b64encode(raw).decode(), "truncated": size > MAX_READ}
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": str(p), "size": size, "encoding": "base64",
                "content": base64.b64encode(raw[:MAX_READ]).decode(),
                "truncated": size > MAX_READ,
                "note": "not valid UTF-8, returned as base64"}
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if offset or limit is not None:
        lines = lines[offset: offset + limit if limit is not None else None]
    text = "".join(lines)
    truncated = len(text) > MAX_READ
    return {"path": str(p), "size": size, "encoding": "utf-8", "total_lines": total,
            "content": text[:MAX_READ], "truncated": truncated}


@mcp.tool()
async def write_file(
    path: str,
    content: str,
    append: bool = False,
    encoding: str = "utf-8",
    mode: str | None = None,
    make_dirs: bool = True,
) -> dict:
    """Write (or append to) a file.

    Args:
        content: File content. With encoding="base64" this is base64 of the raw bytes.
        mode: Optional octal permission string such as "755" (e.g. for scripts).
        make_dirs: Create missing parent directories.
    """
    p = _resolve(path)
    if make_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(content) if encoding == "base64" else content.encode("utf-8")
    with open(p, "ab" if append else "wb") as f:
        f.write(data)
    if mode:
        os.chmod(p, int(mode, 8))
    return {"path": str(p), "bytes_written": len(data), "size": p.stat().st_size}


@mcp.tool()
async def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> dict:
    """Replace ``old`` with ``new`` in a text file. ``old`` must match exactly once
    unless replace_all=true."""
    p = _resolve(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        return {"error": "old string not found", "path": str(p)}
    if count > 1 and not replace_all:
        return {"error": f"old string matches {count} times; add context or set replace_all", "path": str(p)}
    p.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1), encoding="utf-8")
    return {"path": str(p), "replacements": count if replace_all else 1}


@mcp.tool()
async def list_dir(path: str = ".", depth: int = 1, show_hidden: bool = True) -> list[dict]:
    """List a directory (``depth`` > 1 recurses). Returns type, size, mode and mtime per entry."""
    root = _resolve(path)
    out: list[dict] = []

    def walk(d: Path, level: int) -> None:
        for e in sorted(d.iterdir(), key=lambda x: x.name):
            if not show_hidden and e.name.startswith("."):
                continue
            try:
                st = e.lstat()
            except OSError:
                continue
            kind = "symlink" if e.is_symlink() else "dir" if e.is_dir() else "file"
            out.append({"path": str(e), "type": kind, "size": st.st_size,
                        "mode": oct(st.st_mode & 0o7777)[2:], "mtime": int(st.st_mtime)})
            if kind == "dir" and level < depth:
                try:
                    walk(e, level + 1)
                except PermissionError:
                    pass
            if len(out) >= 5000:
                return

    walk(root, 1)
    return out


@mcp.tool()
async def system_info() -> dict:
    """Describe the machine: OS, user, CPU/memory, disk, uptime, network addresses."""
    disk = shutil.disk_usage("/")
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines()[:3]:
            k, v = line.split(":")
            mem[k] = v.strip()
    except OSError:
        pass
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "uid": os.getuid(),
        "cwd_default": WORKDIR,
        "cpus": os.cpu_count(),
        "memory": mem,
        "disk_root": {"total": disk.total, "used": disk.used, "free": disk.free},
        "load_avg": os.getloadavg(),
        "python": platform.python_version(),
        "tools": {t: bool(shutil.which(t)) for t in
                  ("git", "curl", "python3", "node", "docker", "tmux", "cloudflared")},
    }


# --------------------------------------------------------------------------- auth + app

def _current_token() -> str:
    env = os.environ.get("MCP_TOKEN", "").strip()
    if env:
        return env
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


class TokenAuth:
    """ASGI middleware: require ``Authorization: Bearer <token>`` or ``?token=<token>``.

    The token is re-read on every request so `mcptoken new` takes effect without a restart.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/health":
            return await JSONResponse({"status": "ok"})(scope, receive, send)

        expected = _current_token()
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        supplied = ""
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not supplied:
            query = scope.get("query_string", b"").decode()
            for part in query.split("&"):
                if part.startswith("token="):
                    supplied = part[6:]
        if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
            resp = JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def main() -> None:
    os.makedirs(WORKDIR, exist_ok=True)
    app = TokenAuth(mcp.streamable_http_app())
    print(f"[mcp-linux] listening on 0.0.0.0:{PORT}  endpoint: /mcp  health: /health", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
