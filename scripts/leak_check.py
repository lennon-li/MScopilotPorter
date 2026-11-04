#!/usr/bin/env python3
"""Fail if tracked files contain machine-specific or secret-looking content."""
import re
import subprocess
import sys

PATTERNS = {
    "home directory path": re.compile(r"/home/[A-Za-z0-9_.-]+"),
    "concrete dev tunnel id": re.compile(r"\b[a-z0-9]{6,12}-\d{2,5}\.[a-z0-9]+\.devtunnels\.ms"),
    "API key assignment": re.compile(r"MSCP_KEY=[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
SKIP = {"scripts/leak_check.py"}


def main() -> int:
    files = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    bad = 0
    for name in files:
        if name in SKIP:
            continue
        try:
            text = open(name, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                print(f"{name}:{line}: {label}: {match.group(0)[:40]}")
                bad += 1
    print("leak check:", "FAILED" if bad else "clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
