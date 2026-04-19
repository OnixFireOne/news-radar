"""Shared test utilities. Not a package — imported by name via sys.path."""
import os
import httpx

RADAR_API   = os.environ.get("RADAR_API_URL", "http://localhost:8100")
AGENT_API   = os.environ.get("AGENT_API_URL", "http://localhost:18789")
AGENT_TOKEN = os.environ.get("OPENCLAW_API_TOKEN", "")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


class TestRunner:
    def __init__(self, suite_name: str):
        self.suite  = suite_name
        self.passed = 0
        self.failed = 0

    def ok(self, name: str, detail: str = ""):
        self.passed += 1
        suffix = f"  {detail}" if detail else ""
        print(f"  {GREEN}✓{RESET} {name}{suffix}")

    def fail(self, name: str, reason: str = ""):
        self.failed += 1
        print(f"  {RED}✗{RESET} {name}")
        if reason:
            print(f"    {YELLOW}→ {reason}{RESET}")

    def check(self, name: str, condition: bool, reason: str = ""):
        if condition:
            self.ok(name)
        else:
            self.fail(name, reason)

    def results(self) -> tuple[int, int]:
        return self.passed, self.failed


def get(path: str, **kw)   -> httpx.Response:
    return httpx.get(f"{RADAR_API}{path}", timeout=10, **kw)

def post(path: str, **kw)  -> httpx.Response:
    return httpx.post(f"{RADAR_API}{path}", timeout=10, **kw)

def patch(path: str, **kw) -> httpx.Response:
    return httpx.patch(f"{RADAR_API}{path}", timeout=10, **kw)

def agent_post(payload: dict) -> httpx.Response:
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    return httpx.post(
        f"{AGENT_API}/v1/chat/completions",
        json=payload, headers=headers, timeout=30,
    )
