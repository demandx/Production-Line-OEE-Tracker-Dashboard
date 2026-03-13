**Production Line OEE Tracker & Dashboard**
I built this to get hands-on with the kind of analytics work that actually happens in process engineering — tracking machine performance, finding downtime patterns, and giving a shop floor manager something useful to look at without digging through spreadsheets.
OEE (Overall Equipment Effectiveness) is the standard KPI for manufacturing lines. This project simulates a week of production data across 5 machines, computes OEE and related metrics with Pandas, stores everything in SQLite, and serves an interactive dashboard on localhost via Plotly Dash.
bashpython oee_pipeline.py
Then open **http://127.0.0.1:8050** in your browser.

**What OEE actually means
OEE = Availability × Performance × Quality**

Availability : what fraction of scheduled time was the machine actually running (unplanned downtime eats into this)
Performance : when it was running, how fast compared to its design spec
Quality : of everything it produced, how much was good (not rejected)

85% is considered world-class. Most real lines sit between 40–60%. The dashboard RAG-codes each machine against the 85% threshold.

The simulated line
5 machines modelling an electronics SMT assembly line:
**MachineRoleIdeal Cycle TimeM01_SMTSurface Mount Technology — places components18sM02_ReflowReflow oven — solders components22sM03_AOIAutomated Optical Inspection12sM04_THTThrough-Hole Technology35sM05_ICTIn-Circuit Test28s
3 shifts per day × 7 days × 5 machines = 105 shift records total.
Takt time is set at 20s/unit — anything slower than that is a bottleneck. M04 and M05 are consistently over takt, which is intentional.**
How it works
Simulation
simulate_shift_log() generates one shift record per machine per shift. Unplanned downtime uses a triangular distribution (min=0, mode=15, max=90 minutes) — this gives you a realistic skew where most shifts have small outages and a few have big ones. Performance loss is a uniform random factor between 1.0× and 1.35× the ideal cycle time. Reject rate is random between 1% and 12%.
All of this gets stored as a pandas DataFrame, then written to SQLite.
Analytics
Four separate calculations, each a pure function that takes a DataFrame and returns one:
compute_oee() — vectorised A×P×Q per row using numpy.where for zero-safe division. Adds a world_class flag (1/0) for easy aggregation.
rolling_oee() — groups by machine, sorts chronologically by date+shift, applies a 3-shift rolling mean per machine. Shows whether a machine's performance is trending up or down rather than just the current snapshot.
pareto_downtime() — groups by downtime cause, sums minutes, sorts descending, adds cumulative % column. Classic Pareto — the top 2-3 causes usually account for 70-80% of total downtime.
takt_vs_actual() — compares each machine's average actual cycle time against takt time. Flags bottlenecks and calculates the gap in seconds.
Dashboard
Five charts on one page:

OEE Gauges : one per machine, RAG-coded (green ≥85%, amber ≥65%, red below that), delta shows distance from 85% target
7-day trend : daily OEE per machine as a line chart with the 85% threshold marked
Pareto chart : dual y-axis, bars for downtime minutes + cumulative % line
Takt vs actual CT : grouped bar chart, bottleneck machines shown in red
Quality heatmap : machine × shift reject rate matrix, RdYlGn_r colour scale


Project layout
Production Line OEE Tracker & Dashboard/
├── oee_pipeline.py       ← everything is in here
└── oee_production.db     ← created on first run (SQLite)
No separate files for the dashboard, no config files, nothing else needed.

Installation
bashpip install pandas plotly dash
On Ubuntu 24.04:
bashpip install pandas plotly dash --break-system-packages
sqlite3, json, random, datetime are all stdlib — nothing extra needed for those.

Running it
bashpython oee_pipeline.py
The terminal prints a summary as it runs:
[1/4] Simulating production logs...
   Generated 105 shift records

[2/4] Persisting data to SQLite...
   Wrote 105 rows to shift_logs table

[3/4] Running analytics engine...
   Fleet avg OEE : 81.4%
   World-class % : 22.9% of shifts ≥ 85%

   Top-3 Downtime Causes (Pareto):
   1. Mechanical Failure      312 min  (18.4%)
   2. Changeover              287 min  (16.9%)
   3. Material Shortage       261 min  (15.4%)

   Bottleneck Machines (actual CT > takt 20s):
   M04_THT       avg CT=38.2s  *** BOTTLENECK
   M05_ICT       avg CT=30.1s  *** BOTTLENECK

[4/4] Starting Dash dashboard...
   Open http://127.0.0.1:8050 in your browser
Then open the browser. The server keeps running until you Ctrl+C it.

Changing things
At the top of oee_pipeline.py:
pythonSIMULATION_DAYS      = 7     # how many days to simulate
TAKT_TIME_S          = 20    # customer demand rate
OEE_WORLD_CLASS      = 0.85  # benchmark threshold for RAG coding
PLANNED_DOWNTIME_MIN = 30    # scheduled maintenance per shift
To add or remove machines, edit the MACHINES list and IDEAL_CYCLE_TIME_S dict. The rest of the pipeline adapts automatically.

Connecting to real data
The simulator exists so the project runs without any external dependencies. To replace it with real machine data:

Skip generate_all_logs() and load your MES/SCADA export as a CSV into pandas instead
Make sure your DataFrame has these columns: date, shift, machine, available_time_min, planned_downtime_min, unplanned_downtime_min, operating_time_min, ideal_cycle_time_s, actual_cycle_time_s, units_produced, units_rejected, units_good, ideal_units, primary_cause
Pass that DataFrame straight into run_analytics() — nothing else changes

To use PostgreSQL instead of SQLite, change the connection string in sqlite3.connect(). The pandas to_sql / read_sql calls are identical for both.

Stack
Python 3.12 · Pandas · NumPy · Plotly · Dash · SQLite
