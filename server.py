"""
MCP Server for remote file/system operations.
Expose tools, resources, and prompts over Streamable HTTP.

Usage:
    python server.py
    MCP_PORT=9000 python server.py
"""

import datetime
import glob as globmod
import json
import os
import pathlib
import shlex
import subprocess
import sys

from mcp.server import MCPServer

mcp = MCPServer("remote-tools")

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
MAX_OUTPUT = 10000000
MAX_CMD_TIMEOUT = 120
MAX_BG_PROCS = 50
_SENSITIVE = ("KEY", "TOKEN", "SECRET", "PASS", "PWD", "CREDENTIAL", "AUTH")


def _run(cmd, timeout=15, cwd=None, check=False):
    r = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        timeout=timeout, cwd=cwd or WORKSPACE,
    )
    output = r.stdout + r.stderr
    if check and r.returncode != 0:
        output += f"\n[exit code: {r.returncode}]"
    if len(output) > MAX_OUTPUT:
        half = MAX_OUTPUT // 2
        output = output[:half] + f"\n... [{len(output)} chars, truncated] ...\n" + output[-half:]
    return output


def _q(s):
    return shlex.quote(s)


def _find_pip():
    venv_pip = os.path.join(WORKSPACE, ".venv", "bin", "pip")
    if os.path.isfile(venv_pip):
        return venv_pip
    if subprocess.run("which uv", shell=True, capture_output=True).returncode == 0:
        return "uv pip"
    return "pip"


@mcp.tool()
def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read a file with optional line offset and limit."""
    p = pathlib.Path(path)
    if not p.exists():
        return f"Error: {path} does not exist"
    if not p.is_file():
        return f"Error: {path} is not a file"
    lines = p.read_text(errors="replace").splitlines()
    total = len(lines)
    selected = lines[offset:offset + limit]
    if total > limit:
        return f"[lines {offset}-{min(offset + limit, total)} of {total}]\n" + "\n".join(selected)
    return "\n".join(selected)


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent dirs automatically."""
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(content)
    return f"Wrote {len(content)} bytes to {path}"


@mcp.tool()
def append_file(path: str, content: str) -> str:
    """Append content to an existing file."""
    with open(path, "a") as f:
        f.write(content)
    return f"Appended {len(content)} bytes to {path}"


@mcp.tool()
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace an exact, unique substring in a file."""
    content = pathlib.Path(path).read_text()
    count = content.count(old_text)
    if count == 0:
        return "Error: old_text not found in file"
    if count > 1:
        return f"Error: old_text found {count} times, must be unique"
    pathlib.Path(path).write_text(content.replace(old_text, new_text, 1))
    return f"Replaced text in {path}"


@mcp.tool()
def list_dir(path: str = ".", show_hidden: bool = False, long: bool = False) -> str:
    """List directory contents."""
    p = pathlib.Path(path)
    if not p.exists():
        return f"Error: {path} does not exist"
    if long:
        flags = "-la" if show_hidden else "-l"
        return _run(f"ls {flags} {_q(str(p))}", timeout=10)
    entries = sorted(p.iterdir())
    if not show_hidden:
        entries = [e for e in entries if not e.name.startswith(".")]
    lines = []
    for e in entries:
        tag = "/" if e.is_dir() else ""
        size = e.stat().st_size if e.is_file() else 0
        lines.append(f"{e.name}{tag}  ({size} bytes)")
    return "\n".join(lines) or "(empty)"


@mcp.tool()
def glob_search(pattern: str, root: str = ".") -> str:
    """Find files matching a glob pattern (e.g. '**/*.py')."""
    matches = sorted(globmod.glob(os.path.join(root, pattern), recursive=True))
    if not matches:
        return "No files matched"
    out = "\n".join(matches[:200])
    if len(matches) > 200:
        out += f"\n... ({len(matches)} total)"
    return out


@mcp.tool()
def grep_search(pattern: str, path: str = ".", include: str = "") -> str:
    """Search for a regex pattern in files (like grep -rn)."""
    inc = f"--include={_q(include)} " if include else ""
    output = _run(f"grep -rn {inc}{_q(pattern)} {_q(path)}", timeout=15)
    if not output.strip():
        return "No matches found"
    lines = output.splitlines()
    if len(lines) > 100:
        return "\n".join(lines[:100]) + f"\n... ({len(lines)} matches total)"
    return output


@mcp.tool()
def file_info(path: str) -> str:
    """Get file/directory metadata (size, permissions, timestamps)."""
    p = pathlib.Path(path)
    if not p.exists():
        return f"Error: {path} does not exist"
    st = p.stat()
    return json.dumps({
        "path": str(p.resolve()),
        "type": "directory" if p.is_dir() else "file",
        "size_bytes": st.st_size,
        "permissions": oct(st.st_mode)[-3:],
        "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
        "created": datetime.datetime.fromtimestamp(st.st_ctime).isoformat(),
    }, indent=2)


_bg_procs: dict[int, subprocess.Popen] = {}


@mcp.tool()
def run_command(command: str, timeout: int = 30, cwd: str = "/workspace") -> str:
    """Execute a shell command and return stdout+stderr."""
    timeout = min(max(timeout, 1), MAX_CMD_TIMEOUT)
    return _run(command, timeout=timeout, cwd=cwd, check=True)


@mcp.tool()
def run_background(command: str, cwd: str = "/workspace") -> str:
    """Start a command in the background. Returns a PID."""
    if len(_bg_procs) >= MAX_BG_PROCS:
        return f"Error: too many background processes ({MAX_BG_PROCS} max)"
    proc = subprocess.Popen(
        command, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=cwd,
    )
    _bg_procs[proc.pid] = proc
    return f"Started PID={proc.pid}"


@mcp.tool()
def list_background() -> str:
    """List background processes started via run_background."""
    lines = []
    for pid in list(_bg_procs):
        proc = _bg_procs[pid]
        if proc.poll() is None:
            lines.append(f"PID={pid}  RUNNING")
        else:
            lines.append(f"PID={pid}  EXITED(code={proc.returncode})")
            del _bg_procs[pid]
    return "\n".join(lines) if lines else "No background processes"


@mcp.tool()
def kill_background(pid: int) -> str:
    """Kill a background process by PID."""
    proc = _bg_procs.get(pid)
    if not proc:
        return f"No background process with PID {pid}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    del _bg_procs[pid]
    return f"Killed PID {pid}"


@mcp.tool()
def run_python(code: str, timeout: int = 15) -> str:
    """Execute Python code and return output."""
    return _run(
        f"{sys.executable} -c {_q(code)}",
        timeout=min(timeout, 60), cwd=WORKSPACE, check=True,
    )


@mcp.tool()
def git_status(path: str = "/workspace") -> str:
    """Show git status."""
    return _run("git status", cwd=path)


@mcp.tool()
def git_log(path: str = "/workspace", count: int = 10) -> str:
    """Show recent git log."""
    return _run(f"git log --oneline -{count}", cwd=path)


@mcp.tool()
def git_diff(path: str = "/workspace", target: str = "") -> str:
    """Show git diff. Optionally against a branch or commit."""
    cmd = f"git diff {_q(target)}" if target else "git diff"
    return _run(cmd, timeout=15, cwd=path)


@mcp.tool()
def git_branch(path: str = "/workspace") -> str:
    """List git branches."""
    return _run("git branch -a", cwd=path)


@mcp.tool()
def system_info() -> str:
    """Get comprehensive system information."""
    fields = [
        ("hostname", "hostname"),
        ("os", "cat /etc/os-release | head -2"),
        ("kernel", "uname -r"),
        ("arch", "uname -m"),
        ("uptime", "uptime"),
        ("memory", "free -h"),
        ("disk", "df -h / /workspace 2>/dev/null"),
        ("cpu", "nproc && cat /proc/cpuinfo | grep 'model name' | head -1"),
        ("python", "python3 --version"),
        ("node", "node --version 2>/dev/null || echo 'not installed'"),
        ("git", "git --version"),
        ("go", "go version 2>/dev/null || echo 'not installed'"),
    ]
    info = {}
    for key, cmd in fields:
        info[key] = _run(cmd, timeout=5).strip()
    return json.dumps(info, indent=2)


@mcp.tool()
def process_list(filter: str = "") -> str:
    """List running processes. Optionally filter by name."""
    if filter:
        return _run(f"ps aux | grep {_q(filter)} | grep -v grep", timeout=5)
    return _run("ps aux --sort=-%mem | head -30", timeout=5)


@mcp.tool()
def network_info() -> str:
    """Get network configuration and connectivity info."""
    checks = [
        ("dns", "cat /etc/resolv.conf 2>/dev/null"),
        ("connectivity", "curl -s -o /dev/null -w '%{http_code}' https://httpbin.org/get 2>/dev/null || echo 'no internet'"),
        ("hostname", "hostname -I 2>/dev/null || echo 'unavailable'"),
    ]
    results = {}
    for key, cmd in checks:
        results[key] = _run(cmd, timeout=10).strip()
    return json.dumps(results, indent=2)


@mcp.tool()
def http_get(url: str, max_chars: int = 5000) -> str:
    """Fetch a URL and return the response body."""
    output = _run(
        ["curl", "-s", "-L", "-m", "15", "-w", "\\n[HTTP %{http_code}]", url],
        timeout=20,
    )
    if len(output) > max_chars:
        output = output[:max_chars] + f"\n... [{len(output)} total chars]"
    return output


@mcp.tool()
def http_download(url: str, path: str) -> str:
    """Download a file from a URL."""
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    output = _run(
        ["curl", "-s", "-L", "-m", "60", "-o", path, "-w", "%{http_code} %{size_download}"],
        timeout=65,
    )
    return f"Downloaded to {path}: {output.strip()}"


@mcp.tool()
def pip_install(packages: str) -> str:
    """Install Python packages. Uses venv pip if available, falls back to uv pip."""
    return _run(f"{_find_pip()} install {packages}", timeout=120)


@mcp.tool()
def npm_install(packages: str, global_install: bool = False) -> str:
    """Install npm packages."""
    flag = "-g" if global_install else ""
    return _run(f"npm install {flag} {packages}", timeout=120)[-3000:]


@mcp.tool()
def count_lines(path: str) -> str:
    """Count lines, words, and characters in a file."""
    return _run(f"wc -lwc {_q(path)}").strip()


@mcp.tool()
def head_tail(path: str, lines: int = 20, mode: str = "head") -> str:
    """Read first or last N lines of a file. mode='head' or 'tail'."""
    if mode not in ("head", "tail"):
        return "Error: mode must be 'head' or 'tail'"
    return _run(f"{mode} -{lines} {_q(path)}", timeout=10)


@mcp.tool()
def get_env(name: str = "") -> str:
    """Get environment variable(s). Sensitive vars are filtered out by default."""
    if name:
        val = os.environ.get(name)
        return f"{name}={val}" if val is not None else f"{name} is not set"
    safe = {
        k: v for k, v in sorted(os.environ.items())
        if not any(s in k.upper() for s in _SENSITIVE)
    }
    return "\n".join(f"{k}={v}" for k, v in safe.items())


@mcp.tool()
def set_env(name: str, value: str) -> str:
    """Set an environment variable for the current server process."""
    os.environ[name] = value
    return f"Set {name}={value}"


@mcp.resource("system://info")
def system_info_resource() -> str:
    """Live system information snapshot."""
    output = _run("free -h && echo '---' && df -h / && echo '---' && uptime", timeout=5)
    return f"Time: {datetime.datetime.now().isoformat()}\n{output}"


@mcp.resource("workspace://files")
def workspace_files() -> str:
    """List all files in the workspace (up to 3 levels deep)."""
    return _run(f"find {_q(WORKSPACE)} -maxdepth 3 -type f | head -100", timeout=5)


@mcp.resource("git://status")
def git_status_resource() -> str:
    """Current git status and recent commits."""
    return _run("git status && echo '---' && git log --oneline -5", timeout=5)


@mcp.prompt()
def explain_code(code: str) -> str:
    """Explain what a piece of code does."""
    return f"Explain this code in detail:\n\n```\n{code}\n```"


@mcp.prompt()
def debug_error(error: str) -> str:
    """Help debug an error message."""
    return f"I'm getting this error:\n\n```\n{error}\n```\n\nHelp me debug it."


@mcp.prompt()
def review_code(code: str) -> str:
    """Review code for issues and improvements."""
    return f"Review this code for bugs, performance, and readability:\n\n```\n{code}\n```"


if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", "8766"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
