#!/usr/bin/env python3
"""Verify README.md against the repository, not against anyone's memory.

This exists because the first draft of the README inherited three claims from CLAUDE.md that were
no longer true (the default build env, the number of make targets, the WLED version). The README is
the page a remote collaborator sets up from with nobody to ask, so its claims have to be checked
against the source files that define them.

Run: python3 tools/check_readme.py   (exit 1 on any failure)
"""
import os
import re
import subprocess
import sys

SUBMODULES = ("WLED/", "ArduinoLibs/", "HMTL/", "esp-now-router/")
failures = []


def check(ok, msg):
    print(("  ok    " if ok else "  FAIL  ") + msg)
    if not ok:
        failures.append(msg)


def main():
    readme = open("README.md").read()

    print("links resolve to files tracked in THIS repo")
    for _text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme):
        if target.startswith("http"):
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", target], capture_output=True
        ).returncode == 0
        check(tracked, f"link -> {target}")
        # A submodule file exists on disk but is not this repo's file, and GitHub renders the
        # submodule as a commit pointer, so the link 404s in the web view.
        check(not target.startswith(SUBMODULES), f"link not into a submodule: {target}")

    print("no instruction to consult CLAUDE.md for a setup step")
    check(not re.search(r"see CLAUDE|refer to CLAUDE|per CLAUDE", readme, re.I),
          "README stands alone for setup")

    print("every make target named exists")
    makefile = open("Makefile").read()
    for target in sorted(set(re.findall(r"make (test[a-z-]*)\b", readme))):
        check(re.search(rf"^{re.escape(target)}:", makefile, re.M) is not None, f"make {target}")

    print("every [env:] exists in the ini the README attributes it to")
    wled = open("WLED/platformio.ini").read()
    router = open("esp-now-router/platformio.ini").read()
    for env in ("ampworks", "apa102_mpr121", "display_only"):
        check(f"[env:{env}]" in wled, f"WLED/platformio.ini has {env}")
    for env in ("wt32-eth01", "esp32dev", "native"):
        check(f"[env:{env}]" in router, f"esp-now-router/platformio.ini has {env}")

    print("submodule table matches .gitmodules")
    gitmodules = open(".gitmodules").read()
    paths = set(re.findall(r"path = (\S+)", gitmodules))
    check(paths == {"WLED", "ArduinoLibs", "HMTL", "esp-now-router"}, f"paths {sorted(paths)}")
    for url in re.findall(r"url = (\S+)", gitmodules):
        slug = url.replace("https://github.com/", "").replace(".git", "")
        check(slug in readme, f"repo {slug} named")

    print("referenced commands and files are real")
    check(os.access("setup.sh", os.X_OK), "setup.sh is executable")
    check("--no-verify" in open("setup.sh").read(), "setup.sh --no-verify exists")
    check("WLED_IP" in open("WLED/tools/upload_wled.py").read(), "upload honours WLED_IP")
    check(os.path.exists("WLED/tools/set_build_info.py"), "set_build_info.py exists")
    check(os.path.isdir("WLED/wled00"), "firmware source at WLED/wled00/")

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
