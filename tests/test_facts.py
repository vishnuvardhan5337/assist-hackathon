from conftest import run


def brief(person, date):
    return {b["meeting_id"]: b for b in run("brief", "--person", person, "--date", date)}


def test_newest_wins_and_cites_new_source():
    b = brief("p02", "2026-11-05")["ev-nb-me-04"]
    launch = [c for c in b["changed"] if "aunch" in c["text"]][0]
    assert "14 Nov" in launch["text"] and "10 Nov" in launch["text"] and "rwzvbzdv" in launch["sources"]


def test_nothing_new_has_no_filler():
    b = brief("p02", "2026-11-05")["ev-ap-me-04"]
    assert b["status"] == "nothing_new" and b["changed"] == [] and b["question_to_ask"] is None


def test_delivered_promise_is_closed():
    b = brief("p12", "2026-11-06")["ev-nb-ne-05"]
    whats = [p["what"] for p in b["promises"]]
    assert "the revised banner set" not in whats and "the checkout copy" in whats


def test_intern_and_stale_doc_do_not_undo_billing_change():
    out = run("ask", "--person", "p02", "--at", "2026-11-05T18:00:00Z", "--ask", "Who owns billing for Northbeam?")
    assert "Arjun" in out["answer"]["text"] and "rzgt6k79" not in str(out)


def test_same_day_meeting_does_not_shrink_window():
    b = brief("p03", "2026-11-04")["ev-cl-ro-03"]
    assert b["status"] == "brief" and any("UAT" in c["text"] for c in b["changed"])
