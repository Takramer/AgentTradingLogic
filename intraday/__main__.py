"""`python -m intraday <cmd>` — the trial ledger CLI, pinned to intraday's own
ledger (intraday/data/trials.jsonl) and family, so a day-trading trial can
never land in the swing-trading ledger by a forgotten flag.

    python -m intraday log --label "<idea>"
    python -m intraday log --label outcome --resolves "<idea>" --outcome rejected
    python -m intraday report [--t 2.1]
    python -m intraday list
"""
import sys

from rhengine import trials as T

from .validate import FAMILY, LEDGER

if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] in ("log", "report", "list") and "--family" not in argv:
        argv += ["--family", FAMILY]
    if argv and argv[0] == "log" and "--source" not in argv:
        argv += ["--source", "intraday/docs/IDEAS.md"]
    sys.exit(T.main(["--ledger", LEDGER, *argv]))
