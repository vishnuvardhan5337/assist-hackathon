"""Score guards: run the ORGANISERS' harness and scorer and fail if any area drops.

1. Real days 1-5 with the official answer keys.
2. Days 1-7 = real days + two simulated hidden days with hand-written keys in the official format
   (tests/hidden_pack.py), so 'Getting better' (needs 6+ days) is measured too.

Thresholds are deliberately a little below today's scores, so a real regression fails
but a harmless wording change does not. The model is off (LLM_ENABLED=0).
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import hidden_pack  # noqa: E402

PACK = os.environ.get("R3_PACK", "")
FLOOR = {"briefs": 0.95, "answers": 0.95, "honesty": 0.95, "memory": 0.95, "privacy": 1.0, "cost": 1.0}


def run_and_score(tmp, pack, days):
    env = os.environ.copy()
    env.update(R3_PACK=str(pack), R3_STORE=str(tmp / "store"), LLM_ENABLED="0")
    out = tmp / "run"
    subprocess.run([sys.executable, os.path.join(pack, "tools", "run_days.py"), "--cmd", f"{sys.executable} main.py",
                    "--days", days, "--out", str(out)], cwd=ROOT, env=env, check=True, capture_output=True, timeout=600)
    subprocess.run([sys.executable, os.path.join(pack, "tools", "score.py"), "--run", str(out), "--days", days],
                   cwd=ROOT, env=env, check=True, capture_output=True, timeout=300)
    return json.loads((out / "score.json").read_text())


def explain(score):
    bad = [i for i in score["items"] if i["score"] < 1 or i["honesty"] < 1 or i["priv"]]
    return "\n".join(f"day {i['day']} {i['kind']} {i['id']}: score {i['score']:.2f} honesty {i['honesty']:.1f} "
                     f"{'; '.join(i['reasons'] + i['pens'] + i['priv'])}" for i in bad[:15])


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return run_and_score(tmp_path_factory.mktemp("real"), PACK, "1-5")


@pytest.fixture(scope="module")
def seven(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("seven")
    pack = hidden_pack.build(PACK, str(tmp / "pack"))
    return run_and_score(tmp, pack, "1-7")


@pytest.mark.parametrize("area", list(FLOOR))
def test_real_days_area_floor(real, area):
    got = real["areas"][area]["score"]
    assert got is not None and got >= FLOOR[area], f"{area} = {got}\n{explain(real)}"


def test_real_days_total_and_no_cap(real):
    assert not real["privacy_capped"] and not real["privacy_leaks"]
    assert real["total"] >= 80, f"total {real['total']}/85\n{explain(real)}"
    assert real["gate"] == 1.0, "usefulness gate closed: right briefs/answers fell under 50%"


@pytest.mark.parametrize("area", list(FLOOR))
def test_simulated_hidden_days_area_floor(seven, area):
    got = seven["areas"][area]["score"]
    assert got is not None and got >= FLOOR[area], f"{area} = {got}\n{explain(seven)}"


def test_simulated_hidden_days_each_day(seven):
    weak = {d: s for d, s in seven["per_day"].items() if s < 0.9}
    assert not weak, f"weak days {weak}\n{explain(seven)}"


def test_getting_better_is_measured_and_not_falling(seven):
    better = seven["areas"]["better"]["score"]
    assert better is not None, "needs 6+ scored days"
    assert better >= 0.5, f"later days score lower than early days ({better})\n{explain(seven)}"


def test_memory_checks_cover_correction_forget_leave_and_preference(seven):
    assert seven["areas"]["memory"]["checks"] >= 6


# ------------------------------------------------------------------ overfitting stress test (reworded days 1-5)
import paraphrase_pack  # noqa: E402


@pytest.fixture(scope="module")
def reworded_a(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("paraA")
    pack, _ = paraphrase_pack.build(PACK, str(tmp / "pack"), "a")
    return run_and_score(tmp, pack, "1-5")


@pytest.fixture(scope="module")
def reworded_b(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("paraB")
    pack, _ = paraphrase_pack.build(PACK, str(tmp / "pack"), "b")
    return run_and_score(tmp, pack, "1-5")


def test_reworded_a_keeps_full_score(reworded_a):
    assert reworded_a["total"] >= 83, f"total {reworded_a['total']}\n{explain(reworded_a)}"


def test_reworded_b_without_model_stays_above_floor(reworded_b):
    # B was a held-out set (first blind score: 64.0/85). After general fixes, rules alone reach ~80.8.
    assert reworded_b["total"] >= 78, f"total {reworded_b['total']}\n{explain(reworded_b)}"


@pytest.mark.parametrize("which", ["a", "b"])
def test_rewording_never_costs_privacy_or_honesty(which, reworded_a, reworded_b):
    s = reworded_a if which == "a" else reworded_b
    assert not s["privacy_leaks"] and s["areas"]["honesty"]["score"] >= 0.99, explain(s)
