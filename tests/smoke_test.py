"""End-to-end smoke test: talks to a running server through a real MCP client.

    MCP_URL=http://127.0.0.1:8000/mcp MCP_TOKEN=xxx python tests/smoke_test.py
"""
import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
TOKEN = os.environ["MCP_TOKEN"]


async def call(s, name, **args):
    r = await s.call_tool(name, args)
    out = r.structuredContent if r.structuredContent is not None else json.loads(r.content[0].text)
    return out["result"] if isinstance(out, dict) and list(out) == ["result"] else out


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        check.failed = True


check.failed = False


async def main():
    base = URL.rsplit("/mcp", 1)[0]
    async with httpx.AsyncClient() as h:
        check("health open", (await h.get(base + "/health")).status_code == 200)
        check("no token -> 401", (await h.post(URL, json={})).status_code == 401)
        check("bad token -> 401", (await h.post(URL, json={}, headers={"Authorization": "Bearer nope"})).status_code == 401)
        check("query token accepted", (await h.post(URL + "?token=" + TOKEN, json={})).status_code != 401)

    async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            check("tools listed", {"exec", "read_file", "write_file", "edit_file", "list_dir",
                                    "job_start", "job_status", "job_kill", "system_info"} <= names)

            x = await call(s, "exec", command="echo hi; echo err >&2; exit 3")
            check("exec stdout/stderr/exit", x["stdout"] == "hi\n" and x["stderr"] == "err\n" and x["exit_code"] == 3)
            x = await call(s, "exec", command="cat", stdin="piped")
            check("exec stdin", x["stdout"] == "piped")
            x = await call(s, "exec", command="sleep 30", timeout=1)
            check("exec timeout kills", x["timed_out"] is True and x["duration_s"] < 5)
            x = await call(s, "exec", command="echo $FOO; pwd", env={"FOO": "bar"}, cwd="/tmp")
            check("exec env+cwd", x["stdout"] == "bar\n/tmp\n")
            x = await call(s, "exec", command="id -u")
            print("     uid:", x["stdout"].strip())

            d = "/tmp/mcp-smoke"
            await call(s, "exec", command=f"rm -rf {d}")
            await call(s, "write_file", path=f"{d}/a/b.txt", content="one\ntwo\nthree\n")
            x = await call(s, "read_file", path=f"{d}/a/b.txt", offset=1, limit=1)
            check("write+read lines", x["content"] == "two\n" and x["total_lines"] == 3)
            await call(s, "write_file", path=f"{d}/a/b.txt", content="four\n", append=True)
            x = await call(s, "edit_file", path=f"{d}/a/b.txt", old="two", new="2")
            check("edit_file", x.get("replacements") == 1)
            x = await call(s, "edit_file", path=f"{d}/a/b.txt", old="zzz", new="y")
            check("edit_file miss", "error" in x)
            x = await call(s, "read_file", path=f"{d}/a/b.txt")
            check("content after edit", x["content"] == "one\n2\nthree\nfour\n")
            await call(s, "write_file", path=f"{d}/bin.dat", content="AP8AAQ==", encoding="base64", mode="755")
            x = await call(s, "read_file", path=f"{d}/bin.dat")
            check("binary -> base64", x["encoding"] == "base64" and x["content"] == "AP8AAQ==")
            x = await call(s, "exec", command=f"stat -c %a {d}/bin.dat")
            check("chmod mode", x["stdout"].strip() == "755")
            x = await call(s, "list_dir", path=d, depth=2)
            check("list_dir recursive", {e["path"] for e in x} >= {f"{d}/a", f"{d}/a/b.txt", f"{d}/bin.dat"})

            j = await call(s, "job_start", command="for i in 1 2 3; do echo tick$i; sleep 0.2; done; sleep 60")
            await asyncio.sleep(1.5)
            st = await call(s, "job_status", job_id=j["job_id"], tail=2)
            check("job running + tail", st["running"] and "tick3" in st["output"] and "tick1" not in st["output"])
            k = await call(s, "job_kill", job_id=j["job_id"])
            check("job_kill", k["running"] is False)

            x = await call(s, "system_info")
            check("system_info", x["cpus"] >= 1)
    sys.exit(1 if check.failed else 0)


asyncio.run(main())
