
import sqlite3
import random
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH          = Path("oee_production.db")
MACHINES         = ["M01_SMT", "M02_Reflow", "M03_AOI", "M04_THT", "M05_ICT"]
SHIFTS           = ["Morning", "Afternoon", "Night"]
SHIFT_DURATION_H = 8                    # hours per shift
PLANNED_DOWNTIME_MIN = 30               # planned maintenance per shift (min)
IDEAL_CYCLE_TIME_S = {                  # seconds per unit, design spec
    "M01_SMT":   18,
    "M02_Reflow": 22,
    "M03_AOI":   12,
    "M04_THT":   35,
    "M05_ICT":   28,
}
TAKT_TIME_S      = 20                   # customer demand rate (s/unit)
OEE_WORLD_CLASS  = 0.85                 # 85 % world-class threshold
SIMULATION_DAYS  = 7
RANDOM_SEED      = 42

DOWNTIME_CAUSES  = [
    "Mechanical Failure",
    "Material Shortage",
    "Changeover",
    "Operator Absence",
    "Quality Hold",
    "Electrical Fault",
    "Calibration",
]

random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)



def simulate_shift_log(machine: str, shift: str, shift_date: str) -> dict:
    """
    Simulate one production shift for a given machine.

    Returns a dict representing one row of machine log data with:
    - planned & actual production times
    - units produced, rejected, ideal
    - unplanned downtime breakdown by cause
    """
    ideal_ct    = IDEAL_CYCLE_TIME_S[machine]
    avail_time  = SHIFT_DURATION_H * 60           # minutes
    planned_dt  = PLANNED_DOWNTIME_MIN

    # Unplanned downtime: random 0–90 min, weighted by machine type
    unplanned_dt = int(np.random.triangular(0, 15, 90))

    # Operating time = available − planned − unplanned
    operating_time_min = max(avail_time - planned_dt - unplanned_dt, 0)

    # Actual cycle time is worse than ideal (performance loss)
    perf_loss   = np.random.uniform(1.0, 1.35)   # 0–35 % slower
    actual_ct   = ideal_ct * perf_loss            # seconds/unit

    # Units produced in operating window
    units_produced = int((operating_time_min * 60) / actual_ct)

    # Quality: reject rate 1–12 %
    reject_rate  = np.random.uniform(0.01, 0.12)
    units_rejected = int(units_produced * reject_rate)
    units_good     = units_produced - units_rejected

    # Ideal units in operating time
    ideal_units  = int((operating_time_min * 60) / ideal_ct)

    # Assign unplanned downtime to a cause
    cause        = random.choice(DOWNTIME_CAUSES) if unplanned_dt > 0 else "None"

    # Downtime events: split unplanned into 1–3 events
    n_events     = random.randint(1, 3) if unplanned_dt > 5 else 0
    dt_events    = []
    remaining    = unplanned_dt
    for i in range(n_events):
        dur = remaining if i == n_events - 1 else random.randint(1, remaining)
        dur = max(1, dur)
        remaining -= dur
        if remaining < 0:
            dur += remaining
            remaining = 0
        dt_events.append({"cause": random.choice(DOWNTIME_CAUSES), "duration_min": dur})
        if remaining <= 0:
            break

    return {
        "date":                 shift_date,
        "shift":                shift,
        "machine":              machine,
        "available_time_min":   avail_time,
        "planned_downtime_min": planned_dt,
        "unplanned_downtime_min": unplanned_dt,
        "operating_time_min":   operating_time_min,
        "ideal_cycle_time_s":   ideal_ct,
        "actual_cycle_time_s":  round(actual_ct, 2),
        "units_produced":       units_produced,
        "units_rejected":       units_rejected,
        "units_good":           units_good,
        "ideal_units":          ideal_units,
        "primary_cause":        cause,
        "downtime_events":      json.dumps(dt_events),
    }


def generate_all_logs() -> pd.DataFrame:
    """
    Generate SIMULATION_DAYS × 3 shifts × 5 machines = 105 shift log rows.
    Returns a DataFrame.
    """
    print("[1/4] Simulating production logs...")
    records = []
    base_date = datetime(2024, 1, 1)

    for day in range(SIMULATION_DAYS):
        shift_date = (base_date + timedelta(days=day)).strftime("%Y-%m-%d")
        for shift in SHIFTS:
            for machine in MACHINES:
                rec = simulate_shift_log(machine, shift, shift_date)
                records.append(rec)

    df = pd.DataFrame(records)
    print(f"   Generated {len(df)} shift records  "
          f"({SIMULATION_DAYS} days × {len(SHIFTS)} shifts × {len(MACHINES)} machines)")
    return df



def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shift_logs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            date                    TEXT    NOT NULL,
            shift                   TEXT    NOT NULL,
            machine                 TEXT    NOT NULL,
            available_time_min      REAL,
            planned_downtime_min    REAL,
            unplanned_downtime_min  REAL,
            operating_time_min      REAL,
            ideal_cycle_time_s      REAL,
            actual_cycle_time_s     REAL,
            units_produced          INTEGER,
            units_rejected          INTEGER,
            units_good              INTEGER,
            ideal_units             INTEGER,
            primary_cause           TEXT,
            downtime_events         TEXT
        );

        CREATE TABLE IF NOT EXISTS oee_kpis (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT,
            shift           TEXT,
            machine         TEXT,
            availability    REAL,
            performance     REAL,
            quality         REAL,
            oee             REAL,
            world_class     INTEGER
        );
    """)
    conn.commit()


def persist_logs(df: pd.DataFrame, conn: sqlite3.Connection) -> None:
    """Write raw shift logs to SQLite."""
    print("[2/4] Persisting data to SQLite...")
    df.to_sql("shift_logs", conn, if_exists="replace", index=False)
    print(f"   Wrote {len(df)} rows to shift_logs table")


def load_logs(conn: sqlite3.Connection) -> pd.DataFrame:
    """Read shift logs back from SQLite."""
    return pd.read_sql("SELECT * FROM shift_logs", conn)




def compute_oee(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the three OEE components per shift-machine row.

    Availability = operating_time / available_time
    Performance  = ideal_units    / units_produced   (capped at 1.0)
    Quality      = units_good     / units_produced   (capped at 1.0)
    OEE          = Availability × Performance × Quality
    """
    df = df.copy()

    df["availability"] = (
        df["operating_time_min"] / df["available_time_min"]
    ).clip(0, 1)

    df["performance"] = np.where(
        df["units_produced"] > 0,
        (df["ideal_units"] / df["units_produced"]).clip(0, 1),
        0.0,
    )

    df["quality"] = np.where(
        df["units_produced"] > 0,
        (df["units_good"] / df["units_produced"]).clip(0, 1),
        0.0,
    )

    df["oee"]         = df["availability"] * df["performance"] * df["quality"]
    df["world_class"] = (df["oee"] >= OEE_WORLD_CLASS).astype(int)

    return df


def rolling_oee(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """
    Compute a rolling average OEE per machine (sorted by date+shift).
    window = number of shifts to average over.
    Returns DataFrame with added 'rolling_oee' column.
    """
    df = df.copy()
    shift_order = {"Morning": 0, "Afternoon": 1, "Night": 2}
    df["shift_num"] = df["shift"].map(shift_order)
    df = df.sort_values(["machine", "date", "shift_num"])
    df["rolling_oee"] = (
        df.groupby("machine")["oee"]
          .transform(lambda x: x.rolling(window, min_periods=1).mean())
    )
    return df


def pareto_downtime(df: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    """
    Pareto analysis: rank downtime causes by total minutes lost.
    Returns top_n causes with cumulative % — classic 80/20 tool.
    """
    totals = (
        df.groupby("primary_cause")["unplanned_downtime_min"]
          .sum()
          .reset_index()
          .rename(columns={"unplanned_downtime_min": "total_downtime_min"})
          .sort_values("total_downtime_min", ascending=False)
          .reset_index(drop=True)
    )
    totals = totals[totals["primary_cause"] != "None"]
    grand_total = totals["total_downtime_min"].sum()
    totals["pct"]       = totals["total_downtime_min"] / grand_total * 100
    totals["cum_pct"]   = totals["pct"].cumsum()
    totals["rank"]      = range(1, len(totals) + 1)
    return totals.head(top_n)


def takt_vs_actual(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare actual cycle time vs takt time per machine.
    Flag as bottleneck if actual_cycle_time > TAKT_TIME_S.
    Returns per-machine summary with bottleneck flag.
    """
    summary = (
        df.groupby("machine")
          .agg(
              avg_actual_ct=("actual_cycle_time_s", "mean"),
              avg_ideal_ct =("ideal_cycle_time_s",  "mean"),
              total_units  =("units_produced",       "sum"),
          )
          .reset_index()
    )
    summary["takt_time_s"]  = TAKT_TIME_S
    summary["bottleneck"]   = summary["avg_actual_ct"] > TAKT_TIME_S
    summary["ct_gap_s"]     = summary["avg_actual_ct"] - summary["takt_time_s"]
    return summary


def hourly_quality_heatmap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a machine × shift reject-rate matrix for the quality heatmap.
    reject_rate = units_rejected / units_produced
    """
    df = df.copy()
    df["reject_rate"] = np.where(
        df["units_produced"] > 0,
        df["units_rejected"] / df["units_produced"],
        0.0
    )
    pivot = (
        df.pivot_table(
            index="machine",
            columns="shift",
            values="reject_rate",
            aggfunc="mean",
        )
        .round(4)
    )
    return pivot


def run_analytics(df: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    """Master analytics function: compute OEE, persist KPIs, return enriched df."""
    print("[3/4] Running analytics engine...")

    df_oee = compute_oee(df)
    df_oee = rolling_oee(df_oee)

    # Persist KPIs
    kpi_cols = ["date", "shift", "machine", "availability",
                "performance", "quality", "oee", "world_class"]
    df_oee[kpi_cols].to_sql("oee_kpis", conn, if_exists="replace", index=False)

    # Print summary stats
    mean_oee = df_oee["oee"].mean()
    wc_pct   = df_oee["world_class"].mean() * 100
    print(f"   Fleet avg OEE : {mean_oee:.1%}")
    print(f"   World-class % : {wc_pct:.1f}% of shifts ≥ 85%")

    pareto  = pareto_downtime(df_oee)
    takt    = takt_vs_actual(df_oee)
    bottles = takt[takt["bottleneck"]]["machine"].tolist()

    print(f"\n   Top-3 Downtime Causes (Pareto):")
    for _, row in pareto.iterrows():
        print(f"   {int(row['rank'])}. {row['primary_cause']:22s}  "
              f"{row['total_downtime_min']:6.0f} min  ({row['pct']:.1f}%)")

    print(f"\n   Bottleneck Machines (actual CT > takt {TAKT_TIME_S}s):")
    for _, row in takt.iterrows():
        flag = " *** BOTTLENECK" if row["bottleneck"] else ""
        print(f"   {row['machine']:12s}  avg CT={row['avg_actual_ct']:.1f}s{flag}")

    return df_oee


def build_dashboard(df_oee: pd.DataFrame) -> object:
    """
    Build and return a Plotly Dash app with:
    - OEE Gauge per machine (RAG coded: Red/Amber/Green vs 85% threshold)
    - 7-day OEE trend line chart
    - Pareto bar chart of downtime causes
    - Takt-time vs actual cycle time comparison
    - Shift × Machine quality yield heatmap
    """
    import plotly.graph_objects as go
    import plotly.express as px
    from dash import Dash, dcc, html

    app = Dash(__name__)

    #  colour helpers 
    def rag_color(oee_val: float) -> str:
        if oee_val >= 0.85:  return "#22c55e"   # Green
        if oee_val >= 0.65:  return "#f59e0b"   # Amber
        return "#ef4444"                         # Red

    #  1. OEE Gauges 
    machine_avg = df_oee.groupby("machine")["oee"].mean()
    gauge_figs  = []
    for machine, oee_val in machine_avg.items():
        fig = go.Figure(go.Indicator(
            mode   = "gauge+number+delta",
            value  = round(oee_val * 100, 1),
            delta  = {"reference": 85, "suffix": "%"},
            title  = {"text": machine, "font": {"size": 13}},
            gauge  = {
                "axis": {"range": [0, 100], "ticksuffix": "%"},
                "bar":  {"color": rag_color(oee_val)},
                "steps": [
                    {"range": [0,   65], "color": "#fef2f2"},
                    {"range": [65,  85], "color": "#fefce8"},
                    {"range": [85, 100], "color": "#f0fdf4"},
                ],
                "threshold": {
                    "line":  {"color": "#1e3a5f", "width": 3},
                    "thickness": 0.75,
                    "value": 85,
                },
            },
            number = {"suffix": "%", "font": {"size": 28}},
        ))
        fig.update_layout(height=220, margin=dict(t=40, b=10, l=20, r=20),
                          paper_bgcolor="#0f172a", font_color="white")
        gauge_figs.append(fig)

    #  2. 7-day OEE Trend 
    daily_oee = (
        df_oee.groupby(["date", "machine"])["oee"]
              .mean()
              .reset_index()
    )
    trend_fig = px.line(
        daily_oee, x="date", y="oee", color="machine",
        title="7-Day OEE Trend by Machine",
        labels={"oee": "OEE", "date": "Date"},
        color_discrete_sequence=px.colors.qualitative.Bold,
    )
    trend_fig.add_hline(
        y=OEE_WORLD_CLASS, line_dash="dash",
        line_color="#ef4444", annotation_text="World-class 85%",
        annotation_font_color="#ef4444",
    )
    trend_fig.update_layout(
        paper_bgcolor="#0f172a", plot_bgcolor="#1e293b",
        font_color="white", legend_bgcolor="#1e293b",
        yaxis=dict(tickformat=".0%", range=[0, 1.05]),
        height=360,
    )

    # ─ 3. Pareto Downtime 
    pareto_df = pareto_downtime(df_oee, top_n=len(DOWNTIME_CAUSES))
    pareto_fig = go.Figure()
    pareto_fig.add_bar(
        x=pareto_df["primary_cause"], y=pareto_df["total_downtime_min"],
        name="Downtime (min)", marker_color="#7c3aed",
    )
    pareto_fig.add_scatter(
        x=pareto_df["primary_cause"], y=pareto_df["cum_pct"],
        name="Cumulative %", yaxis="y2",
        line=dict(color="#f97316", width=2), mode="lines+markers",
    )
    pareto_fig.update_layout(
        title="Pareto Analysis — Downtime Causes",
        yaxis=dict(title="Total Downtime (min)"),
        yaxis2=dict(title="Cumulative %", overlaying="y", side="right",
                    range=[0, 110], ticksuffix="%"),
        paper_bgcolor="#0f172a", plot_bgcolor="#1e293b",
        font_color="white", height=340,
    )

    #  4. Takt vs Actual CT 
    takt_df   = takt_vs_actual(df_oee)
    takt_fig  = go.Figure()
    takt_fig.add_bar(
        x=takt_df["machine"], y=takt_df["avg_actual_ct"],
        name="Avg Actual CT",
        marker_color=[rag_color(0 if b else 1) for b in takt_df["bottleneck"]],
    )
    takt_fig.add_bar(
        x=takt_df["machine"], y=takt_df["avg_ideal_ct"],
        name="Ideal CT", marker_color="#0ea5e9",
    )
    takt_fig.add_hline(
        y=TAKT_TIME_S, line_dash="dot", line_color="#facc15",
        annotation_text=f"Takt Time = {TAKT_TIME_S}s",
        annotation_font_color="#facc15",
    )
    takt_fig.update_layout(
        title="Takt Time vs Actual Cycle Time",
        yaxis_title="Seconds per Unit", barmode="group",
        paper_bgcolor="#0f172a", plot_bgcolor="#1e293b",
        font_color="white", height=340,
    )

    #  5. Quality Heatmap 
    hm_pivot  = hourly_quality_heatmap(df_oee)
    heat_fig  = go.Figure(go.Heatmap(
        z    = hm_pivot.values * 100,
        x    = hm_pivot.columns.tolist(),
        y    = hm_pivot.index.tolist(),
        colorscale = "RdYlGn_r",
        text = [[f"{v*100:.1f}%" for v in row] for row in hm_pivot.values],
        texttemplate = "%{text}",
        colorbar = dict(title="Reject %", ticksuffix="%"),
        zmin=0, zmax=12,
    ))
    heat_fig.update_layout(
        title="Quality Yield — Reject Rate by Shift & Machine",
        paper_bgcolor="#0f172a", plot_bgcolor="#1e293b",
        font_color="white", height=320,
    )

    #  Layout 
    app.layout = html.Div(style={"backgroundColor": "#0d1117", "padding": "20px",
                                  "fontFamily": "Inter, sans-serif"}, children=[

        html.H1("Production Line OEE Dashboard",
                style={"color": "#f8fafc", "textAlign": "center",
                       "borderBottom": "2px solid #7c3aed", "paddingBottom": "10px"}),
        html.P(f"World-class threshold: {OEE_WORLD_CLASS:.0%}  |  "
               f"Takt time: {TAKT_TIME_S}s/unit  |  "
               f"Simulation: {SIMULATION_DAYS} days",
               style={"color": "#94a3b8", "textAlign": "center", "marginTop": "-8px"}),

        # Gauge row
        html.Div(
            [dcc.Graph(figure=fig, style={"flex": "1"}) for fig in gauge_figs],
            style={"display": "flex", "gap": "10px", "marginTop": "16px"},
        ),

        # Trend + Pareto
        html.Div([
            dcc.Graph(figure=trend_fig, style={"flex": "1.4"}),
            dcc.Graph(figure=pareto_fig, style={"flex": "1"}),
        ], style={"display": "flex", "gap": "10px", "marginTop": "10px"}),

        # Takt + Heatmap
        html.Div([
            dcc.Graph(figure=takt_fig,  style={"flex": "1"}),
            dcc.Graph(figure=heat_fig,  style={"flex": "1"}),
        ], style={"display": "flex", "gap": "10px", "marginTop": "10px"}),
    ])

    return app


# 
# MAIN
# 
if __name__ == "__main__":
    # 1. Generate simulated data
    df_raw = generate_all_logs()

    # 2. Persist to SQLite
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)
    persist_logs(df_raw, conn)

    # 3. Run analytics
    df_oee = run_analytics(df_raw, conn)

    # 4. Launch dashboard
    print("\n[4/4] Starting Dash dashboard...")
    print("   Open http://127.0.0.1:8050 in your browser\n")
    app = build_dashboard(df_oee)
    app.run(debug=False, host="0.0.0.0", port=8050)

    conn.close()
