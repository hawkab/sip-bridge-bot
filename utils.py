import os, shlex, subprocess, textwrap, time, re, uuid
from pathlib import Path
from typing import List, Tuple
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

def run_argv_result(argv: List[str], timeout: int = 60) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return p.returncode, (p.stdout or "").strip() or f"exit={p.returncode}"
    except Exception as e:
        return 1, f"ERR: {e}"

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
    total = st.f_blocks * st.f_frsize
    # mem
    mem_free = 0
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        kv = {k.strip():int(v.split()[0])*1024 for k,v in (line.split(":",1) for line in meminfo)}
        mem_free = kv.get("MemAvailable", kv.get("MemFree",0))
    except Exception: pass
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
    Asterisk: `{ast_active}`

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
def _git_path_dirty(repo_dir: str, rel_path: str) -> bool:
    code, out = run_argv_result(["git", "-C", repo_dir, "status", "--porcelain", "--", rel_path])
    return code == 0 and bool(out.strip())

def _git_stash_push_paths(repo_dir: str, paths: List[str], stash_name: str) -> Tuple[bool, str]:
    argv = ["git", "-C", repo_dir, "stash", "push", "-m", stash_name, "--"] + paths
    code, out = run_argv_result(argv)
    return code == 0, out

def _git_stash_pop_by_name(repo_dir: str, stash_name: str) -> Tuple[bool, str]:
    code, out = run_argv_result(["git", "-C", repo_dir, "stash", "list"])
    if code != 0:
        return False, out

    stash_ref = ""
    for line in out.splitlines():
        if stash_name in line:
            stash_ref = line.split(":", 1)[0].strip()
            break

    if not stash_ref:
        return True, f"stash '{stash_name}' not found in list; nothing to restore"

    pop_code, pop_out = run_argv_result(["git", "-C", repo_dir, "stash", "pop", stash_ref], timeout=120)
    return pop_code == 0, pop_out

def _git_current_head(repo_dir: str) -> str:
    code, out = run_argv_result(["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"])
    return out if code == 0 else "unknown"

def _git_local_branch(repo_dir: str) -> str:
    code, out = run_argv_result(["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"])
    return out if code == 0 else "unknown"

def git_pull(repo_dir: str, branch: str) -> str:
    logs = []

    def add(cmd):
        logs.append("$ " + " ".join(cmd) + "\n" + run_argv_loose(cmd))

    def add_result(cmd):
        code, out = run_argv_result(cmd, timeout=120)
        logs.append("$ " + " ".join(cmd) + "\n" + out)
        return code, out

    # отметим каталог как safe для текущего пользователя
    add(["git","config","--global","--add","safe.directory", repo_dir])

    if not os.path.isdir(repo_dir) or not os.path.isdir(os.path.join(repo_dir, ".git")):
        if not CONFIG.GIT_REMOTE_URL:
            return f"Repo '{repo_dir}' missing and GIT_REMOTE_URL not set"
        add(["git", "clone", CONFIG.GIT_REMOTE_URL, repo_dir])

    add(["git","-C",repo_dir,"remote","-v"])
    add(["git","-C",repo_dir,"fetch","--all","--prune"])

    current_branch = _git_local_branch(repo_dir)
    current_head = _git_current_head(repo_dir)
    stash_name = f"sms-bot-update-{current_branch}-{current_head}-{uuid.uuid4().hex[:8]}"
    proxy_rel_path = "proxy.txt"
    proxy_stashed = False

    if _git_path_dirty(repo_dir, proxy_rel_path):
        logs.append(
            "# proxy.txt has local modifications. Stashing it before pull so update is not blocked."
        )
        ok, out = _git_stash_push_paths(repo_dir, [proxy_rel_path], stash_name)
        logs.append(
            "$ git -C " + repo_dir + " stash push -m " + stash_name + " -- " + proxy_rel_path + "\n" + out
        )
        if not ok:
            logs.append("# failed to stash proxy.txt; aborting update to avoid losing local proxy list")
            return "\n\n".join(logs)
        proxy_stashed = True
    else:
        logs.append("# proxy.txt has no local modifications; stash is not required")

    add(["git","-C",repo_dir,"checkout",branch])
    pull_code, _ = add_result(["git","-C",repo_dir,"pull","--ff-only","origin",branch])

    if proxy_stashed:
        logs.append("# restoring local proxy.txt changes after pull")
        ok, out = _git_stash_pop_by_name(repo_dir, stash_name)
        logs.append("$ git -C " + repo_dir + " stash pop <matched-stash>\n" + out)
        if not ok:
            logs.append(
                "# stash pop failed. Most likely proxy.txt has merge conflicts with upstream. "
                "Resolve proxy.txt manually and run 'git -C {repo} status'.".format(repo=repo_dir)
            )

    if pull_code != 0:
        logs.append(
            "# git pull failed. Working tree was not auto-reset. Inspect the log above; "
            "if stash was created, it has already been restored or left in git stash list."
        )

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
