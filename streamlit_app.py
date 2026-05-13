import streamlit as st
import requests
import json
from datetime import datetime

st.set_page_config(
    page_title="OpenSRE Mini",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .metric-card {
        background: #f8f9fa;
        border-radius: 8px;
        padding: 16px;
        border-left: 4px solid #4CAF50;
    }
    .critical { border-left-color: #f44336 !important; }
    .warning  { border-left-color: #ff9800 !important; }
    .normal   { border-left-color: #4CAF50 !important; }
    .confidence-bar {
        height: 8px;
        border-radius: 4px;
        background: #e0e0e0;
        margin-top: 4px;
    }
    .confidence-fill {
        height: 100%;
        border-radius: 4px;
        background: linear-gradient(90deg, #4CAF50, #2196F3);
    }
</style>
""", unsafe_allow_html=True)


# ── Sidebar config ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Config")
    api_url = st.text_input(
        "API URL",
        value="http://localhost:8080",
        help="Your Cloud Run URL or local server"
    )
    st.divider()
    st.caption("OpenSRE Mini · Phase 2")

    # Health check
    try:
        r = requests.get(f"{api_url}/health", timeout=3)
        if r.status_code == 200:
            st.success("API connected")
        else:
            st.error("API error")
    except Exception:
        st.warning("API unreachable")


# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_analyze, tab_history, tab_scenario = st.tabs([
    "🔍 Analyze incident",
    "📋 Incident history",
    "🧪 Run scenario",
])


# ── Tab 1: Analyze ─────────────────────────────────────────────────────────────

with tab_analyze:
    st.header("Analyze an incident")
    st.caption("Paste your logs, set your metrics, and get an AI-generated RCA.")

    col1, col2 = st.columns([3, 2], gap="large")

    with col1:
        logs = st.text_area(
            "Raw logs",
            height=200,
            placeholder="ERROR database timeout\nERROR retry failed\nERROR connection pool exhausted",
        )
        events = st.text_input(
            "Recent events (comma-separated)",
            placeholder="deployment_started, config_change",
        )

    with col2:
        st.subheader("Infrastructure metrics")
        cpu = st.slider("CPU %", 0, 100, 50)
        memory = st.slider("Memory %", 0, 100, 60)
        latency = st.slider("Latency (ms)", 0, 5000, 300, step=50)
        error_rate = st.slider("Error rate %", 0, 100, 2)

    st.divider()

    if st.button("🚀 Analyze", type="primary", disabled=not logs.strip()):
        with st.spinner("Running RCA pipeline..."):
            try:
                payload = {
                    "logs": logs,
                    "metrics": {
                        "cpu": cpu,
                        "memory": memory,
                        "latency": latency,
                        "error_rate": error_rate,
                    },
                    "events": [e.strip() for e in events.split(",") if e.strip()],
                }
                resp = requests.post(
                    f"{api_url}/analyze",
                    json=payload,
                    timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()

                # ── Results ────────────────────────────────────────────────────
                st.success(f"Incident **{result['incident_id']}** analyzed")

                c1, c2, c3 = st.columns(3)
                c1.metric("Confidence", f"{round(result['confidence'] * 100)}%")
                c2.metric("Eval score", f"{round(result['evaluation_score'] * 100)}%")
                c3.metric("Keywords matched", len(result.get("matched_keywords", [])))

                st.subheader("Issue")
                st.info(result["issue"])

                st.subheader("Root cause")
                st.error(result["root_cause"])

                st.subheader("Remediation steps")
                steps = result["solution"].split(". ")
                for i, step in enumerate(steps, 1):
                    if step.strip():
                        st.write(f"**{i}.** {step.strip()}")

                if result.get("matched_keywords"):
                    st.subheader("Matched keywords")
                    st.write(" · ".join(
                        f"`{kw}`" for kw in result["matched_keywords"]
                    ))

                with st.expander("Raw JSON response"):
                    st.json(result)

            except requests.exceptions.ConnectionError:
                st.error("Cannot connect to the API. Check the URL in the sidebar.")
            except Exception as e:
                st.error(f"Analysis failed: {e}")
    elif not logs.strip():
        st.caption("Paste some logs above to enable analysis.")


# ── Tab 2: History ─────────────────────────────────────────────────────────────

with tab_history:
    st.header("Incident history")

    col_refresh, col_limit = st.columns([1, 3])
    with col_refresh:
        refresh = st.button("🔄 Refresh")
    with col_limit:
        limit = st.select_slider("Show last", options=[5, 10, 20, 50], value=20)

    try:
        resp = requests.get(f"{api_url}/incidents?limit={limit}", timeout=10)
        incidents = resp.json() if resp.status_code == 200 else []
    except Exception:
        incidents = []
        st.warning("Could not load incident history.")

    if not incidents:
        st.info("No incidents yet. Run an analysis first.")
    else:
        st.caption(f"{len(incidents)} incident(s) found")
        for inc in incidents:
            score = inc.get("evaluation_score", 0)
            confidence = inc.get("confidence", 0)
            ts = inc.get("timestamp", "")[:19].replace("T", " ")

            with st.expander(
                f"**{inc.get('id', '?')}** · {ts} · score {round(score * 100)}%"
            ):
                col_a, col_b = st.columns(2)
                col_a.metric("Confidence", f"{round(confidence * 100)}%")
                col_b.metric("Eval score", f"{round(score * 100)}%")

                st.write("**Root cause**")
                st.write(inc.get("root_cause", "—"))

                st.write("**Logs summary**")
                st.code(inc.get("logs_summary", "—"))

                metrics = inc.get("metrics", {})
                if metrics:
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("CPU", f"{metrics.get('cpu', 0)}%")
                    mc2.metric("Memory", f"{metrics.get('memory', 0)}%")
                    mc3.metric("Latency", f"{metrics.get('latency', 0)}ms")
                    mc4.metric("Errors", f"{metrics.get('error_rate', 0)}%")

                st.caption("Full RCA response")
                st.json(inc.get("full_response", {}))


# ── Tab 3: Scenarios ───────────────────────────────────────────────────────────

SCENARIOS = {
    "Database timeout": {
        "logs": "ERROR database timeout\nERROR retry failed\nERROR connection pool exhausted\nERROR database timeout",
        "metrics": {"cpu": 92, "memory": 84, "latency": 1200, "error_rate": 15},
        "events": ["deployment_started"],
    },
    "CPU exhaustion": {
        "logs": "WARN high cpu usage detected\nERROR request timeout\nERROR worker process killed",
        "metrics": {"cpu": 98, "memory": 70, "latency": 3500, "error_rate": 25},
        "events": [],
    },
    "Memory leak": {
        "logs": "WARN memory usage high\nERROR OOM killer invoked\nERROR process killed signal 9\nWARN memory usage high",
        "metrics": {"cpu": 45, "memory": 97, "latency": 800, "error_rate": 8},
        "events": [],
    },
    "Broken deployment": {
        "logs": "ERROR startup failed\nERROR crash loop detected\nERROR service killed\nERROR crash loop detected",
        "metrics": {"cpu": 60, "memory": 55, "latency": 2000, "error_rate": 40},
        "events": ["deployment_started", "rollout_started"],
    },
    "API rate limiting": {
        "logs": "ERROR 429 too many requests\nERROR rate limit exceeded\nWARN request throttled",
        "metrics": {"cpu": 35, "memory": 40, "latency": 600, "error_rate": 30},
        "events": ["traffic_spike"],
    },
}

with tab_scenario:
    st.header("Run a test scenario")
    st.caption("Pre-built incident scenarios to test the pipeline end-to-end.")

    chosen = st.selectbox("Select scenario", list(SCENARIOS.keys()))
    scenario = SCENARIOS[chosen]

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Logs")
        st.code(scenario["logs"])
        st.subheader("Events")
        st.write(scenario["events"] if scenario["events"] else "None")

    with col_right:
        st.subheader("Metrics")
        m = scenario["metrics"]
        st.metric("CPU", f"{m['cpu']}%")
        st.metric("Memory", f"{m['memory']}%")
        st.metric("Latency", f"{m['latency']}ms")
        st.metric("Error rate", f"{m['error_rate']}%")

    if st.button("▶ Run scenario", type="primary"):
        with st.spinner(f"Running '{chosen}'..."):
            try:
                resp = requests.post(
                    f"{api_url}/analyze",
                    json=scenario,
                    timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()

                st.success(f"Done — {result['incident_id']}")

                rc1, rc2 = st.columns(2)
                rc1.metric("Confidence", f"{round(result['confidence'] * 100)}%")
                rc2.metric("Eval score", f"{round(result['evaluation_score'] * 100)}%")

                st.subheader("Issue")
                st.info(result["issue"])
                st.subheader("Root cause")
                st.error(result["root_cause"])
                st.subheader("Solution")
                for i, s in enumerate(result["solution"].split(". "), 1):
                    if s.strip():
                        st.write(f"**{i}.** {s.strip()}")

                with st.expander("Raw JSON"):
                    st.json(result)

            except Exception as e:
                st.error(f"Scenario failed: {e}")
