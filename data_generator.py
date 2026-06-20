"""
data_generator.py
Generates a realistic synthetic initial state for one full operating day:
aircraft, crew, flights, passengers, baggage, gates, runways, check-in
counters, security checkpoints, ground vehicles, staff resources, and the
first weather reading. This is the "seed" the Digital Twin then evolves
tick by tick.
"""

import random
from datetime import datetime, timedelta

AIRLINES = ["IndiGo", "Air India", "Vistara", "SpiceJet", "Emirates",
            "Qatar Airways", "Lufthansa", "British Airways"]
DOMESTIC_DESTS = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Goa"]
INTL_DESTS = ["Dubai", "Doha", "London", "Frankfurt", "Singapore", "New York"]
AC_TYPES = [("A320", 180), ("B737", 189), ("A321", 220), ("B777", 396), ("A350", 325)]
FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Sai", "Ananya", "Diya", "Ishaan", "Kabir",
               "Meera", "Riya", "Arjun", "Krishna", "Sara", "Neha", "Rohan", "Priya",
               "Aman", "Tara", "Vikram", "Zoya", "Rahul", "Pooja", "Karan", "Nisha"]
LAST_NAMES = ["Sharma", "Verma", "Patel", "Gupta", "Iyer", "Nair", "Khan", "Singh",
              "Reddy", "Joshi", "Mehta", "Kapoor", "Chopra", "Das", "Rao"]

CHECKIN_METHODS = ["MOBILE", "KIOSK", "MANUAL", "PREMIUM"]
CHECKIN_WEIGHTS = [0.40, 0.25, 0.25, 0.10]

N_AIRCRAFT = 9
N_FLIGHTS = 14
N_GATES = 10
N_RUNWAYS = 3
N_SECURITY_CHECKPOINTS = 5

RESOURCE_TYPES = {
    "GROUND_CREW": 12,
    "SECURITY_PERSONNEL": 14,
    "CHECKIN_STAFF": 10,
    "IMMIGRATION_OFFICER": 6,
    "FIRE_RESPONSE": 4,
    "MEDICAL_TEAM": 3,
    "MAINTENANCE_ENGINEER": 6,
}

VEHICLE_TYPES = {
    "FUEL_TRUCK": 4,
    "CATERING_TRUCK": 4,
    "BAGGAGE_TRUCK": 6,
    "PUSHBACK_TRUCK": 4,
    "WATER_SERVICE": 3,
    "CLEANING_VAN": 4,
}


def random_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def generate(conn, sim_start: datetime):
    cur = conn.cursor()

    # ---- sim clock ----
    cur.execute(
        "INSERT INTO sim_clock (id, sim_time_value, tick_count) VALUES (1, ?, 0)",
        (sim_start.isoformat(),),
    )

    # ---- aircraft ----
    aircraft_ids = []
    for i in range(N_AIRCRAFT):
        ac_type, cap = random.choice(AC_TYPES)
        cur.execute(
            "INSERT INTO aircraft (tail_number, ac_type, capacity, status) VALUES (?,?,?,?)",
            (f"VT-{random.choice('ABCDEFGH')}{random.randint(100,999)}", ac_type, cap, "PARKED"),
        )
        aircraft_ids.append((cur.lastrowid, cap))

    # ---- gates ----
    gate_ids = []
    for i in range(N_GATES):
        cur.execute(
            "INSERT INTO gates (gate_number, status) VALUES (?, 'AVAILABLE')",
            (f"G{i+1}",),
        )
        gate_ids.append(cur.lastrowid)

    # ---- runways ----
    runway_ids = []
    for i in range(N_RUNWAYS):
        cur.execute(
            "INSERT INTO runways (name, status) VALUES (?, 'AVAILABLE')",
            (f"RWY-{['09L','09R','27'][i % 3]}",),
        )
        runway_ids.append(cur.lastrowid)

    # ---- check-in counters (physical: MANUAL / KIOSK / PREMIUM) ----
    for i in range(6):
        cur.execute(
            "INSERT INTO checkin_counters (counter_type, status, queue_length) VALUES ('MANUAL','OPEN',0)"
        )
    for i in range(4):
        cur.execute(
            "INSERT INTO checkin_counters (counter_type, status, queue_length) VALUES ('KIOSK','OPEN',0)"
        )
    for i in range(2):
        cur.execute(
            "INSERT INTO checkin_counters (counter_type, status, queue_length) VALUES ('PREMIUM','OPEN',0)"
        )

    # ---- security checkpoints ----
    for i in range(N_SECURITY_CHECKPOINTS):
        cur.execute(
            "INSERT INTO security_checkpoints (name, queue_length, status) VALUES (?, 0, 'OPEN')",
            (f"Checkpoint {chr(65+i)}",),
        )

    # ---- resources ----
    for rtype, count in RESOURCE_TYPES.items():
        for i in range(count):
            cur.execute(
                "INSERT INTO resources (type, name, status) VALUES (?,?, 'AVAILABLE')",
                (rtype, f"{rtype}_{i+1}"),
            )

    # ---- ground vehicles ----
    for vtype, count in VEHICLE_TYPES.items():
        for i in range(count):
            cur.execute(
                "INSERT INTO ground_vehicles (type, status) VALUES (?, 'IDLE')",
                (vtype,),
            )

    # ---- weather (initial reading: clear) ----
    cur.execute(
        "INSERT INTO weather (timestamp, condition, visibility_km, wind_speed_kmh, impact_factor) "
        "VALUES (?, 'CLEAR', 10.0, 12.0, 0.05)",
        (sim_start.isoformat(),),
    )

    # ---- flights, crew, passengers, baggage ----
    depart_cursor = sim_start + timedelta(minutes=20)
    for i in range(N_FLIGHTS):
        airline = random.choice(AIRLINES)
        international = random.random() < 0.35
        dest = random.choice(INTL_DESTS) if international else random.choice(DOMESTIC_DESTS)
        ac_id, capacity = random.choice(aircraft_ids)
        flight_number = f"{airline[:2].upper()}{random.randint(100,999)}"
        scheduled = depart_cursor
        depart_cursor += timedelta(minutes=random.randint(25, 40))

        cur.execute(
            """INSERT INTO flights
               (flight_number, airline, origin, destination, international, aircraft_id,
                scheduled_departure, status, passengers_total)
               VALUES (?,?,?,?,?,?,?, 'SCHEDULED', 0)""",
            (flight_number, airline, "Pune (PNQ)", dest, int(international), ac_id,
             scheduled.isoformat()),
        )
        flight_id = cur.lastrowid

        # crew: 2 pilots + 4 cabin crew
        for role in ["CAPTAIN", "FIRST_OFFICER", "CABIN_CREW", "CABIN_CREW", "CABIN_CREW", "CABIN_CREW"]:
            cur.execute(
                "INSERT INTO crew (name, role, status, flight_id) VALUES (?,?, 'ASSIGNED', ?)",
                (random_name(), role, flight_id),
            )

        # passengers
        load_factor = random.uniform(0.65, 0.95)
        n_pax = int(capacity * load_factor)
        for seat_num in range(1, n_pax + 1):
            method = random.choices(CHECKIN_METHODS, weights=CHECKIN_WEIGHTS)[0]
            vip = 1 if (method == "PREMIUM" and random.random() < 0.5) else 0
            bag_count = random.choices([0, 1, 2], weights=[0.15, 0.55, 0.30])[0]
            cur.execute(
                """INSERT INTO passengers (name, flight_id, seat, status, bag_count, checkin_method, vip)
                   VALUES (?,?,?, 'BOOKED', ?, ?, ?)""",
                (random_name(), flight_id, f"{(seat_num // 6) + 1}{chr(65 + seat_num % 6)}",
                 bag_count, method, vip),
            )
            passenger_id = cur.lastrowid
            for b in range(bag_count):
                tracking_id = f"BG{flight_id:03d}{passenger_id:05d}{b}"
                cur.execute(
                    """INSERT INTO baggage (tracking_id, passenger_id, flight_id, status, location, lost_risk)
                       VALUES (?,?,?, 'PENDING', 'NOT_CHECKED', 0.02)""",
                    (tracking_id, passenger_id, flight_id),
                )

        cur.execute("UPDATE flights SET passengers_total = ? WHERE id = ?", (n_pax, flight_id))

    conn.commit()
