"""The model fallback can only add facts that code can check: the quote must be in the record,
the person must exist in the record's company, the date must parse."""
from conftest import run  # noqa: F401  (session store with days 1-5)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from assist import store  # noqa: E402
from assist import extract  # noqa: E402
from assist.world import World  # noqa: E402


def world():
    return World(store.connect())


def test_invented_quote_is_rejected():
    w = world()
    r = w.by_id["rgg1ex2g"]
    fake = {"record": r["id"], "kind": "owner", "topic": "billing", "person": "Kavya", "quote": "Kavya owns all billing forever"}
    assert extract.check(fake, {r["id"]: r}, w) is None


def test_real_quote_is_kept_and_resolved():
    w = world()
    r = w.by_id["rgg1ex2g"]
    f = {"record": r["id"], "kind": "owner", "topic": "billing", "person": "Arjun", "quote": "Arjun takes over Northbeam billing"}
    assert extract.check(f, {r["id"]: r}, w) == {"kind": "owner", "attr": "billing", "value": "p11"}


def test_record_not_sent_is_rejected():
    w = world()
    r = w.by_id["rgg1ex2g"]
    f = {"record": "rnotsent", "kind": "owner", "topic": "billing", "person": "Arjun", "quote": "Arjun takes over Northbeam billing"}
    assert extract.check(f, {r["id"]: r}, w) is None


def test_unknown_person_and_bad_date_are_rejected():
    w = world()
    r = w.by_id["rwzvbzdv"]
    assert extract.check({"record": r["id"], "kind": "owner", "topic": "billing", "person": "Zorro",
                          "quote": "we need to move the launch"}, {r["id"]: r}, w) is None
    assert extract.check({"record": r["id"], "kind": "date", "topic": "launch", "when": "someday",
                          "quote": "we need to move the launch"}, {r["id"]: r}, w) is None


def test_promise_due_is_worked_out_in_author_time():
    w = world()
    r = w.by_id["rebgnfbz"]                      # Meera, 2 Nov
    f = {"record": r["id"], "kind": "promise", "what": "checkout copy", "when": "tomorrow", "quote": "the checkout copy kal tak"}
    assert extract.check(f, {r["id"]: r}, w) == {"kind": "promise", "what": "the checkout copy", "due": "2026-11-03"}


def test_no_model_means_no_calls():
    os.environ["LLM_ENABLED"] = "0"
    assert extract.run(store.connect(), 3, extract.llm.Usage()) == 0


def _fake_rec(w, body):
    r = dict(w.by_id["rgg1ex2g"], id="rfake01", body=body)
    return r


def test_person_handing_over_is_not_the_new_owner():
    w = world()
    r = _fake_rec(w, "Kavya is handing Northbeam billing over to Arjun this week.")
    wrong = {"record": r["id"], "kind": "owner", "topic": "billing for nb", "person": "Kavya", "previous": None,
             "quote": "Kavya is handing Northbeam billing over to Arjun"}
    right = dict(wrong, person="Arjun", previous="Kavya")
    assert extract.check(wrong, {r["id"]: r}, w) is None
    assert extract.check(right, {r["id"]: r}, w) == {"kind": "owner", "attr": "billing", "value": "p11"}


def test_new_equals_previous_is_rejected():
    w = world()
    r = _fake_rec(w, "Going forward Arjun is responsible for Northbeam billing.")
    f = {"record": r["id"], "kind": "owner", "topic": "billing", "person": "Arjun", "previous": "Arjun",
         "quote": "Arjun is responsible for Northbeam billing"}
    assert extract.check(f, {r["id"]: r}, w) is None
