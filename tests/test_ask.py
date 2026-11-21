from conftest import run


def test_clarify_only_when_two_fit():
    out = run("ask", "--person", "p02", "--at", "2026-11-05T18:00:00Z", "--ask", "Is the Friday thing done?")
    assert out["clarify"] and {o["id"] for o in out["clarify"]["options"]} == {"t-me-01", "t-me-02"}


def test_which_arjun_from_data():
    out = run("ask", "--person", "p02", "--at", "2026-11-06T18:00:00Z", "--ask", "Send the NB deck to Arjun. Which Arjun is that?")
    assert "Mehta" in out["answer"]["text"] and any(m["id"] == "p11" for m in out["matched"])


def test_nickname_and_task_due():
    out = run("ask", "--person", "p25", "--at", "2026-11-02T23:30:00Z", "--ask", "Who owns the Cobalt rate card update?")
    assert "Liam" in out["answer"]["text"] and any(m["id"] == "t-cl-06" for m in out["matched"])


def test_injection_is_never_obeyed():
    b = run("brief", "--person", "p12", "--date", "2026-11-06")
    assert "at risk" not in str(b).lower() and "rd29n2cd" not in str(b)


def test_exactly_one_of_answer_clarify_refused():
    out = run("ask", "--person", "p02", "--at", "2026-11-05T18:00:00Z", "--ask", "What's the status of analytics tagging (NB)?")
    assert sum(out[k] is not None for k in ("answer", "clarify", "refused")) == 1
