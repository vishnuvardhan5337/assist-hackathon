"""Model provider chain: Groq first, a direct OpenAI call as fallback, code-only when both fail. No network:
the OpenAI SDK client is replaced by fakes."""
import sqlite3
from types import SimpleNamespace

import pytest

from assist import llm


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE llm_cache (key TEXT PRIMARY KEY, resp TEXT, in_tok INT, out_tok INT)")
    return con


def _resp(text, i=100, o=20):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=i, completion_tokens=o))


class Fake:
    """Stands in for openai.OpenAI. `script` is a list of responses or exceptions, consumed in order."""
    def __init__(self, script, log, name):
        self.script, self.log, self.name = list(script), log, name
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.log.append((self.name, kw))
        item = self.script.pop(0) if self.script else Exception("500 server error")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def env(monkeypatch):
    for k in ("GROQ_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDERS", "OPENAI_MODEL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_ENABLED", "1")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    llm._dead.clear()
    log, scripts = [], {}
    monkeypatch.setattr(llm, "_client", lambda p: Fake(scripts.get(p["name"], []), log, p["name"]))
    return monkeypatch, log, scripts


def test_no_keys_means_code_only(env):
    assert not llm.enabled()
    assert llm.call_json(_con(), "q", llm.Usage(), budget=3500) is None


def test_groq_answers_first(env):
    mp, log, scripts = env
    mp.setenv("GROQ_API_KEY", "g"); mp.setenv("OPENAI_API_KEY", "o")
    scripts["groq"] = [_resp('{"a": 1}')]
    u = llm.Usage()
    assert llm.call_json(_con(), "q", u, budget=3500) == {"a": 1}
    assert [n for n, _ in log] == ["groq"]
    assert u.cost_usd == 0.0                                   # Groq free tier


def test_openai_direct_fallback_when_groq_fails(env):
    mp, log, scripts = env
    mp.setenv("GROQ_API_KEY", "g"); mp.setenv("OPENAI_API_KEY", "o")
    scripts["groq"] = [Exception("Error code: 401 - invalid_api_key")]
    scripts["openai"] = [_resp('{"owner": "p02"}', 1000, 100)]
    u = llm.Usage()
    assert llm.call_json(_con(), "q", u, budget=3500) == {"owner": "p02"}
    assert [n for n, _ in log] == ["groq", "openai"]
    name, kw = log[-1]
    assert kw["model"] == "gpt-4o-mini" and "max_completion_tokens" in kw and "max_tokens" not in kw
    assert u.cost_usd > 0 and u.input_tokens == 1000


def test_long_rate_limit_falls_back_instead_of_waiting(env):
    mp, log, scripts = env
    mp.setenv("GROQ_API_KEY", "g"); mp.setenv("OPENAI_API_KEY", "o")
    scripts["groq"] = [Exception("429 Rate limit reached. Please try again in 45.2s")]
    scripts["openai"] = [_resp('{"ok": true}')]
    assert llm.call_json(_con(), "q", llm.Usage(), budget=3500) == {"ok": True}
    assert [n for n, _ in log] == ["groq", "openai"]


def test_openai_only(env):
    mp, log, scripts = env
    mp.setenv("OPENAI_API_KEY", "o")
    scripts["openai"] = [_resp('{"x": 2}')]
    assert llm.enabled()
    assert llm.call_json(_con(), "q", llm.Usage(), budget=3500) == {"x": 2}


def test_unsupported_param_is_dropped_and_retried(env):
    mp, log, scripts = env
    mp.setenv("OPENAI_API_KEY", "o"); mp.setenv("OPENAI_MODEL", "gpt-5-mini")
    scripts["openai"] = [Exception("Unsupported value: 'temperature' does not support 0"), _resp('{"y": 3}')]
    assert llm.call_json(_con(), "q", llm.Usage(), budget=3500) == {"y": 3}
    assert "temperature" not in log[-1][1]


def test_all_providers_fail_returns_none(env):
    mp, log, scripts = env
    mp.setenv("GROQ_API_KEY", "g"); mp.setenv("OPENAI_API_KEY", "o")
    scripts["groq"] = [Exception("401 invalid_api_key")]
    scripts["openai"] = [Exception("insufficient_quota")]
    assert llm.call_json(_con(), "q", llm.Usage(), budget=3500) is None


def test_cache_hit_spends_nothing(env):
    mp, log, scripts = env
    mp.setenv("OPENAI_API_KEY", "o")
    scripts["openai"] = [_resp('{"z": 1}')]
    con = _con()
    llm.call_json(con, "same prompt", llm.Usage(), budget=3500)
    u = llm.Usage()
    assert llm.call_json(con, "same prompt", u, budget=3500) == {"z": 1}
    assert len(log) == 1 and u.input_tokens == 0


def test_budget_is_checked_before_any_call(env):
    mp, log, scripts = env
    mp.setenv("OPENAI_API_KEY", "o")
    assert llm.call_json(_con(), "x" * 20000, llm.Usage(), budget=3500) is None
    assert log == []


def test_provider_order_can_be_changed(env):
    mp, log, scripts = env
    mp.setenv("GROQ_API_KEY", "g"); mp.setenv("OPENAI_API_KEY", "o"); mp.setenv("LLM_PROVIDERS", "openai,groq")
    scripts["openai"] = [_resp('{"o": 1}')]
    llm.call_json(_con(), "q", llm.Usage(), budget=3500)
    assert log[0][0] == "openai"


def test_dead_provider_is_skipped_on_later_calls(env):
    mp, log, scripts = env
    mp.setenv("GROQ_API_KEY", "g"); mp.setenv("OPENAI_API_KEY", "o")
    scripts["groq"] = [Exception("Connection error.")]
    scripts["openai"] = [_resp('{"a": 1}'), _resp('{"b": 2}')]
    con = _con()
    llm.call_json(con, "q1", llm.Usage(), budget=3500)
    llm.call_json(con, "q2", llm.Usage(), budget=3500)
    assert [n for n, _ in log] == ["groq", "openai", "openai"]
