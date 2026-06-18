"""
ai_engine.py
The "AI Decision Engine" referenced throughout the spec. Two scikit-learn
models provide predictive intelligence (delay risk, lost-baggage risk),
and a rule-based recommender continuously scans live airport state to
produce actionable, human-readable recommendations across every module.
"""

import random
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression


class AIEngine:
    def __init__(self, seed=42):
        self.rng = np.random.default_rng(seed)
        self.delay_model = self._train_delay_model()
        self.baggage_model = self._train_baggage_model()

    # ------------------------------------------------------------------ #
    # Model training (synthetic historical data — stands in for the
    # months of operational logs a real airport would train on)
    # ------------------------------------------------------------------ #
    def _train_delay_model(self, n=600):
        weather_impact = self.rng.uniform(0.02, 0.7, n)
        checkin_queue = self.rng.integers(0, 80, n)
        security_queue = self.rng.integers(0, 60, n)
        gate_conflict = self.rng.integers(0, 2, n)
        maintenance_flag = self.rng.integers(0, 2, n)

        noise = self.rng.normal(0, 6, n)
        delay = (
            5
            + weather_impact * 70
            + checkin_queue * 0.25
            + security_queue * 0.35
            + gate_conflict * 18
            + maintenance_flag * 35
            + noise
        )
        delay = np.clip(delay, 0, None)

        X = np.column_stack([weather_impact, checkin_queue, security_queue, gate_conflict, maintenance_flag])
        model = RandomForestRegressor(n_estimators=80, max_depth=6, random_state=42)
        model.fit(X, delay)
        return model

    def _train_baggage_model(self, n=600):
        sorting_congestion = self.rng.uniform(0, 1, n)
        screening_backlog = self.rng.uniform(0, 1, n)
        international = self.rng.integers(0, 2, n)
        weather_impact = self.rng.uniform(0.02, 0.7, n)

        score = (
            sorting_congestion * 2.5
            + screening_backlog * 2.0
            + international * 0.6
            + weather_impact * 1.2
            - 2.8
        )
        prob = 1 / (1 + np.exp(-score))
        labels = (self.rng.uniform(0, 1, n) < prob).astype(int)

        X = np.column_stack([sorting_congestion, screening_backlog, international, weather_impact])
        model = LogisticRegression()
        model.fit(X, labels)
        return model

    # ------------------------------------------------------------------ #
    # Predictions consumed by the simulation engine + dashboard
    # ------------------------------------------------------------------ #
    def predict_flight_delay(self, conn, flight_id):
        weather = conn.execute("SELECT impact_factor FROM weather ORDER BY id DESC LIMIT 1").fetchone()
        impact = weather["impact_factor"] if weather else 0.05
        checkin_q = conn.execute(
            "SELECT COALESCE(SUM(queue_length),0) c FROM checkin_counters"
        ).fetchone()["c"]
        security_q = conn.execute(
            "SELECT COALESCE(SUM(queue_length),0) c FROM security_checkpoints"
        ).fetchone()["c"]
        flight = conn.execute("SELECT gate_id, maintenance_ok FROM flights WHERE id=?", (flight_id,)).fetchone()
        gate_conflict = 0 if (flight and flight["gate_id"]) else 1
        maintenance_flag = 0 if (flight and flight["maintenance_ok"]) else 1

        X = np.array([[impact, checkin_q, security_q, gate_conflict, maintenance_flag]])
        pred = float(self.delay_model.predict(X)[0])
        return round(pred, 1)

    def predict_baggage_lost_risk(self, conn, flight_id):
        sorting = conn.execute("SELECT COUNT(*) c FROM baggage WHERE flight_id=? AND status='SORTING'",
                                (flight_id,)).fetchone()["c"]
        screening = conn.execute("SELECT COUNT(*) c FROM baggage WHERE flight_id=? AND status='SCREENING'",
                                  (flight_id,)).fetchone()["c"]
        flight = conn.execute("SELECT international FROM flights WHERE id=?", (flight_id,)).fetchone()
        weather = conn.execute("SELECT impact_factor FROM weather ORDER BY id DESC LIMIT 1").fetchone()
        impact = weather["impact_factor"] if weather else 0.05

        sorting_congestion = min(1.0, sorting / 15)
        screening_backlog = min(1.0, screening / 15)
        international = int(flight["international"]) if flight else 0

        X = np.array([[sorting_congestion, screening_backlog, international, impact]])
        prob = float(self.baggage_model.predict_proba(X)[0][1])
        return round(min(0.05, max(0.003, prob * 0.1)), 3)

    # ------------------------------------------------------------------ #
    # Rule-based recommendation engine — scans every module each refresh
    # ------------------------------------------------------------------ #
    def generate_recommendations(self, conn):
        recs = []

        weather = conn.execute("SELECT * FROM weather ORDER BY id DESC LIMIT 1").fetchone()
        if weather and weather["impact_factor"] > 0.4:
            recs.append(("WEATHER", "HIGH",
                         f"{weather['condition']} conditions (impact {weather['impact_factor']:.2f}) — "
                         f"recommend ground-delay program and reduced taxi speed."))

        for method in ["MANUAL", "KIOSK", "PREMIUM"]:
            row = conn.execute(
                "SELECT SUM(queue_length) q, COUNT(*) n, SUM(status='OPEN') open_n "
                "FROM checkin_counters WHERE counter_type=?", (method,)
            ).fetchone()
            if row["q"] and row["open_n"] and row["q"] / max(1, row["open_n"]) > 12:
                recs.append(("CHECK-IN", "MEDIUM",
                             f"{method} queue averaging {row['q']//max(1,row['open_n'])} pax/counter — "
                             f"recommend opening an additional {method.lower()} counter."))

        sec = conn.execute("SELECT AVG(queue_length) q, SUM(status='OPEN') open_n FROM security_checkpoints").fetchone()
        if sec["q"] and sec["q"] > 20:
            recs.append(("SECURITY", "HIGH",
                         f"Average security queue at {int(sec['q'])} — recommend additional staffing or opening a closed lane."))
        if sec["open_n"] is not None and sec["open_n"] < 2:
            recs.append(("SECURITY", "HIGH", "Fewer than 2 checkpoints open — staffing shortage risk."))

        gate_wait = conn.execute(
            "SELECT COUNT(*) c FROM flights WHERE status='CHECKIN_OPEN' AND gate_id IS NULL"
        ).fetchone()["c"]
        if gate_wait > 0:
            recs.append(("GATES", "MEDIUM", f"{gate_wait} flight(s) waiting on gate assignment — "
                                             f"recommend expediting turnaround at occupied gates."))

        runway_q = conn.execute("SELECT COUNT(*) c FROM flights WHERE status='RUNWAY_QUEUE'").fetchone()["c"]
        if runway_q > 2:
            recs.append(("RUNWAY", "HIGH", f"{runway_q} flights queued for runway access — "
                                            f"recommend dynamic re-sequencing to maximize throughput."))

        risky_bags = conn.execute("SELECT COUNT(*) c FROM baggage WHERE lost_risk > 0.15 AND lost=0").fetchone()["c"]
        if risky_bags > 0:
            recs.append(("BAGGAGE", "MEDIUM", f"AI flags {risky_bags} bag(s) at elevated mishandling risk — "
                                               f"recommend manual verification at sorting."))

        active_incidents = conn.execute("SELECT COUNT(*) c FROM incidents WHERE status='ACTIVE'").fetchone()["c"]
        if active_incidents > 0:
            recs.append(("INCIDENTS", "HIGH", f"{active_incidents} unresolved incident(s) require attention."))

        busy_resources = conn.execute(
            "SELECT type, COUNT(*) c FROM resources WHERE status='BUSY' GROUP BY type HAVING c > 3"
        ).fetchall()
        for r in busy_resources:
            recs.append(("RESOURCES", "LOW", f"{r['type']} utilization high ({r['c']} busy) — "
                                              f"recommend reallocating standby staff."))

        if not recs:
            recs.append(("SYSTEM", "LOW", "All modules operating within normal parameters."))
        return recs
