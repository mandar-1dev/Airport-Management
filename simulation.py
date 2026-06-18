"""
simulation.py
The Digital Twin's simulation engine. Every call to `advance_time()` ticks
the airport forward by a fixed number of minutes and pushes every flight,
passenger and bag through its lifecycle state machine, while keeping
weather, gates, runways, counters, security, resources and incidents
fully interconnected (per the "Full Integration Requirement").
"""

import random
import string
from datetime import datetime, timedelta

import database as db

TICK_MINUTES = 10

FLIGHT_STATES = [
    "SCHEDULED", "CHECKIN_OPEN", "BOARDING_PREP", "BOARDING", "READY_FOR_PUSHBACK",
    "PUSHBACK", "TAXI_OUT", "RUNWAY_QUEUE", "TAKEOFF", "CLIMB", "CRUISE", "DESCENT",
    "LANDING", "TAXI_IN", "AT_GATE_ARRIVED", "DEBOARDING", "CLEANING", "TURNAROUND",
    "COMPLETED",
]

WEATHER_CONDITIONS = [
    ("CLEAR", 0.03, 10.0, 0.55),
    ("CLOUDY", 0.10, 8.0, 0.20),
    ("RAIN", 0.30, 5.0, 0.15),
    ("STORM", 0.65, 2.0, 0.06),
    ("FOG", 0.55, 1.0, 0.04),
]

INCIDENT_TYPES = [
    "MEDICAL_EMERGENCY", "ENGINE_FAILURE", "BIRD_STRIKE", "FIRE_INCIDENT",
    "SECURITY_BREACH", "LOST_BAGGAGE", "WEATHER_DISRUPTION", "FUEL_SHORTAGE",
    "GATE_CONFLICT", "RUNWAY_OBSTRUCTION",
]


def _now(conn):
    row = conn.execute("SELECT sim_time_value, tick_count FROM sim_clock WHERE id=1").fetchone()
    return datetime.fromisoformat(row["sim_time_value"]), row["tick_count"]


def _set_now(conn, now, tick_count):
    conn.execute("UPDATE sim_clock SET sim_time_value=?, tick_count=? WHERE id=1",
                 (now.isoformat(), tick_count))


def _log(conn, now, module, message):
    db.log_event(conn, module, message, now.strftime("%H:%M"))


# --------------------------------------------------------------------------- #
# WEATHER
# --------------------------------------------------------------------------- #
def update_weather(conn, now):
    """Weather drifts probabilistically and its impact_factor feeds directly
    into flight delay probability, taxi speed and runway availability."""
    if random.random() < 0.18:  # weather changes ~18% of ticks
        cond_row = random.choices(
            WEATHER_CONDITIONS, weights=[w[3] for w in WEATHER_CONDITIONS]
        )[0]
        condition = cond_row[0]
        impact_factor = cond_row[1] + random.uniform(-0.03, 0.03)
        visibility = cond_row[2] + random.uniform(-1, 1)
        wind_speed = random.uniform(5, 45)
        conn.execute(
            "INSERT INTO weather (timestamp, condition, visibility_km, wind_speed_kmh, impact_factor) "
            "VALUES (?,?,?,?,?)",
            (now.isoformat(), condition, max(0.2, visibility), wind_speed, max(0.02, impact_factor)),
        )
        _log(conn, now, "WEATHER", f"Condition changed to {condition} (impact={impact_factor:.2f})")
        if condition in ("STORM", "FOG"):
            spawn_incident(conn, now, forced_type="WEATHER_DISRUPTION")


def get_weather_impact(conn):
    row = conn.execute("SELECT * FROM weather ORDER BY id DESC LIMIT 1").fetchone()
    return row


# --------------------------------------------------------------------------- #
# CHECK-IN  /  SECURITY  /  IMMIGRATION  (passenger journey, queue-driven)
# --------------------------------------------------------------------------- #
def process_checkin(conn, now):
    weather = get_weather_impact(conn)
    impact = weather["impact_factor"] if weather else 0.05

    flights = conn.execute(
        "SELECT id, international FROM flights WHERE status NOT IN ('SCHEDULED')"
    ).fetchall()
    open_flight_ids = {f["id"] for f in flights}
    if not open_flight_ids:
        return

    # passengers trickle into the check-in queue gradually (not all at once on open)
    for fid in open_flight_ids:
        booked = conn.execute(
            "SELECT id FROM passengers WHERE flight_id=? AND status='BOOKED' LIMIT 20", (fid,)
        ).fetchall()
        for p in booked:
            conn.execute("UPDATE passengers SET status='QUEUED_CHECKIN' WHERE id=?", (p["id"],))

    for method, table_throughput in [("MOBILE", None), ("MANUAL", 6), ("KIOSK", 8), ("PREMIUM", 10)]:
        if method == "MOBILE":
            # mobile check-in needs no physical counter, just self-service speed
            cap = 18
            rows = conn.execute(
                "SELECT id, flight_id, bag_count FROM passengers "
                "WHERE status='QUEUED_CHECKIN' AND checkin_method='MOBILE' LIMIT ?",
                (cap,),
            ).fetchall()
        else:
            open_counters = conn.execute(
                "SELECT COUNT(*) c FROM checkin_counters WHERE counter_type=? AND status='OPEN'",
                (method,),
            ).fetchone()["c"]
            cap = int(open_counters * table_throughput * max(0.4, 1 - impact))
            rows = conn.execute(
                "SELECT id, flight_id, bag_count FROM passengers "
                "WHERE status='QUEUED_CHECKIN' AND checkin_method=? LIMIT ?",
                (method, cap),
            ).fetchall()

        for r in rows:
            conn.execute("UPDATE passengers SET status='CHECKED_IN' WHERE id=?", (r["id"],))
            if r["bag_count"] > 0:
                conn.execute(
                    "UPDATE baggage SET status='CONVEYOR', location='Terminal Conveyor' "
                    "WHERE passenger_id=? AND status='PENDING'",
                    (r["id"],),
                )

        # update queue length snapshot for the dashboard (per type, written to first row)
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM passengers WHERE status='QUEUED_CHECKIN' AND checkin_method=?",
            (method,),
        ).fetchone()["c"]
        if method != "MOBILE":
            conn.execute(
                "UPDATE checkin_counters SET queue_length=? WHERE counter_type=?",
                (remaining, method),
            )


def process_security_and_immigration(conn, now):
    weather = get_weather_impact(conn)
    impact = weather["impact_factor"] if weather else 0.05

    # move checked-in passengers into the security queue
    conn.execute("UPDATE passengers SET status='QUEUED_SECURITY' WHERE status='CHECKED_IN'")

    checkpoints = conn.execute("SELECT * FROM security_checkpoints").fetchall()
    open_checkpoints = [c for c in checkpoints if c["status"] == "OPEN"]
    per_checkpoint_cap = int(18 * max(0.4, 1 - impact))
    total_cap = len(open_checkpoints) * per_checkpoint_cap

    rows = conn.execute(
        "SELECT id, flight_id FROM passengers WHERE status='QUEUED_SECURITY' LIMIT ?",
        (total_cap,),
    ).fetchall()
    for r in rows:
        flight = conn.execute("SELECT international FROM flights WHERE id=?", (r["flight_id"],)).fetchone()
        # tiny chance security flags a suspicious item -> short re-screen delay (handled by staying queued)
        if random.random() < 0.01:
            continue
        next_status = "QUEUED_IMMIGRATION_DEP" if flight["international"] else "GATE_AREA"
        conn.execute("UPDATE passengers SET status=? WHERE id=?", (next_status, r["id"]))

    remaining_q = conn.execute(
        "SELECT COUNT(*) c FROM passengers WHERE status='QUEUED_SECURITY'"
    ).fetchone()["c"]
    avg_q = remaining_q // max(1, len(checkpoints))
    for c in checkpoints:
        alert = None
        if avg_q > 25:
            alert = "Excessive queue – recommend opening additional lane"
        elif len(open_checkpoints) < 2:
            alert = "Staffing shortage risk"
        conn.execute(
            "UPDATE security_checkpoints SET queue_length=?, alert=? WHERE id=?",
            (avg_q, alert, c["id"]),
        )
    if avg_q > 30 and random.random() < 0.3:
        log_queue_alert(conn, now, "SECURITY",
                         f"Excessive security queue detected by AI monitoring (avg {avg_q}/checkpoint)",
                         "Additional lane opened / staff reallocated by AI.")

    # immigration (international departures)
    officers = conn.execute(
        "SELECT COUNT(*) c FROM resources WHERE type='IMMIGRATION_OFFICER' AND status='AVAILABLE'"
    ).fetchone()["c"]
    imm_cap = int(officers * 4 * max(0.3, 1 - impact))
    imm_rows = conn.execute(
        "SELECT id FROM passengers WHERE status='QUEUED_IMMIGRATION_DEP' LIMIT ?", (imm_cap,)
    ).fetchall()
    for r in imm_rows:
        conn.execute("UPDATE passengers SET status='GATE_AREA' WHERE id=?", (r["id"],))

    # arrival-side immigration / customs / baggage claim / exit for passengers already IN_FLIGHT->ARRIVED
    arr_rows = conn.execute("SELECT id, flight_id FROM passengers WHERE status='ARRIVED'").fetchall()
    for r in arr_rows:
        flight = conn.execute("SELECT international FROM flights WHERE id=?", (r["flight_id"],)).fetchone()
        nxt = "QUEUED_IMMIGRATION_ARR" if flight["international"] else "BAGGAGE_CLAIM"
        conn.execute("UPDATE passengers SET status=? WHERE id=?", (nxt, r["id"]))

    imm_arr_rows = conn.execute(
        "SELECT id FROM passengers WHERE status='QUEUED_IMMIGRATION_ARR' LIMIT ?", (imm_cap,)
    ).fetchall()
    for r in imm_arr_rows:
        conn.execute("UPDATE passengers SET status='BAGGAGE_CLAIM' WHERE id=?", (r["id"],))

    claim_rows = conn.execute("SELECT id, flight_id FROM passengers WHERE status='BAGGAGE_CLAIM'").fetchall()
    for r in claim_rows:
        bag = conn.execute(
            "SELECT status FROM baggage WHERE passenger_id=? LIMIT 1", (r["id"],)
        ).fetchone()
        if bag is None or bag["status"] in ("CAROUSEL", "CLAIMED", "LOST") or random.random() < 0.5:
            conn.execute(
                "UPDATE baggage SET status='CLAIMED', location='Collected' "
                "WHERE passenger_id=? AND status='CAROUSEL'",
                (r["id"],),
            )
            flight = conn.execute("SELECT international FROM flights WHERE id=?", (r["flight_id"],)).fetchone()
            nxt = "CUSTOMS" if flight["international"] else "EXITED"
            conn.execute("UPDATE passengers SET status=? WHERE id=?", (nxt, r["id"]))

    cust_rows = conn.execute("SELECT id FROM passengers WHERE status='CUSTOMS'").fetchall()
    for r in cust_rows:
        if random.random() < 0.6:
            conn.execute("UPDATE passengers SET status='EXITED' WHERE id=?", (r["id"],))


# --------------------------------------------------------------------------- #
# BAGGAGE HANDLING LIFECYCLE
# --------------------------------------------------------------------------- #
def process_baggage(conn, now, ai_engine=None):
    # CONVEYOR -> SORTING -> SCREENING -> LOADED (waits for flight BOARDING/READY_FOR_PUSHBACK)
    for src, dst in [("CONVEYOR", "SORTING"), ("SORTING", "SCREENING")]:
        rows = conn.execute("SELECT id FROM baggage WHERE status=?", (src,)).fetchall()
        for r in rows:
            if random.random() < 0.7:
                conn.execute("UPDATE baggage SET status=?, location=? WHERE id=?",
                             (dst, dst.title(), r["id"]))

    # screened bags load onto aircraft once the flight is boarding/ready
    rows = conn.execute(
        """SELECT b.id, b.flight_id FROM baggage b
           JOIN flights f ON f.id=b.flight_id
           WHERE b.status='SCREENING' AND f.status IN ('BOARDING','READY_FOR_PUSHBACK')"""
    ).fetchall()
    for r in rows:
        risk = 0.02
        if ai_engine is not None:
            risk = ai_engine.predict_baggage_lost_risk(conn, r["flight_id"])
        lost = 1 if random.random() < risk else 0
        status = "LOST" if lost else "LOADED"
        conn.execute(
            "UPDATE baggage SET status=?, location=?, lost_risk=?, lost=? WHERE id=?",
            (status, "Cargo Hold" if not lost else "MISSING", risk, lost, r["id"]),
        )
        if lost:
            _log(conn, now, "BAGGAGE", f"AI flagged bag {r['id']} on flight #{r['flight_id']} as lost in transit")

    # bags travel with the aircraft: once flight lands -> unload -> arrival conveyor -> carousel
    rows = conn.execute(
        """SELECT b.id FROM baggage b JOIN flights f ON f.id=b.flight_id
           WHERE b.status='LOADED' AND f.status IN ('TAXI_IN','AT_GATE_ARRIVED')"""
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE baggage SET status='UNLOADED', location='Arrival Belt' WHERE id=?", (r["id"],))

    rows = conn.execute("SELECT id FROM baggage WHERE status='UNLOADED'").fetchall()
    for r in rows:
        if random.random() < 0.6:
            conn.execute("UPDATE baggage SET status='CAROUSEL', location='Carousel' WHERE id=?", (r["id"],))


# --------------------------------------------------------------------------- #
# GATES / BOARDING / RUNWAYS / FLIGHT STATE MACHINE
# --------------------------------------------------------------------------- #
def _assign_gate(conn, flight_id):
    gate = conn.execute("SELECT id FROM gates WHERE status='AVAILABLE' LIMIT 1").fetchone()
    if gate:
        conn.execute("UPDATE gates SET status='OCCUPIED', flight_id=? WHERE id=?", (flight_id, gate["id"]))
        conn.execute("UPDATE flights SET gate_id=? WHERE id=?", (gate["id"], flight_id))
        return True
    return False


def _assign_runway(conn, flight_id):
    runway = conn.execute("SELECT id FROM runways WHERE status='AVAILABLE' LIMIT 1").fetchone()
    if runway:
        conn.execute("UPDATE runways SET status='OCCUPIED', flight_id=? WHERE id=?", (flight_id, runway["id"]))
        conn.execute("UPDATE flights SET runway_id=? WHERE id=?", (runway["id"], flight_id))
        return True
    return False


def progress_flights(conn, now, ai_engine=None):
    weather = get_weather_impact(conn)
    impact = weather["impact_factor"] if weather else 0.05
    flights = conn.execute("SELECT * FROM flights WHERE status != 'COMPLETED'").fetchall()
    # priority: emergencies get airborne/runway priority automatically
    flights = sorted(flights, key=lambda f: (-f["priority"], f["scheduled_departure"]))

    for f in flights:
        fid = f["id"]
        status = f["status"]
        sched = datetime.fromisoformat(f["scheduled_departure"])

        if status == "SCHEDULED":
            if now >= sched - timedelta(minutes=150):
                conn.execute("UPDATE flights SET status='CHECKIN_OPEN' WHERE id=?", (fid,))
                _log(conn, now, "FLIGHT", f"Check-in opened for {f['flight_number']}")

        elif status == "CHECKIN_OPEN":
            if now >= sched - timedelta(minutes=60) and f["maintenance_ok"]:
                if _assign_gate(conn, fid):
                    conn.execute("UPDATE flights SET status='BOARDING_PREP' WHERE id=?", (fid,))
                    _log(conn, now, "GATE", f"Gate assigned to {f['flight_number']}")
                else:
                    conn.execute(
                        "UPDATE flights SET delay_minutes=delay_minutes+? WHERE id=?",
                        (TICK_MINUTES, fid),
                    )
                    spawn_incident(conn, now, forced_type="GATE_CONFLICT",
                                    description_override=f"No gate available for {f['flight_number']}")
            elif not f["maintenance_ok"]:
                _try_resolve_maintenance(conn, now, f)

        elif status == "BOARDING_PREP":
            if now >= sched - timedelta(minutes=40):
                conn.execute("UPDATE gates SET status='BOARDING' WHERE id=?", (f["gate_id"],))
                conn.execute("UPDATE flights SET status='BOARDING' WHERE id=?", (fid,))
                _log(conn, now, "BOARDING", f"Boarding started for {f['flight_number']}")

        elif status == "BOARDING":
            cap = max(35, int(60 * (1 - impact)))
            board_now = conn.execute(
                "SELECT id FROM passengers WHERE flight_id=? AND status='GATE_AREA' LIMIT ?",
                (fid, cap),
            ).fetchall()
            for p in board_now:
                conn.execute("UPDATE passengers SET status='BOARDED' WHERE id=?", (p["id"],))
            boarded = conn.execute(
                "SELECT COUNT(*) c FROM passengers WHERE flight_id=? AND status IN ('BOARDED','IN_FLIGHT')",
                (fid,),
            ).fetchone()["c"]
            progress = boarded / max(1, f["passengers_total"])
            conn.execute("UPDATE flights SET boarding_progress=? WHERE id=?", (progress, fid))
            if progress >= 0.95 or (now >= sched + timedelta(minutes=50)):
                conn.execute("UPDATE flights SET status='READY_FOR_PUSHBACK' WHERE id=?", (fid,))
                _log(conn, now, "BOARDING", f"Boarding complete for {f['flight_number']}")

        elif status == "READY_FOR_PUSHBACK":
            pushback_truck = conn.execute(
                "SELECT id FROM ground_vehicles WHERE type='PUSHBACK_TRUCK' AND status='IDLE' LIMIT 1"
            ).fetchone()
            if pushback_truck and random.random() < (0.7 - impact * 0.4):
                conn.execute("UPDATE ground_vehicles SET status='IN_USE', aircraft_id=? WHERE id=?",
                             (f["aircraft_id"], pushback_truck["id"]))
                conn.execute("UPDATE gates SET status='AVAILABLE', flight_id=NULL WHERE id=?", (f["gate_id"],))
                conn.execute(
                    "UPDATE flights SET status='PUSHBACK', actual_departure=? WHERE id=?",
                    (now.isoformat(), fid),
                )
                # any passenger who never made it to boarding has missed the flight;
                # this drains the journey funnel instead of leaving stale queues forever
                conn.execute(
                    """UPDATE passengers SET status='MISSED_FLIGHT' WHERE flight_id=? AND status NOT IN
                       ('BOARDED','IN_FLIGHT','ARRIVED','QUEUED_IMMIGRATION_ARR','BAGGAGE_CLAIM','CUSTOMS','EXITED')""",
                    (fid,),
                )
                conn.execute(
                    "UPDATE baggage SET status='OFFLOADED', location='Held at origin' "
                    "WHERE flight_id=? AND status NOT IN ('LOADED','LOST')",
                    (fid,),
                )
            else:
                conn.execute("UPDATE flights SET delay_minutes=delay_minutes+? WHERE id=?", (TICK_MINUTES, fid))

        elif status == "PUSHBACK":
            if random.random() < (0.75 - impact * 0.3):
                conn.execute("UPDATE flights SET status='TAXI_OUT' WHERE id=?", (fid,))

        elif status == "TAXI_OUT":
            if random.random() < (0.7 - impact * 0.4):
                conn.execute("UPDATE flights SET status='RUNWAY_QUEUE' WHERE id=?", (fid,))

        elif status == "RUNWAY_QUEUE":
            if _assign_runway(conn, fid):
                conn.execute("UPDATE flights SET status='TAKEOFF' WHERE id=?", (fid,))
                _log(conn, now, "RUNWAY", f"{f['flight_number']} cleared for takeoff")
            else:
                conn.execute("UPDATE flights SET delay_minutes=delay_minutes+? WHERE id=?", (TICK_MINUTES, fid))

        elif status == "TAKEOFF":
            if random.random() < (0.8 - impact * 0.5):
                conn.execute("UPDATE runways SET status='AVAILABLE', flight_id=NULL WHERE id=?", (f["runway_id"],))
                conn.execute(
                    "UPDATE passengers SET status='IN_FLIGHT' WHERE flight_id=? AND status='BOARDED'", (fid,)
                )
                conn.execute("UPDATE flights SET status='CLIMB' WHERE id=?", (fid,))

        elif status == "CLIMB":
            if random.random() < 0.8:
                conn.execute("UPDATE flights SET status='CRUISE' WHERE id=?", (fid,))

        elif status == "CRUISE":
            if random.random() < 0.5:
                conn.execute("UPDATE flights SET status='DESCENT' WHERE id=?", (fid,))

        elif status == "DESCENT":
            if random.random() < 0.7:
                conn.execute("UPDATE flights SET status='LANDING' WHERE id=?", (fid,))

        elif status == "LANDING":
            if random.random() < (0.75 - impact * 0.4):
                conn.execute(
                    "UPDATE passengers SET status='ARRIVED' WHERE flight_id=? AND status='IN_FLIGHT'", (fid,)
                )
                conn.execute("UPDATE flights SET status='TAXI_IN' WHERE id=?", (fid,))

        elif status == "TAXI_IN":
            if random.random() < (0.75 - impact * 0.3) and _assign_gate(conn, fid):
                conn.execute("UPDATE flights SET status='AT_GATE_ARRIVED' WHERE id=?", (fid,))

        elif status == "AT_GATE_ARRIVED":
            conn.execute("UPDATE flights SET status='DEBOARDING' WHERE id=?", (fid,))

        elif status == "DEBOARDING":
            if random.random() < 0.8:
                conn.execute("UPDATE gates SET status='CLEANING' WHERE id=?", (f["gate_id"],))
                conn.execute("UPDATE flights SET status='CLEANING' WHERE id=?", (fid,))

        elif status == "CLEANING":
            if random.random() < 0.75:
                conn.execute("UPDATE flights SET status='TURNAROUND' WHERE id=?", (fid,))

        elif status == "TURNAROUND":
            if random.random() < 0.7:
                conn.execute("UPDATE gates SET status='AVAILABLE', flight_id=NULL WHERE id=?", (f["gate_id"],))
                conn.execute("UPDATE flights SET status='COMPLETED' WHERE id=?", (fid,))
                _log(conn, now, "FLIGHT", f"{f['flight_number']} turnaround complete – cycle finished")


def _try_resolve_maintenance(conn, now, flight):
    engineer = conn.execute(
        "SELECT id FROM resources WHERE type='MAINTENANCE_ENGINEER' AND status='AVAILABLE' LIMIT 1"
    ).fetchone()
    if engineer and random.random() < 0.5:
        conn.execute("UPDATE flights SET maintenance_ok=1 WHERE id=?", (flight["id"],))
        _log(conn, now, "MAINTENANCE", f"Maintenance cleared for {flight['flight_number']}")
    else:
        conn.execute("UPDATE flights SET delay_minutes=delay_minutes+? WHERE id=?", (TICK_MINUTES, flight["id"]))


# --------------------------------------------------------------------------- #
# INCIDENT SIMULATION  +  AI AUTO-RESPONSE
# --------------------------------------------------------------------------- #
def log_queue_alert(conn, now, module, description, response):
    """A correctly-labelled congestion alert (distinct from spawn_incident's
    random scenario generator, so description and response never mismatch)."""
    conn.execute(
        "INSERT INTO incidents (type, status, module, timestamp, description, severity, response) "
        "VALUES ('QUEUE_CONGESTION','RESOLVED',?,?,?,?,?)",
        (module, now.strftime("%H:%M"), description, "MEDIUM", response),
    )
    _log(conn, now, module, f"{description} -> {response}")


def spawn_incident(conn, now, forced_type=None, description_override=None):
    itype = forced_type or random.choice(INCIDENT_TYPES)
    flights = conn.execute("SELECT * FROM flights WHERE status NOT IN ('COMPLETED')").fetchall()
    target_flight = random.choice(flights) if flights else None
    severity = random.choices(["LOW", "MEDIUM", "HIGH"], weights=[0.5, 0.35, 0.15])[0]
    response = "No automated action required."

    if itype == "MEDICAL_EMERGENCY" and target_flight:
        team = conn.execute("SELECT id FROM resources WHERE type='MEDICAL_TEAM' AND status='AVAILABLE' LIMIT 1").fetchone()
        if team:
            conn.execute("UPDATE resources SET status='BUSY', assigned_to=? WHERE id=?",
                         (target_flight["flight_number"], team["id"]))
            response = f"Medical team dispatched to gate for {target_flight['flight_number']}."
        conn.execute("UPDATE flights SET delay_minutes=delay_minutes+15 WHERE id=?", (target_flight["id"],))

    elif itype == "ENGINE_FAILURE" and target_flight:
        conn.execute("UPDATE aircraft SET status='MAINTENANCE' WHERE id=?", (target_flight["aircraft_id"],))
        spare = conn.execute(
            "SELECT id FROM aircraft WHERE status='PARKED' AND id NOT IN (SELECT aircraft_id FROM flights WHERE status NOT IN ('COMPLETED'))"
        ).fetchone()
        if spare:
            conn.execute("UPDATE flights SET aircraft_id=?, maintenance_ok=1 WHERE id=?",
                         (spare["id"], target_flight["id"]))
            response = f"Spare aircraft reassigned to {target_flight['flight_number']}."
        else:
            conn.execute("UPDATE flights SET maintenance_ok=0, delay_minutes=delay_minutes+45 WHERE id=?",
                         (target_flight["id"],))
            response = "No spare aircraft available – flight held for engineering inspection."

    elif itype == "BIRD_STRIKE":
        runway = conn.execute("SELECT id, name FROM runways WHERE status!='CLOSED' LIMIT 1").fetchone()
        if runway:
            conn.execute("UPDATE runways SET status='MAINTENANCE' WHERE id=?", (runway["id"],))
            response = f"{runway['name']} closed for inspection; traffic rerouted to remaining runways."

    elif itype == "FIRE_INCIDENT":
        team = conn.execute("SELECT id FROM resources WHERE type='FIRE_RESPONSE' AND status='AVAILABLE' LIMIT 1").fetchone()
        if team:
            conn.execute("UPDATE resources SET status='BUSY' WHERE id=?", (team["id"],))
            response = "Fire response team dispatched immediately."
        severity = "HIGH"

    elif itype == "SECURITY_BREACH":
        cp = conn.execute("SELECT id FROM security_checkpoints ORDER BY RANDOM() LIMIT 1").fetchone()
        if cp:
            conn.execute("UPDATE security_checkpoints SET status='CLOSED', alert='Breach – lane closed' WHERE id=?",
                         (cp["id"],))
            response = "Checkpoint temporarily closed; passengers rerouted to other lanes."

    elif itype == "LOST_BAGGAGE":
        response = description_override or "AI baggage tracking flagged a missing bag for manual trace."

    elif itype == "WEATHER_DISRUPTION":
        conn.execute("UPDATE flights SET delay_minutes=delay_minutes+10 WHERE status NOT IN ('COMPLETED')")
        response = "AI applied a precautionary ground-delay program across all active flights."

    elif itype == "FUEL_SHORTAGE" and target_flight:
        conn.execute("UPDATE flights SET delay_minutes=delay_minutes+20 WHERE id=?", (target_flight["id"],))
        response = "Fuel truck rerouted from standby pool; refuel rescheduled."

    elif itype == "GATE_CONFLICT":
        response = description_override or "AI queued the flight for the next available gate."

    elif itype == "RUNWAY_OBSTRUCTION":
        runway = conn.execute("SELECT id, name FROM runways WHERE status!='CLOSED' LIMIT 1").fetchone()
        if runway:
            conn.execute("UPDATE runways SET status='MAINTENANCE' WHERE id=?", (runway["id"],))
            response = f"{runway['name']} closed; AI rerouted runway sequencing."

    if target_flight and itype in ("MEDICAL_EMERGENCY",):
        # emergencies get landing/runway priority automatically
        conn.execute("UPDATE flights SET priority=priority+1 WHERE id=?", (target_flight["id"],))

    desc = description_override or f"{itype.replace('_', ' ').title()} reported" + (
        f" on {target_flight['flight_number']}" if target_flight else "")
    conn.execute(
        "INSERT INTO incidents (type, status, module, timestamp, description, severity, response) "
        "VALUES (?,?,?,?,?,?,?)",
        (itype, "RESOLVED" if response != "No automated action required." else "ACTIVE",
         "AI_DECISION_ENGINE", now.strftime("%H:%M"), desc, severity, response),
    )
    _log(conn, now, "INCIDENT", f"{desc} -> {response}")


def maybe_spawn_random_incident(conn, now, probability=0.12):
    if random.random() < probability:
        spawn_incident(conn, now)


def reopen_resources(conn):
    """Periodically frees up busy resources / reopens closed checkpoints so the
    airport doesn't permanently lock up over a long simulation."""
    conn.execute("UPDATE resources SET status='AVAILABLE', assigned_to=NULL WHERE status='BUSY' AND RANDOM()%4=0")
    conn.execute("UPDATE security_checkpoints SET status='OPEN', alert=NULL WHERE status='CLOSED' AND RANDOM()%5=0")
    conn.execute("UPDATE runways SET status='AVAILABLE' WHERE status='MAINTENANCE' AND RANDOM()%6=0")
    conn.execute("UPDATE ground_vehicles SET status='IDLE', aircraft_id=NULL WHERE status='IN_USE' AND RANDOM()%3=0")
    conn.execute("UPDATE aircraft SET status='PARKED' WHERE status='MAINTENANCE' AND RANDOM()%8=0")


# --------------------------------------------------------------------------- #
# MAIN TICK
# --------------------------------------------------------------------------- #
def advance_time(conn, ai_engine=None, minutes=TICK_MINUTES):
    now, tick_count = _now(conn)
    now = now + timedelta(minutes=minutes)
    tick_count += 1

    update_weather(conn, now)
    process_checkin(conn, now)
    process_security_and_immigration(conn, now)
    progress_flights(conn, now, ai_engine)
    process_baggage(conn, now, ai_engine)
    maybe_spawn_random_incident(conn, now)
    reopen_resources(conn)

    _set_now(conn, now, tick_count)
    conn.commit()
    return now, tick_count
