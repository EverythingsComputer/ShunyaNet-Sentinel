#!/usr/bin/env python3
"""CLI: python cli_run.py <test_script.py> — runs the test in an isolated scratch."""
import sys
from isolated import run

code, scratch = run(sys.argv[1])
print("SCRATCH_DIR=%s" % scratch)
sys.exit(code)
