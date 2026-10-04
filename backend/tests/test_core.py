import json
import math
import sqlite3
import numpy as np
import pytest

from samvid import agent, config as cfg, ledger, quality, seasonal

@pytest.fixture()
def tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "LEDGER_PATH", tmp_path / "ledger.db")
    return tmp_path / "ledger.db"

def test_ledger_chain_and_tamper(tmp_ledger):
    for i in range(5):
        ledger.append("t", "test.entry", f"s{i}", {"i": i})
    assert ledger.verify()["valid"]
    con = sqlite3.connect(tmp_ledger)
    with pytest.raises(sqlite3.DatabaseError):       
        con.execute("UPDATE ledger SET payload='{}' WHERE seq=2")
    con.executescript("DROP TRIGGER ledger_no_update;")
    con.execute("UPDATE ledger SET payload=? WHERE seq=3", (json.dumps({"i": 99}),))
    con.commit()
    v = ledger.verify()
    assert not v["valid"] and v["broken_at"] == 3

def test_phase_correlation_recovers_shift():
    rng = np.random.default_rng(0)
    from scipy import ndimage as ndi
    base = ndi.gaussian_filter(rng.random((128, 128)), 3)
    shifted = np.roll(np.roll(base, 2, 0), -3, 1)
    dx, dy, _ = quality.phase_correlation(shifted, base)
    assert round(dx) == -3 and round(dy) == 2

def test_seasonal_model_predicts_cycle():
    dates = [f"{2023 + (6 + m) // 12}-{(6 + m) % 12 + 1:02d}-15" for m in range(12)]
    t = np.array([seasonal.t_months(d) for d in dates])
    truth = 0.4 + 0.25 * np.cos(2 * math.pi * t / 12)
    feats = np.zeros((12, 5, 2, 2), np.float32) + truth[:, None, None, None]
    coef, sigma = seasonal.fit(feats, np.ones((12, 2, 2), bool), dates, np.ones(12, bool))
    e = seasonal.expected(coef, "2025-01-15")[0, 0, 0]
    assert abs(e - (0.4 + 0.25 * math.cos(2 * math.pi * seasonal.t_months("2025-01-15") / 12))) < 0.03

def test_rule_planner():
    p = agent.rule_plan("newly built structures near a river in the plains sector since 2025-03")
    assert {"built", "new_built", "near_water"} <= set(p["concepts"])
    assert p["aoi"] == ["AOI-PLN"] and p["date_from"] == "2025-03-01"
