#!/usr/bin/env python3
"""Verify README.md against the repository, not against anyone's memory.

This exists because the first draft of the README inherited three claims from CLAUDE.md that were
no longer true (the default build env, the number of make targets, the WLED version). The README is
the page a remote collaborator sets up from with nobody to ask, so its claims have to be checked
against the source files that define them.

Every assertion here must be able to FAIL. Self-review of the first version found two that could
not: a substring test that `aphelps/WLED` satisfied via the unrelated `aphelps/WLED_dev.git` clone
URL, and two sections that announced they were checking the README while actually testing hardcoded
literals. Both are fixed below by parsing the README and matching precisely; if you add a check,
prove it fails by breaking the thing it guards.

Run via `make test-readme` — the Makefile fails hard on a missing submodule before Python
starts. Invoked directly, a missing submodule is a SKIP with exit 0, so the bare script is not a
safe CI entry point on an uninitialised clone. Exit 1 on any failure; runs from anywhere.
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBMODULE_PREFIXES = ("WLED/", "ArduinoLibs/", "HMTL/", "esp-now-router/")
failures = []


def check(ok, msg):
    print(("  ok    " if ok else "  FAIL  ") + msg)
    if not ok:
        failures.append(msg)


def read(rel):
    with open(os.path.join(REPO, rel)) as fh:
        return fh.read()


def tracked(rel):
    return subprocess.run(["git", "-C", REPO, "ls-files", "--error-unmatch", rel],
                          capture_output=True).returncode == 0


def main():
    # A missing submodule is a setup problem, not a README problem — say so rather than dying on a
    # FileNotFoundError traceback, which is exactly the unhelpful failure this repo's Makefile
    # takes care to avoid elsewhere.
    for need in ("WLED/platformio.ini", "esp-now-router/platformio.ini"):
        if not os.path.exists(os.path.join(REPO, need)):
            print(f"SKIP: {need} missing — run ./setup.sh (or: git submodule update --init)")
            return 0

    readme = read("README.md")

    print("links resolve to files tracked in THIS repo")
    for _text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme):
        if target.startswith("http"):
            continue
        check(tracked(target), f"link -> {target}")
        # A submodule file exists on disk but is not this repo's file, and GitHub renders the
        # submodule as a commit pointer, so the link 404s in the web view.
        check(not target.startswith(SUBMODULE_PREFIXES), f"link not into a submodule: {target}")

    print("commands shown in bash blocks actually parse")
    # Whole block at once: per-line `bash -n` rejects any multi-line construct (`for ... do` alone
    # is a syntax error, and so is `done`), while a broken `\`-continuation parses fine as two
    # independent halves. The block is the unit a reader pastes.
    makefile = read("Makefile")
    make_targets = sorted(set(re.findall(r"^([A-Za-z][\w.-]*):", makefile, re.M)), key=len,
                          reverse=True)
    glued = re.compile(r"\b(" + "|".join(re.escape(tg) for tg in make_targets) + r")#")
    for block in re.findall(r"```bash\n(.*?)```", readme, re.S):
        first = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")
        ok = subprocess.run(["bash", "-n"], input=block, text=True,
                            capture_output=True).returncode == 0
        check(ok, f"pastes cleanly: {first[:60]}")
        for line in block.splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            # bash -n only catches SYNTAX errors. `make test-libs# note` parses fine as a command
            # yet invokes the target `test-libs#`, which does not exist. Matching bare `\S#`
            # false-positives on URL fragments and quoted strings, so only fire when the token
            # fused to the `#` is a real make target.
            check(not glued.search(line),
                  f"'#' is separated from the make target: {line.strip()[:60]}")

    print("no instruction to consult CLAUDE.md for a setup step")
    check(not re.search(r"see CLAUDE|refer to CLAUDE|per CLAUDE", readme, re.I),
          "README stands alone for setup")

    print("every make target the README names exists")
    named_targets = sorted(set(re.findall(r"^make (test[a-z-]*)\b", readme, re.M)))
    check(bool(named_targets), "README names at least one make target")
    for target in named_targets:
        check(re.search(rf"^{re.escape(target)}:", makefile, re.M) is not None, f"make {target}")

    print("every [env:] the README names exists in the ini it attributes it to")
    # Parsed from the README's own env tables, not hardcoded: a rename or typo in the README must
    # fail here. Each table follows the heading naming its ini.
    inis = {"WLED/platformio.ini": read("WLED/platformio.ini"),
            "esp-now-router/platformio.ini": read("esp-now-router/platformio.ini")}
    sections = re.split(r"^`([^`]+platformio\.ini)`", readme, flags=re.M)
    found_any = False
    for idx in range(1, len(sections), 2):
        ini_name, body = sections[idx], sections[idx + 1]
        if ini_name not in inis:
            continue
        body = body.split("\n## ")[0]
        for env in re.findall(r"^\| `([a-z0-9_.-]+)` \|", body, re.M):
            found_any = True
            check(f"[env:{env}]" in inis[ini_name], f"{ini_name} has [env:{env}]")
    check(found_any, "README's env tables were parsed (not silently empty)")

    print("submodule table matches .gitmodules")
    gitmodules = read(".gitmodules")
    # Require the path and its upstream URL on the SAME line. Testing them separately is too weak:
    # `| `WLED/`` also appears in the Repo layout table, so deleting the whole submodule row still
    # passed a "does this path appear anywhere" check.
    for path, url in re.findall(r"path = (\S+)\s*\n\s*url = (\S+)", gitmodules):
        stripped = url.replace(".git", "")
        row = re.search(rf"^\| `{re.escape(path)}/` \|.*{re.escape(stripped)}.*\|", readme, re.M)
        check(row is not None, f"submodule table row for {path} -> {stripped}")

    print("drift-prone numbers still true")
    # A missing source key is a FAIL line, not an AttributeError traceback — same principle as
    # the submodule pre-flight above.
    pkg = read("WLED/package.json")
    version_m = re.search(r'"version":\s*"([^"]+)"', pkg)
    check(version_m is not None, "WLED/package.json has a version field")
    if version_m:
        check(f"WLED {version_m.group(1)}" in readme,
              f"WLED version {version_m.group(1)} as stated")
    # Match the number near its keyword rather than an exact phrase: "37 build environments" is
    # still true and must still pass, while a wrong count or a new upstream env must still fail.
    env_count = len(re.findall(r"^\[env:", inis["WLED/platformio.ini"], re.M))
    check(re.search(rf"\b{env_count}\b[^.\n]*environments", readme) is not None,
          f"{env_count} environments as stated")
    # Stop at the blank line that terminates the continued value. Matching to the next [section]
    # over-captures the keys that follow and inflates the count — it said 36 when the answer is 23.
    default_m = re.search(r"^default_envs\s*=\s*(.*?)(?=^\s*$)",
                          inis["WLED/platformio.ini"], re.M | re.S)
    check(default_m is not None, "WLED/platformio.ini has default_envs")
    if default_m:
        n_default = len([e for e in default_m.group(1).split()
                         if e and not e.startswith((";", "#"))])
        check(re.search(rf"\b{n_default}\b[^.\n]*(ESP8266|targets)", readme) is not None,
              f"{n_default} default_envs targets as stated")
    # Scope to [env:ampworks]: the first upload_port anywhere in the ~3000-line upstream ini is
    # only ours by luck. And a missing key is a failure, not silence — the README hardcodes the
    # device address in three places on the strength of this value.
    ampworks_ini = inis["WLED/platformio.ini"].split("[env:ampworks]", 1)[-1].split("\n[env:")[0]
    check("[env:ampworks]" in inis["WLED/platformio.ini"], "[env:ampworks] exists")
    port = re.search(r"^upload_port\s*=\s*(\S+)", ampworks_ini, re.M)
    check(port is not None, "upload_port set in [env:ampworks]")
    if port:
        check(port.group(1) in readme, f"default device {port.group(1)} as stated")

    print("referenced commands and files are real")
    check(os.access(os.path.join(REPO, "setup.sh"), os.X_OK), "setup.sh is executable")
    check("--no-verify" in read("setup.sh"), "setup.sh --no-verify exists")
    check("WLED_IP" in read("WLED/tools/upload_wled.py"), "upload honours WLED_IP")
    check(os.path.exists(os.path.join(REPO, "WLED/tools/set_build_info.py")),
          "set_build_info.py exists")
    check(os.path.isdir(os.path.join(REPO, "WLED/wled00")), "firmware source at WLED/wled00/")

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
