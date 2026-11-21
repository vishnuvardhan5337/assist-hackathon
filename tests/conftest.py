"""Shared setup: ingest days 1-5 of the pack into a temporary store once per test session.
Needs R3_PACK pointing at the unzipped pack. The model is switched off so tests are free and repeatable."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*args):
    out = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), *args],
                         capture_output=True, text=True, env=os.environ.copy())
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="session", autouse=True)
def store(tmp_path_factory):
    if not os.environ.get("R3_PACK"):
        pytest.skip("set R3_PACK to the unzipped data pack")
    os.environ["R3_STORE"] = str(tmp_path_factory.mktemp("store"))
    os.environ["LLM_ENABLED"] = "0"
    for d in range(1, 6):
        run("ingest", "--day", str(d))
    return os.environ["R3_STORE"]
