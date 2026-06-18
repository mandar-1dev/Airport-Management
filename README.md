# Smart Airport Digital Twin & Operations Management System

A full operational digital twin of an airport, built end-to-end: every
flight, passenger, bag, gate, runway, counter, security checkpoint,
ground vehicle, and staff resource lives in a shared SQLite database and
evolves tick-by-tick through realistic, interconnected state machines —
per the "Full Integration Requirement" (bad weather -> delays -> gate
conflicts -> boarding delays -> runway congestion -> reduced throughput).

## What's implemented

- **Flight lifecycle**: SCHEDULED -> CHECKIN_OPEN -> BOARDING_PREP -> BOARDING
  -> READY_FOR_PUSHBACK -> PUSHBACK -> TAXI_OUT -> RUNWAY_QUEUE -> TAKEOFF ->
  CLIMB -> CRUISE -> DESCENT -> LANDING -> TAXI_IN -> AT_GATE_ARRIVED ->
  DEBOARDING -> CLEANING -> TURNAROUND -> COMPLETED.
- **Passenger journey**: booking -> check-in (manual / kiosk / mobile /
  premium) -> baggage drop -> security -> immigration (international only)
  -> gate area -> boarding -> in-flight -> arrival -> immigration/customs ->
  baggage claim -> exit. Passengers who don't make it through in time are
  marked MISSED_FLIGHT instead of sitting forever in a stale queue.
- **Baggage lifecycle**: check-in -> conveyor -> sorting -> screening ->
  loaded -> unloaded -> carousel -> claimed (or flagged LOST by the AI model
  before it happens, or OFFLOADED if the passenger missed the flight).
- **Gates, runways, taxiways**: dynamic assignment/contention, AI-flagged
  gate conflicts, runway queueing and sequencing.
- **Check-in & security**: queue-driven throughput, AI recommendations to
  open/close counters or lanes, congestion alerts.
- **Weather Impact Engine**: condition drives takeoff/landing probability,
  taxi speed, and delay accumulation across every active flight.
- **Incident simulation + AI Decision Engine**: medical emergencies, engine
  failures, bird strikes, fires, security breaches, fuel shortages, gate
  conflicts, runway obstructions — each gets an automatic, logged AI
  response (aircraft reassignment, resource dispatch, runway closure, etc.).
- **Resource & ground-vehicle management**: staff and vehicles tracked by
  type/status, surfaced as AI utilization recommendations.
- **AI Engine** (`ai_engine.py`): a scikit-learn RandomForestRegressor
  predicts per-flight delay risk from live weather/queue/gate/maintenance
  features; a LogisticRegression model predicts per-bag lost-baggage risk
  before it happens; a rule-based recommender scans every module each tick.
- **Digital Twin dashboard** (`app.py`, Streamlit + Plotly): a live aircraft
  map, full flight/passenger/baggage tables and funnels, gate/runway grids,
  resource charts, a weather panel, an incident log, and an AI insights page
  with model feature importance.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Use the sidebar to advance simulated time (+10 min / +1 hour) and watch
every module react together, or reset to a fresh day.

## Project layout

- `database.py` — SQLite schema + connection helpers
- `data_generator.py` — generates a full synthetic operating day (flights,
  passengers, baggage, crew, gates, runways, counters, resources, vehicles)
- `simulation.py` — the tick-based state-machine engine for every module
- `ai_engine.py` — predictive models + rule-based recommendation engine
- `queries.py` — read-only helpers that turn DB state into dashboard-ready data
- `app.py` — the Streamlit dashboard

## Notes on calibration

This is a stress-tested single-day simulation (14 flights, ~2,000-2,500
passengers, randomized incidents and weather), tuned so a typical run lands
around a 5-10% missed-flight rate, ~3% baggage loss rate, and 85-95%
on-time performance under normal conditions — believable numbers for a
demo, while still being chaotic enough to showcase the AI/incident layer
when weather or congestion spikes hit. Tune the constants at the top of
`simulation.py` and `data_generator.py` (flight count, throughput caps,
incident probability) to make the airport busier/calmer.
