from conftest import run


def test_private_note_is_refused_to_others():
    out = run("ask", "--person", "p03", "--at", "2026-11-06T18:00:00Z", "--ask", "What did Meera say about me?")
    assert out["answer"] is None and out["refused"]
    assert "slow" not in str(out).lower() and "approval" not in str(out).lower()


def test_owner_sees_own_private_note():
    out = run("ask", "--person", "p02", "--at", "2026-11-04T18:00:00Z", "--ask", "When is my Dubai trip?")
    assert "rjuznjge" in out["answer"]["sources"]


def test_others_never_hear_about_private_trip():
    out = run("ask", "--person", "p03", "--at", "2026-11-04T18:00:00Z", "--ask", "Is Meera travelling anywhere this month?")
    assert "dubai" not in str(out).lower()


def test_other_company_gets_nothing_from_kitepoint():
    out = run("ask", "--person", "q02", "--at", "2026-11-06T18:00:00Z", "--ask", "Who owns billing for Northbeam?")
    assert "arjun" not in str(out).lower() and "kavya" not in str(out).lower()


def test_forget_removes_it_everywhere():
    run("forget", "--person", "p02", "--what", "Forget what I said about the Dubai trip.")
    out = run("ask", "--person", "p02", "--at", "2026-11-06T18:00:00Z", "--ask", "When is my Dubai trip?")
    assert "dubai" not in str(out).lower() and "rjuznjge" not in str(out)
