import os, shlex, subprocess, textwrap, time, re
from pathlib import Path
from typing import List
from config import CONFIG

# --- Shell helpers ---
def run(cmd: str) -> str:
    try:
        out = subprocess.check_output(shlex.split(cmd), stderr=subprocess.STDOUT, timeout=10)
        return out.decode(errors="ignore").strip()
    except Exception as e:
        return f"ERR: {e}"

def run_argv(argv: List[str]) -> str:
    try:
        out = subprocess.check_output(argv, stderr=subprocess.STDOUT, timeout=10, text=True)
        return out.strip()
    except Exception as e:
        return f"ERR: {e}"

def run_argv_loose(argv: List[str]) -> str:
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=60)
    return (p.stdout or "").strip() or f"exit={p.returncode}"

# --- Files / logs ---
def file_tail(path: str, n: int=200) -> str:
    if not os.path.exists(path): return f"{path} not found"
    try:
        out = subprocess.check_output(["tail","-n",str(n),path], timeout=10, text=True)
        return out
    except Exception as e:
        return f"ERR: {e}"

def get_journal(unit: str | None, n: int = 200) -> str:
    if unit:
        return run(f"journalctl -u {unit} -n {n} --no-pager")
    return run(f"journalctl -n {n} --no-pager")

def get_os_logs(n: int = 200) -> str:
    return file_tail(CONFIG.OS_LOG, n) if os.path.exists(CONFIG.OS_LOG) else get_journal(None, n)

def get_asterisk_logs(n: int = 200) -> str:
    return file_tail(CONFIG.ASTERISK_LOG, n) if os.path.exists(CONFIG.ASTERISK_LOG) else get_journal("asterisk", n)

def _write_tmp(name: str, content: str) -> str:
    p = f"/tmp/{name}"
    Path(p).write_text(content, encoding="utf-8")
    return p

# --- Status helpers ---
def bytes2hr(n: int) -> str:
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"

def get_asterisk_uptime_text() -> str:
    tries = [
        ["/usr/sbin/rasterisk", "-x", "core show uptime"],
        ["rasterisk", "-x", "core show uptime"],
        [CONFIG.ASTERISK_CLI, "-rx", "core show uptime"],
        ["asterisk", "-rx", "core show uptime"],
    ]
    last = ""
    for argv in tries:
        out = run_argv_loose(argv)
        last = out
        if out and "Unable to connect to remote asterisk" not in out and "Unknown command" not in out:
            return out
    return last or "n/a"

def get_status() -> str:
    uptime = run("uptime -p")
    # temp
    temp = "n/a"
    try:
        t = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        temp = f"{int(t)/1000:.1f} °C"
    except Exception:
        t = run("/usr/bin/vcgencmd measure_temp")
        if "temp=" in t: temp = t.replace("temp=","").strip()
    # disk
    st = os.statvfs("/")
    free = st.f_bavail * st.f_frsize
    total = st.f_blocks * st_frs = st.f_frsize
    total = st.f_blocks * st.f_frsize
    # mem
    mem_free = 0
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        kv = {k.strip():int(v.split()[0])*1024 for k,v in (line.split(":",1) for line in meminfo)}
        mem_free = kv.get("MemAvailable", kv.get("MemFree",0))
    except Exception: pass
    # vpn
    wg_active = run(f"systemctl is-active wg-quick@{CONFIG.WG_IFACE}")
    wg_show   = run("wg show")
    # asterisk
    ast_active = run("systemctl is-active asterisk")
    ast_uptime = get_asterisk_uptime_text()
    # app version (новое)
    app_ver = get_app_version_text()

    return textwrap.dedent(f"""
    🖥️ *Server status*
    Uptime: `{uptime}`
    Temp: `{temp}`
    Disk: `{bytes2hr(total-free)}/{bytes2hr(total)} used`
    RAM free: `{bytes2hr(mem_free)}`
    VPN ({CONFIG.WG_IFACE}): `{wg_active}`
    Asterisk: `{ast_active}`

    WireGuard:
    ```
    {wg_show}
    ```

    Asterisk uptime:
    ```
    {ast_uptime}
    ```

    Приложение (Git):
    ```
    {app_ver}
    ```
    """).strip()


# --- TG/Ys helpers ---
def norm_sim(sim) -> int:
    s = str(sim or "").strip()
    m = re.search(r"\d+", s)
    return int(m.group()) if m else CONFIG.TG_DEFAULT_SIM

def render_resp(r: dict) -> str:
    line = f"{r.get('Response')} — {r.get('Message') or ''}".strip()
    outs = r.get("Outputs") or []
    if outs:
        line += "\n" + "\n".join(outs)
    return line

# --- Git update ---
def git_pull(repo_dir: str, branch: str) -> str:
    logs = []
    def add(cmd): logs.append("$ " + " ".join(cmd) + "\n" + run_argv_loose(cmd))

    # отметим каталог как safe для текущего пользователя
    add(["git","config","--global","--add","safe.directory", repo_dir])

    if not os.path.isdir(repo_dir) or not os.path.isdir(os.path.join(repo_dir, ".git")):
        if not CONFIG.GIT_REMOTE_URL:
            return f"Repo '{repo_dir}' missing and GIT_REMOTE_URL not set"
        add(["git", "clone", CONFIG.GIT_REMOTE_URL, repo_dir])

    add(["git","-C",repo_dir,"remote","-v"])
    add(["git","-C",repo_dir,"fetch","--all","--prune"])
    add(["git","-C",repo_dir,"checkout",branch])
    add(["git","-C",repo_dir,"pull","--ff-only","origin",branch])
    return "\n\n".join(logs)

def get_app_version_text() -> str:
    """
    Возвращает краткую сводку версии из Git для репозитория CONFIG.GIT_REPO_DIR.
    Толерантна к ошибкам/отсутствию репозитория.
    """
    repo = CONFIG.GIT_REPO_DIR

    # На всякий случай отметим каталог как безопасный (исправляет "dubious ownership")
    _ = run_argv_loose(["git", "config", "--global", "--add", "safe.directory", repo])

    inside = run_argv_loose(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"]).strip()
    if inside != "true":
        return "n/a"

    branch   = run_argv_loose(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    commit   = run_argv_loose(["git", "-C", repo, "rev-parse", "--short", "HEAD"]).strip()
    describe = run_argv_loose(["git", "-C", repo, "describe", "--tags", "--always", "--dirty"]).strip()
    date     = run_argv_loose(["git", "-C", repo, "show", "-s", "--format=%cd", "--date=iso-strict", "HEAD"]).strip()
    subj     = run_argv_loose(["git", "-C", repo, "show", "-s", "--format=%s", "HEAD"]).strip()
    dirty_out= run_argv_loose(["git", "-C", repo, "status", "--porcelain"])
    dirty    = "dirty" if dirty_out.strip() else "clean"

    # Склеим компактный блок
    return textwrap.dedent(f"""
    Branch: `{branch}`
    Commit: `{commit}` ({dirty})
    Tag/Describe: `{describe}`
    Date: `{date}`
    Subject: {subj or "-"}
    """).strip()
