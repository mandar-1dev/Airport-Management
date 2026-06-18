"""
queries.py
Read-only helper functions that turn the live database state into
pandas DataFrames / dicts ready for Streamlit + Plotly to render.
Keeping all reads here separates the dashboard's presentation layer
from the simulation engine's write logic.
"""

import pandas as pd

PASSENGER_STAGE_ORDER = [
    "BOOKED", "QUEUED_CHECKIN", "CHECKED_IN", "QUEUED_SECURITY",
    "QUEUED_IMMIGRATION_DEP", "GATE_AREA", "BOARDING", "BOARDED", "IN_FLIGHT",
    "ARRIVED", "QUEUED_IMMIGRATION_ARR", "BAGGAGE_CLAIM", "CUSTOMS", "EXITED",
    "MISSED_FLIGHT",
]

BAGGAGE_STAGE_ORDER = [
    "PENDING", "CONVEYOR", "SORTING", "SCREENING", "LOADED",
    "UNLOADED", "CAROUSEL", "CLAIMED", "LOST", "OFFLOADED",
]

FLIGHT_STATE_COLORS = {
    "SCHEDULED": "#9CA3AF", "CHECKIN_OPEN": "#60A5FA", "BOARDING_PREP": "#3B82F6",
    "BOARDING": "#2563EB", "READY_FOR_PUSHBACK": "#F59E0B", "PUSHBACK": "#F59E0B",
    "TAXI_OUT": "#FBBF24", "RUNWAY_QUEUE": "#F97316", "TAKEOFF": "#EF4444",
    "CLIMB": "#10B981", "CRUISE": "#059669", "DESCENT": "#10B981", "LANDING": "#EF4444",
    "TAXI_IN": "#FBBF24", "AT_GATE_ARRIVED": "#3B82F6", "DEBOARDING": "#60A5FA",
    "CLEANING": "#A78BFA", "TURNAROUND": "#818CF8", "COMPLETED": "#6B7280",
}


def get_flights_df(conn):
    return pd.read_sql_query(
        """SELECT f.id, f.flight_number, f.airline, f.origin, f.destination,
                  f.international, f.status, f.delay_minutes, f.weather_factor,
                  ROUND(f.boarding_progress*100,1) AS boarding_pct,
                  f.passengers_total, f.passengers_boarded, f.priority,
                  f.maintenance_ok, f.scheduled_departure, f.actual_departure,
                  g.gate_number, r.name AS runway_name, a.tail_number, a.ac_type
           FROM flights f
           LEFT JOIN gates g ON g.id=f.gate_id
           LEFT JOIN runways r ON r.id=f.runway_id
           LEFT JOIN aircraft a ON a.id=f.aircraft_id
           ORDER BY f.scheduled_departure""",
        conn,
    )


def get_passengers_df(conn, flight_id=None):
    q = "SELECT * FROM passengers"
    params = ()
    if flight_id:
        q += " WHERE flight_id=?"
        params = (flight_id,)
    return pd.read_sql_query(q, conn, params=params)


def get_baggage_df(conn, flight_id=None):
    q = """SELECT b.*, p.name AS passenger_name, f.flight_number
           FROM baggage b
           JOIN passengers p ON p.id=b.passenger_id
           JOIN flights f ON f.id=b.flight_id"""
    params = ()
    if flight_id:
        q += " WHERE b.flight_id=?"
        params = (flight_id,)
    return pd.read_sql_query(q, conn, params=params)


def get_gates_df(conn):
    return pd.read_sql_query(
        """SELECT g.gate_number, g.status, f.flight_number
           FROM gates g LEFT JOIN flights f ON f.id=g.flight_id ORDER BY g.gate_number""",
        conn,
    )


def get_runways_df(conn):
    return pd.read_sql_query(
        """SELECT r.name, r.status, f.flight_number
           FROM runways r LEFT JOIN flights f ON f.id=r.flight_id ORDER BY r.name""",
        conn,
    )


def get_checkin_counters_df(conn):
    return pd.read_sql_query("SELECT * FROM checkin_counters ORDER BY counter_type, id", conn)


def get_security_df(conn):
    return pd.read_sql_query("SELECT * FROM security_checkpoints ORDER BY name", conn)


def get_resources_df(conn):
    return pd.read_sql_query(
        "SELECT type, status, COUNT(*) AS count FROM resources GROUP BY type, status ORDER BY type", conn
    )


def get_vehicles_df(conn):
    return pd.read_sql_query(
        "SELECT type, status, COUNT(*) AS count FROM ground_vehicles GROUP BY type, status ORDER BY type", conn
    )


def get_incidents_df(conn, limit=50):
    return pd.read_sql_query(
        "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", conn, params=(limit,)
    )


def get_weather_latest(conn):
    row = conn.execute("SELECT * FROM weather ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else {}


def get_weather_history_df(conn, limit=40):
    return pd.read_sql_query(
        "SELECT * FROM (SELECT * FROM weather ORDER BY id DESC LIMIT ?) ORDER BY id ASC", conn, params=(limit,)
    )


def get_event_log_df(conn, limit=60):
    return pd.read_sql_query(
        "SELECT * FROM (SELECT * FROM event_log ORDER BY id DESC LIMIT ?) ORDER BY id DESC", conn, params=(limit,)
    )


def get_kpis(conn):
    flights = conn.execute("SELECT COUNT(*) c FROM flights").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) c FROM flights WHERE status NOT IN ('COMPLETED')").fetchone()["c"]
    on_time = conn.execute("SELECT COUNT(*) c FROM flights WHERE delay_minutes <= 25").fetchone()["c"]
    avg_delay = conn.execute("SELECT AVG(delay_minutes) a FROM flights").fetchone()["a"] or 0
    pax_in_system = conn.execute(
        "SELECT COUNT(*) c FROM passengers WHERE status NOT IN ('EXITED','MISSED_FLIGHT')"
    ).fetchone()["c"]
    bags_in_system = conn.execute(
        "SELECT COUNT(*) c FROM baggage WHERE status NOT IN ('CLAIMED','LOST','PENDING','OFFLOADED')"
    ).fetchone()["c"]
    lost_bags = conn.execute("SELECT COUNT(*) c FROM baggage WHERE lost=1").fetchone()["c"]
    active_incidents = conn.execute("SELECT COUNT(*) c FROM incidents WHERE status='ACTIVE'").fetchone()["c"]
    gates_free = conn.execute("SELECT COUNT(*) c FROM gates WHERE status='AVAILABLE'").fetchone()["c"]
    return {
        "total_flights": flights,
        "active_flights": active,
        "on_time_pct": round(100 * on_time / max(1, flights), 1),
        "avg_delay": round(avg_delay, 1),
        "passengers_in_system": pax_in_system,
        "bags_in_system": bags_in_system,
        "lost_bags": lost_bags,
        "active_incidents": active_incidents,
        "gates_free": gates_free,
    }


def get_passenger_funnel(conn):
    rows = pd.read_sql_query("SELECT status, COUNT(*) c FROM passengers GROUP BY status", conn)
    counts = dict(zip(rows["status"], rows["c"]))
    return {stage: counts.get(stage, 0) for stage in PASSENGER_STAGE_ORDER}


def get_baggage_funnel(conn):
    rows = pd.read_sql_query("SELECT status, COUNT(*) c FROM baggage GROUP BY status", conn)
    counts = dict(zip(rows["status"], rows["c"]))
    return {stage: counts.get(stage, 0) for stage in BAGGAGE_STAGE_ORDER}
