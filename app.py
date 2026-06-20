"""
app.py
Smart Airport Digital Twin & Operations Management System — main dashboard.
Run with:  streamlit run app.py
"""

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import database as db
import data_generator
import queries as q
import simulation as sim
from ai_engine import AIEngine

st.set_page_config(page_title="Smart Airport Digital Twin", page_icon="✈️", layout="wide")



def get_ai_engine():
    return AIEngine()


def init_simulation():
    if "conn" in st.session_state:
        try:
            st.session_state.conn.close()
        except Exception:
            pass
    conn = db.init_db(reset=True)
    sim_start = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
    data_generator.generate(conn, sim_start)
    st.session_state.conn = conn
    st.session_state.initialized = True


if "initialized" not in st.session_state:
    init_simulation()

conn = st.session_state.conn
ai_engine = get_ai_engine()



clock_row = conn.execute("SELECT sim_time_value, tick_count FROM sim_clock WHERE id=1").fetchone()
sim_time = datetime.fromisoformat(clock_row["sim_time_value"])

st.sidebar.title("✈️ Airport Digital Twin")
st.sidebar.caption("Smart Airport Operations Management System")
st.sidebar.markdown(f"**Simulated time:** {sim_time.strftime('%H:%M')}  \n**Tick:** {clock_row['tick_count']}")

c1, c2 = st.sidebar.columns(2)
if c1.button("⏭ +10 min"):
    sim.advance_time(conn, ai_engine, minutes=10)
    st.rerun()
if c2.button("⏩ +1 hour"):
    for _ in range(6):
        sim.advance_time(conn, ai_engine, minutes=10)
    st.rerun()

if st.sidebar.button("🔄 Reset Simulation", use_container_width=True):
    init_simulation()
    st.rerun()

st.sidebar.divider()
page = st.sidebar.radio(
    "Navigate",
    ["Overview / Digital Twin", "Flights", "Passengers", "Check-in & Security",
     "Baggage", "Gates & Runways", "Ground Ops & Resources", "Weather",
     "Incidents", "AI Insights"],
)

st.sidebar.divider()
st.sidebar.caption("Recent activity")
log_df = q.get_event_log_df(conn, limit=8)
for _, r in log_df.iterrows():
    st.sidebar.text(f"[{r['timestamp']}] {r['module']}: {r['message'][:46]}")



def page_overview():
    st.title("🛰️ Digital Twin — Live Airport State")
    kpis = q.get_kpis(conn)

    cols = st.columns(6)
    cols[0].metric("Total Flights", kpis["total_flights"])
    cols[1].metric("Active Flights", kpis["active_flights"])
    cols[2].metric("On-Time %", f"{kpis['on_time_pct']}%")
    cols[3].metric("Avg Delay (min)", kpis["avg_delay"])
    cols[4].metric("Passengers In-System", kpis["passengers_in_system"])
    cols[5].metric("Active Incidents", kpis["active_incidents"], delta=None)

    cols2 = st.columns(4)
    cols2[0].metric("Bags In-System", kpis["bags_in_system"])
    cols2[1].metric("Lost Bags", kpis["lost_bags"])
    cols2[2].metric("Gates Free", kpis["gates_free"])
    weather = q.get_weather_latest(conn)
    cols2[3].metric("Weather", weather.get("condition", "N/A"),
                     f"impact {weather.get('impact_factor', 0):.2f}")

    st.subheader("Live Aircraft Map")
    flights_df = q.get_flights_df(conn)
    flights_df = flights_df[flights_df["status"] != "COMPLETED"]

    lane_map = {
        "SCHEDULED": "Gate / Ground", "CHECKIN_OPEN": "Gate / Ground", "BOARDING_PREP": "Gate / Ground",
        "BOARDING": "Gate / Ground", "READY_FOR_PUSHBACK": "Gate / Ground", "AT_GATE_ARRIVED": "Gate / Ground",
        "DEBOARDING": "Gate / Ground", "CLEANING": "Gate / Ground", "TURNAROUND": "Gate / Ground",
        "PUSHBACK": "Taxiway", "TAXI_OUT": "Taxiway", "TAXI_IN": "Taxiway",
        "RUNWAY_QUEUE": "Runway", "TAKEOFF": "Runway", "LANDING": "Runway",
        "CLIMB": "Airborne", "CRUISE": "Airborne", "DESCENT": "Airborne",
    }
    lane_order = ["Airborne", "Runway", "Taxiway", "Gate / Ground"]
    flights_df["lane"] = flights_df["status"].map(lane_map).fillna("Gate / Ground")
    flights_df["x_pos"] = flights_df.groupby("lane").cumcount() if False else range(len(flights_df))
    # spread points within each lane
    flights_df["x_pos"] = flights_df.groupby("lane").cumcount()

    fig = go.Figure()
    for status, color in q.FLIGHT_STATE_COLORS.items():
        subset = flights_df[flights_df["status"] == status]
        if subset.empty:
            continue
        fig.add_trace(go.Scatter(
            x=subset["x_pos"], y=subset["lane"], mode="markers+text",
            marker=dict(size=22, color=color, line=dict(width=1, color="white")),
            text=subset["flight_number"], textposition="top center",
            name=status,
            hovertext=[f"{r.flight_number} · {r.destination} · delay {r.delay_minutes}m"
                       for r in subset.itertuples()],
            hoverinfo="text",
        ))
    fig.update_yaxes(categoryorder="array", categoryarray=lane_order, title=None)
    fig.update_xaxes(visible=False)
    fig.update_layout(height=380, legend=dict(orientation="h", y=-0.2), margin=dict(t=10))
    st.plotly_chart(fig, use_container_width=True)

    cgate, crwy = st.columns(2)
    with cgate:
        st.caption("Gate status")
        gdf = q.get_gates_df(conn)
        st.dataframe(gdf, use_container_width=True, height=250, hide_index=True)
    with crwy:
        st.caption("Runway status")
        rdf = q.get_runways_df(conn)
        st.dataframe(rdf, use_container_width=True, height=250, hide_index=True)



def page_flights():
    st.title("🛫 Flight Lifecycle")
    fdf = q.get_flights_df(conn)

    fig = px.bar(fdf.groupby("status").size().reindex(sim.FLIGHT_STATES, fill_value=0).reset_index(name="count"),
                 x="status", y="count", color="status",
                 color_discrete_map=q.FLIGHT_STATE_COLORS, title="Flights by lifecycle stage")
    fig.update_layout(showlegend=False, height=320)
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        fdf[["flight_number", "airline", "destination", "international", "status", "gate_number",
             "runway_name", "boarding_pct", "delay_minutes", "scheduled_departure"]],
        use_container_width=True, hide_index=True,
    )

    st.subheader("AI Delay Prediction")
    options = fdf["flight_number"].tolist()
    if options:
        chosen = st.selectbox("Select a flight", options)
        fid = int(fdf[fdf["flight_number"] == chosen]["id"].iloc[0])
        predicted = ai_engine.predict_flight_delay(conn, fid)
        row = fdf[fdf["id"] == fid].iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Current recorded delay", f"{row['delay_minutes']} min")
        c2.metric("AI-predicted additional risk", f"{predicted} min")
        c3.metric("Boarding progress", f"{row['boarding_pct']}%")



def page_passengers():
    st.title("🧳 Passenger Journey")
    funnel = q.get_passenger_funnel(conn)
    stages = [s for s in funnel if funnel[s] > 0] or list(funnel.keys())[:3]
    fig = go.Figure(go.Funnel(
        y=[s.replace("_", " ").title() for s in stages],
        x=[funnel[s] for s in stages],
        textinfo="value+percent initial",
    ))
    fig.update_layout(height=520, title="Passengers by journey stage (all flights)")
    st.plotly_chart(fig, use_container_width=True)

    fdf = q.get_flights_df(conn)
    chosen = st.selectbox("Inspect a flight's passengers", fdf["flight_number"].tolist())
    if chosen:
        fid = int(fdf[fdf["flight_number"] == chosen]["id"].iloc[0])
        pdf = q.get_passengers_df(conn, fid)
        st.dataframe(pdf[["name", "seat", "status", "checkin_method", "bag_count", "vip"]],
                     use_container_width=True, hide_index=True)



def page_checkin_security():
    st.title("🛂 Check-in, Security & Immigration")

    cdf = q.get_checkin_counters_df(conn)
    agg = cdf.groupby("counter_type").agg(open_counters=("status", lambda s: (s == "OPEN").sum()),
                                           total_queue=("queue_length", "sum")).reset_index()
    fig = px.bar(agg, x="counter_type", y="total_queue", color="counter_type", title="Check-in queue by type")
    fig.update_layout(height=300, showlegend=False)
    c1, c2 = st.columns([2, 1])
    c1.plotly_chart(fig, use_container_width=True)
    c2.dataframe(agg, use_container_width=True, hide_index=True)

    st.subheader("Security checkpoints")
    sdf = q.get_security_df(conn)
    st.dataframe(sdf, use_container_width=True, hide_index=True)

    flagged = sdf[sdf["alert"].notna()]
    for _, r in flagged.iterrows():
        st.warning(f"**{r['name']}** — {r['alert']}")



def page_baggage():
    st.title("🧷 Baggage Handling Lifecycle")
    funnel = q.get_baggage_funnel(conn)
    fig = go.Figure(go.Funnel(
        y=[s.title() for s in funnel],
        x=list(funnel.values()),
        textinfo="value",
    ))
    fig.update_layout(height=460, title="Bags by handling stage")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("AI lost-baggage risk")
    bdf = q.get_baggage_df(conn)
    at_risk = bdf[(bdf["lost_risk"] > 0.1) | (bdf["lost"] == 1)].sort_values("lost_risk", ascending=False)
    if at_risk.empty:
        st.success("No bags currently flagged as high risk.")
    else:
        st.dataframe(
            at_risk[["tracking_id", "flight_number", "passenger_name", "status", "lost_risk", "lost"]],
            use_container_width=True, hide_index=True,
        )

    search = st.text_input("🔎 Track a bag by tracking ID")
    if search:
        match = bdf[bdf["tracking_id"].str.contains(search, case=False, na=False)]
        st.dataframe(match, use_container_width=True, hide_index=True)



def page_gates_runways():
    st.title("🚪 Gates & Runways")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Gates")
        st.dataframe(q.get_gates_df(conn), use_container_width=True, hide_index=True, height=400)
    with c2:
        st.subheader("Runways")
        st.dataframe(q.get_runways_df(conn), use_container_width=True, hide_index=True, height=400)



def page_ground_ops():
    st.title("🚚 Ground Operations & Resource Management")
    rdf = q.get_resources_df(conn)
    fig1 = px.bar(rdf, x="type", y="count", color="status", barmode="stack", title="Staff resources")
    fig1.update_layout(height=380)
    st.plotly_chart(fig1, use_container_width=True)

    vdf = q.get_vehicles_df(conn)
    fig2 = px.bar(vdf, x="type", y="count", color="status", barmode="stack", title="Ground vehicles")
    fig2.update_layout(height=380)
    st.plotly_chart(fig2, use_container_width=True)



def page_weather():
    st.title("🌦️ Weather Impact Engine")
    w = q.get_weather_latest(conn)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Condition", w.get("condition", "—"))
    c2.metric("Visibility (km)", round(w.get("visibility_km", 0), 1))
    c3.metric("Wind (km/h)", round(w.get("wind_speed_kmh", 0), 1))
    c4.metric("Impact factor", round(w.get("impact_factor", 0), 2))

    hist = q.get_weather_history_df(conn)
    if not hist.empty:
        fig = px.line(hist, x="timestamp", y="impact_factor", markers=True,
                       title="Weather impact factor over time")
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)

    st.info("Higher impact factor reduces takeoff/landing probability, slows taxi operations, "
            "and increases delay accumulation across every flight currently active.")



def page_incidents():
    st.title("🚨 Incident Simulation & AI Response")
    idf = q.get_incidents_df(conn)
    if idf.empty:
        st.success("No incidents recorded yet.")
        return
    sev_color = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}
    idf["sev"] = idf["severity"].map(sev_color)
    st.dataframe(
        idf[["sev", "type", "status", "timestamp", "description", "response"]],
        use_container_width=True, hide_index=True,
    )



def page_ai_insights():
    st.title("🧠 AI Decision Engine — Recommendations")
    recs = ai_engine.generate_recommendations(conn)
    sev_color = {"HIGH": "error", "MEDIUM": "warning", "LOW": "info"}
    for module, severity, text in recs:
        getattr(st, sev_color.get(severity, "info"))(f"**[{module}]** {text}")

    st.subheader("Delay-prediction model — feature importance")
    importances = ai_engine.delay_model.feature_importances_
    feat_names = ["Weather impact", "Check-in queue", "Security queue", "Gate conflict", "Maintenance flag"]
    fig = px.bar(x=feat_names, y=importances, title="RandomForestRegressor feature importance")
    fig.update_layout(height=320, yaxis_title="importance", xaxis_title=None)
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Trained on synthetic operational data: weather impact, queue lengths, gate conflicts and "
               "maintenance flags predicting expected delay minutes. The logistic-regression baggage model "
               "uses sorting/screening congestion, international routing and weather impact to flag "
               "lost-baggage risk per bag before it happens.")


PAGES = {
    "Overview / Digital Twin": page_overview,
    "Flights": page_flights,
    "Passengers": page_passengers,
    "Check-in & Security": page_checkin_security,
    "Baggage": page_baggage,
    "Gates & Runways": page_gates_runways,
    "Ground Ops & Resources": page_ground_ops,
    "Weather": page_weather,
    "Incidents": page_incidents,
    "AI Insights": page_ai_insights,
}

PAGES[page]()
