#!/usr/bin/env python3
"""Isolated test runner: copies app to scratch, seeds state if given, runs test."""
import os, shutil, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE = {"venv", "venv-linux", ".git", "__pycache__", "Report_Output",
           ".DS_Store", "Previous-Versions", "tests", "_local-scratch", "docs"}


def make_scratch(seed=None):
    """seed: list of (src_path, dest_name) copied in after the app copy."""
    d = tempfile.mkdtemp(prefix="sentinel_scratch_")
    for item in os.listdir(SRC):
        if item in EXCLUDE or item.endswith("-old.py") or item.endswith("-old.txt"):
            continue
        s = os.path.join(SRC, item)
        t = os.path.join(d, item)
        if os.path.isdir(s):
            shutil.copytree(s, t, dirs_exist_ok=True)
        else:
            shutil.copy2(s, t)
    for junk in ("app_state.json", ".sentinel_first_run", "rolling_rss.txt",
                 "Memory_Long_Term.txt", "Memory_Short_Term.txt"):
        p = os.path.join(d, junk)
        if os.path.exists(p):
            os.remove(p)
    for src_path, dest_name in (seed or []):
        shutil.copy2(src_path, os.path.join(d, dest_name))
    return d


def run(test_script, seed=None, timeout=300):
    scratch = make_scratch(seed)
    env = dict(os.environ)
    env["SENTINEL_APP"] = os.path.join(scratch, "ShunyaNet_Sentinel.py")
    env["SENTINEL_DIR"] = scratch
    env["QT_QPA_PLATFORM"] = "offscreen"
    venv_py = os.path.join(SRC, "venv-linux", "bin", "python")
    if not os.path.exists(venv_py):
        venv_py = sys.executable
    r = subprocess.run([venv_py, "-u", test_script], env=env, timeout=timeout)
    return r.returncode, scratch
