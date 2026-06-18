"""
database.py
SQLite schema and connection helpers for the Smart Airport Digital Twin.
Every entity in the airport ecosystem (flights, passengers, baggage, gates,
runways, crew, resources, weather, incidents) lives in this single SQLite
database so every module can read/write a shared, consistent state.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "airport_twin.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sim_time_value TEXT NOT NULL,
    tick_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS aircraft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tail_number TEXT NOT NULL,
    ac_type TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARKED'
);

CREATE TABLE IF NOT EXISTS crew (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    flight_id INTEGER
);

CREATE TABLE IF NOT EXISTS gates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    flight_id INTEGER
);

CREATE TABLE IF NOT EXISTS runways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    flight_id INTEGER
);

CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number TEXT NOT NULL,
    airline TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    international INTEGER NOT NULL DEFAULT 0,
    aircraft_id INTEGER,
    gate_id INTEGER,
    runway_id INTEGER,
    scheduled_departure TEXT NOT NULL,
    actual_departure TEXT,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    delay_minutes INTEGER NOT NULL DEFAULT 0,
    weather_factor REAL NOT NULL DEFAULT 0.0,
    boarding_progress REAL NOT NULL DEFAULT 0.0,
    passengers_total INTEGER NOT NULL DEFAULT 0,
    passengers_checked_in INTEGER NOT NULL DEFAULT 0,
    passengers_boarded INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    maintenance_ok INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS passengers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    flight_id INTEGER NOT NULL,
    seat TEXT,
    status TEXT NOT NULL DEFAULT 'BOOKED',
    bag_count INTEGER NOT NULL DEFAULT 0,
    checkin_method TEXT,
    vip INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS baggage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_id TEXT NOT NULL,
    passenger_id INTEGER NOT NULL,
    flight_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    location TEXT,
    lost_risk REAL NOT NULL DEFAULT 0.0,
    lost INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS checkin_counters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    counter_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CLOSED',
    queue_length INTEGER NOT NULL DEFAULT 0,
    flight_id INTEGER
);

CREATE TABLE IF NOT EXISTS security_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    queue_length INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN',
    alert TEXT
);

CREATE TABLE IF NOT EXISTS weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    condition TEXT NOT NULL,
    visibility_km REAL NOT NULL,
    wind_speed_kmh REAL NOT NULL,
    impact_factor REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    module TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MEDIUM',
    response TEXT
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    assigned_to TEXT
);

CREATE TABLE IF NOT EXISTS ground_vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'IDLE',
    aircraft_id INTEGER
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    module TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


TABLE_NAMES = [
    "sim_clock", "aircraft", "crew", "gates", "runways", "flights", "passengers",
    "baggage", "checkin_counters", "security_checkpoints", "weather", "incidents",
    "resources", "ground_vehicles", "event_log",
]


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(reset=False):
    """Create the database and return a connection.

    Important: this never deletes the underlying .db file. On Windows, an
    open sqlite3 connection holds an OS-level lock on the file, so
    os.remove() on a freshly-opened (or still-open, e.g. from a previous
    Streamlit session) connection raises PermissionError [WinError 32].
    Instead, `reset=True` clears every table in place via DROP + CREATE,
    which works identically on Windows, macOS and Linux.
    """
    conn = get_connection()
    if reset:
        for table in TABLE_NAMES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def is_initialized(conn):
    row = conn.execute("SELECT COUNT(*) AS c FROM flights").fetchone()
    return row["c"] > 0


def log_event(conn, module, message, timestamp):
    conn.execute(
        "INSERT INTO event_log (timestamp, module, message) VALUES (?, ?, ?)",
        (timestamp, module, message),
    )


def df(conn, query, params=()):
    """Run a query and return a list of sqlite3.Row results."""
    return conn.execute(query, params).fetchall()
