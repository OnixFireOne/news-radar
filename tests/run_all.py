#!/usr/bin/env python3
"""
News Radar Integration Test Runner
===================================
Tests the interaction between news-radar API and the OpenClaw Agent.

Usage:
    python tests/run_all.py                    # run all suites
    python tests/run_all.py api                # only API tests
    python tests/run_all.py agent              # only agent tests
    python tests/run_all.py settings           # only settings tests

Environment:
    RADAR_API_URL   (default: http://localhost:8100)
    AGENT_API_URL   (default: http://localhost:18789)
"""
import sys
import os
import importlib.util

# Allow running from project root or tests/ dir
sys.path.insert(0, os.path.dirname(__file__))

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)           # makes `integration` package findable

GREEN = "\033[92m"
RED   = "\033[91m"
CYAN  = "\033[96m"
BOLD  = "\033[1m"
RESET = "\033[0m"

SUITES_FILES = {
    "api":      "integration/test_api_endpoints.py",
    "agent":    "integration/test_agent_dispatch.py",
    "settings": "integration/test_settings.py",
}


def run_suite(name: str, rel_path: str) -> tuple[int, int]:
    print(f"\n{BOLD}{CYAN}══ {name.upper()} ══{RESET}")
    try:
        import importlib.util
        file_path = os.path.join(ROOT, rel_path)
        spec = importlib.util.spec_from_file_location(f"suite_{name}", file_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.run()
    except Exception as e:
        import traceback
        print(f"{RED}Suite failed to load: {e}{RESET}")
        traceback.print_exc()
        return 0, 1


def main():
    filter_names = sys.argv[1:] if len(sys.argv) > 1 else list(SUITES_FILES.keys())
    total_pass, total_fail = 0, 0

    for name in filter_names:
        if name not in SUITES_FILES:
            print(f"{RED}Unknown suite: {name}. Options: {list(SUITES_FILES)}{RESET}")
            continue
        p, f = run_suite(name, SUITES_FILES[name])
        total_pass += p
        total_fail += f

    print(f"\n{BOLD}{'─' * 40}{RESET}")
    print(f"  {GREEN}Passed: {total_pass}{RESET}   {RED}Failed: {total_fail}{RESET}")
    print(f"{'─' * 40}")
    sys.exit(1 if total_fail > 0 else 0)


if __name__ == "__main__":
    main()
