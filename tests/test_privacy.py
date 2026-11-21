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


# ---------------------------------------------------------------- feedback corrections are private to their author
def _with_feedback(monkeypatch, fb):
    import assist.world as W
    orig = W.World.__init__

    def init(self, con):
        orig(self, con)
        self.feedback = list(self.feedback) + [fb]
    monkeypatch.setattr(W.World, "__init__", init)


def _ask(person, q):
    from assist import ask, store
    from assist.world import World
    return ask.run(World(store.connect()), person, "2026-11-06T12:00:00Z", q)


def test_unsupported_correction_reaches_only_its_author(monkeypatch):
    _with_feedback(monkeypatch, {"type": "correction", "person": "p02", "day": 3,
                                 "words": "No, Nandini handles Northbeam invoicing now, not Kavya."})
    mine = _ask("p02", "Who handles invoicing for Northbeam?")
    other = _ask("p03", "Who handles invoicing for Northbeam?")
    assert "Nandini" in str(mine["answer"])                  # the person who corrected it sees their correction
    assert "Nandini" not in str(other)                       # nobody else hears a fact no record of theirs supports


def test_supported_correction_reaches_others_with_a_visible_source(monkeypatch):
    from assist import memory, store
    from assist.world import World
    _with_feedback(monkeypatch, {"type": "correction", "person": "p02", "day": 3,
                                 "words": "No, Arjun handles Northbeam billing now, not Kavya."})
    w = World(store.connect())
    cs = memory.corrections(w, None, viewer="p03")
    assert cs, "Arjun owning billing is in a team chat p03 can see"
    for c in cs:
        assert c["event"]["sources"] and all(w.can_see("p03", w.by_id[s]) for s in c["event"]["sources"])
