#!/usr/bin/env python3
"""Zero-dependency test runner (no pytest needed).

    python tests/run_tests.py        # from the fib-leg/ repo root

Discovers test_* functions in the test modules, runs them, prints a summary.
"""
from __future__ import annotations

import os
import sys
import traceback

# make the repo root importable when run as `python tests/run_tests.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_dhan     # noqa: E402
import test_fib_leg  # noqa: E402
import test_fyers    # noqa: E402
import test_pivots   # noqa: E402

MODULES = [test_pivots, test_fib_leg, test_fyers, test_dhan]


def main() -> int:
    passed = failed = 0
    for mod in MODULES:
        for name in dir(mod):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  PASS  {mod.__name__}.{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"  FAIL  {mod.__name__}.{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
