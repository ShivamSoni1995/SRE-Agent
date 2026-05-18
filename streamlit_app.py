import streamlit as st
import requests
from datetime import datetime

st.set_page_config(
    page_title="OpenSRE Mini",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.status-open         { color: #f44336; font-weight: bold; }
.status-acknowledged { color: #ff9800; font-weight: bold; }
.status-resolved     { color: #4CAF50; font-weight: bold; }
.source-badge {
    background: #e3f2fd; border-radius: 4px;
    padding: 2px 8px; font-size: 11px; color: #1565c0;
}
div[data-testid="stChatMessage"] { margin-bottom: 4px; }
</style>
""", unsafe_allow_html=True)

STATUS_COLORS = {
    "open": "🔴", "acknowledged": "🟡", "resolved": "🟢"
}

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
        "logs": "WARN memory usage high\nERROR OOM killer invoked\nERROR process killed signal 9",
        "metrics": {"cpu": 45, "memory": 97, "latency": 800, "error_rate": 8},
        "events": [],
    },
    "Broken deployment": {
        "logs": "ERROR startup failed\nERROR crash loop detected\nERROR service killed",
        "metrics": {"cpu": 60, "memory": 55, "latency": 2000, "error_rate": 40},
        "events": ["deployment_started", "rollout_started"],
    },
    "API rate limiting": {
        "logs": "ERROR 429 too many requests\nERROR rate limit exceeded\nWARN request throttled",
        "metrics": {"cpu": 35, "memory": 40, "latency": 600, "error_rate": 30},
        "events": ["traffic_spike"],
    },
}


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ OpenSRE Mini")
    api_url = st.text_input(
        "API URL",
        value="http://localhost:8080",
        help="Cloud Run URL or local server",
    )
    api_key = st.text_input(
        "API Key",
        type="password",
        help="Leave empty if auth is disabled (dev mode)",
    )

    def headers():
        h = {"Content-Type": "application/json"}
        if api_key:
            h["Authorization"] = f"Bearer {api_key}"
        return h

    st.divider()
    try:
        r = requests.get(f"{api_url}/health", timeout=3)
        d = r.json()
        if r.status_code == 200:
            st.success(f"v{d.get('version','?')} connected")
            st.caption(f"Auth: {d.get('auth','unknown')}")
        else:
            st.error("API error")
    except Exception:
        st.warning("API unreachable")

    st.divider()
    st.caption("OpenSRE Mini · v0.3.0")


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_analyze, tab_history, tab_chat, tab_scenario, tab_webhooks = st.tabs([
    "🔍 Analyze",
    "📋 History",
    "💬 Chat",
    "🧪 Scenarios",
    "🔗 Webhooks",
])


# ── Tab 1: Analyze ────────────────────────────────────────────────────────────

with tab_analyze:
    st.header("Analyze an incident")

    col1, col2 = st.columns([3, 2], gap="large")

    with col1:
        logs = st.text_area(
            "Raw logs", height=180,
            placeholder="ERROR database timeout\nERROR retry failed",
        )
        events_raw = st.text_input(
            "Recent events (comma-separated)",
            placeholder="deployment_started, config_change",
        )
    with col2:
        st.subheader("Metrics")
        cpu        = st.slider("CPU %",        0, 100,  50)
        memory     = st.slider("Memory %",     0, 100,  60)
        latency    = st.slider("Latency (ms)", 0, 5000, 300, step=50)
        error_rate = st.slider("Error rate %", 0, 100,  2)

    if st.button("🚀 Analyze", type="primary", disabled=not logs.strip()):
        with st.spinner("Running pipeline..."):
            try:
                payload = {
                    "logs": logs,
                    "metrics": {"cpu": cpu, "memory": memory,
                                "latency": latency, "error_rate": error_rate},
                    "events": [e.strip() for e in events_raw.split(",") if e.strip()],
                }
                resp = requests.post(
                    f"{api_url}/analyze", json=payload,
                    headers=headers(), timeout=30,
                )
                resp.raise_for_status()
                r = resp.json()

                st.success(f"**{r['incident_id']}** created")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Confidence",    f"{round(r['confidence']*100)}%")
                c2.metric("Eval score",    f"{round(r['evaluation_score']*100)}%")
                c3.metric("Semantic",      f"{round(r['semantic_score']*100)}%" if r.get('semantic_score') else "n/a")
                c4.metric("Keywords",      len(r.get('matched_keywords', [])))

                st.info(f"**Issue:** {r['issue']}")
                st.error(f"**Root cause:** {r['root_cause']}")

                st.subheader("Remediation")
                for i, s in enumerate(r['solution'].split('. '), 1):
                    if s.strip():
                        st.write(f"**{i}.** {s.strip()}")

                st.caption(f"Scoring: `{r.get('scoring_method','n/a')}`")

                with st.expander("Raw JSON"):
                    st.json(r)

            except requests.exceptions.ConnectionError:
                st.error("Cannot connect — check the API URL in the sidebar.")
            except Exception as e:
                st.error(f"Failed: {e}")


# ── Tab 2: History ────────────────────────────────────────────────────────────

with tab_history:
    st.header("Incident history")

    col_r, col_l = st.columns([1, 3])
    with col_r:
        refresh = st.button("🔄 Refresh")
    with col_l:
        limit = st.select_slider("Show last", [5, 10, 20, 50], value=20)

    try:
        resp = requests.get(
            f"{api_url}/incidents?limit={limit}",
            headers=headers(), timeout=10,
        )
        incidents = resp.json() if resp.status_code == 200 else []
    except Exception:
        incidents = []
        st.warning("Could not load incidents.")

    if not incidents:
        st.info("No incidents yet — run an analysis first.")
    else:
        st.caption(f"{len(incidents)} incident(s)")
        for inc in incidents:
            full      = inc.get("full_response", {})
            lifecycle = full.get("lifecycle", {"status": "open"})
            status    = lifecycle.get("status", "open")
            emoji     = STATUS_COLORS.get(status, "⚪")
            source    = full.get("source", "api")
            score     = inc.get("evaluation_score", 0)
            ts        = inc.get("timestamp", "")[:19].replace("T", " ")
            iid       = inc.get("id", "?")

            with st.expander(
                f"{emoji} **{iid}** · {ts} · {status.upper()} · "
                f"score {round(score*100)}% · `{source}`"
            ):
                # Status controls
                st.write("**Update status**")
                sc1, sc2, sc3, sc4 = st.columns([2, 1, 1, 1])
                actor = sc1.text_input("Actor", value="on-call-engineer", key=f"actor_{iid}")
                if sc2.button("Acknowledge", key=f"ack_{iid}"):
                    try:
                        requests.post(
                            f"{api_url}/incidents/{iid}/status",
                            json={"status": "acknowledged", "actor": actor},
                            headers=headers(), timeout=5,
                        )
                        st.success("Acknowledged — refresh to see update")
                    except Exception as e:
                        st.error(str(e))
                if sc3.button("Resolve", key=f"res_{iid}"):
                    try:
                        requests.post(
                            f"{api_url}/incidents/{iid}/status",
                            json={"status": "resolved", "actor": actor,
                                  "note": "Resolved via UI"},
                            headers=headers(), timeout=5,
                        )
                        st.success("Resolved — refresh to see update")
                    except Exception as e:
                        st.error(str(e))

                st.divider()

                mc1, mc2 = st.columns(2)
                mc1.metric("Confidence",  f"{round(inc.get('confidence',0)*100)}%")
                mc2.metric("Eval score",  f"{round(score*100)}%")

                st.write("**Root cause**")
                st.write(inc.get("root_cause", "—"))

                m = inc.get("metrics", {})
                if m:
                    mm1, mm2, mm3, mm4 = st.columns(4)
                    mm1.metric("CPU",     f"{m.get('cpu',0)}%")
                    mm2.metric("Memory",  f"{m.get('memory',0)}%")
                    mm3.metric("Latency", f"{m.get('latency',0)}ms")
                    mm4.metric("Errors",  f"{m.get('error_rate',0)}%")

                conv = full.get("conversation", [])
                if conv:
                    st.write(f"**Chat history** ({len(conv)} messages)")
                    for msg in conv[-4:]:
                        role = "🧑" if msg["role"] == "user" else "🤖"
                        st.caption(f"{role} {msg['content'][:120]}")

                st.caption("Full response")
                st.json(full)


# ── Tab 3: Chat ───────────────────────────────────────────────────────────────

with tab_chat:
    st.header("💬 Chat with an incident")
    st.caption("Ask follow-up questions about any stored incident.")

    incident_id = st.text_input(
        "Incident ID", placeholder="INC-6F18F2",
        help="Copy from History tab or from an /analyze response",
    )

    if incident_id:
        # Load and display history
        try:
            hist_resp = requests.get(
                f"{api_url}/incidents/{incident_id}/chat",
                headers=headers(), timeout=5,
            )
            if hist_resp.status_code == 200:
                history = hist_resp.json().get("messages", [])
            else:
                history = []
                st.warning(f"Incident {incident_id} not found.")
        except Exception:
            history = []
            st.warning("Could not load chat history.")

        # Display conversation
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            with st.chat_message(role):
                st.write(msg["content"])

        # Input
        user_input = st.chat_input("Ask about this incident...")
        if user_input:
            with st.chat_message("user"):
                st.write(user_input)
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        resp = requests.post(
                            f"{api_url}/incidents/{incident_id}/chat",
                            json={"message": user_input},
                            headers=headers(), timeout=20,
                        )
                        resp.raise_for_status()
                        response = resp.json()["response"]
                        st.write(response)
                    except Exception as e:
                        st.error(f"Chat failed: {e}")
    else:
        st.info("Enter an incident ID above to start chatting.")

        st.subheader("Example questions to ask")
        examples = [
            "Why did this happen after the deployment?",
            "What should I check first?",
            "How long will the fix take?",
            "Is this related to the last incident we had?",
            "What monitoring should I add to catch this earlier?",
            "Walk me through the remediation steps in more detail.",
        ]
        for ex in examples:
            st.caption(f"💬 {ex}")


# ── Tab 4: Scenarios ──────────────────────────────────────────────────────────

with tab_scenario:
    st.header("🧪 Test scenarios")

    chosen = st.selectbox("Select scenario", list(SCENARIOS.keys()))
    scenario = SCENARIOS[chosen]

    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("Logs")
        st.code(scenario["logs"])
        st.write("**Events:**", scenario["events"] or "None")
    with col_r:
        st.subheader("Metrics")
        m = scenario["metrics"]
        st.metric("CPU",        f"{m['cpu']}%")
        st.metric("Memory",     f"{m['memory']}%")
        st.metric("Latency",    f"{m['latency']}ms")
        st.metric("Error rate", f"{m['error_rate']}%")

    if st.button("▶ Run scenario", type="primary"):
        with st.spinner(f"Running '{chosen}'..."):
            try:
                resp = requests.post(
                    f"{api_url}/analyze", json=scenario,
                    headers=headers(), timeout=30,
                )
                resp.raise_for_status()
                r = resp.json()

                st.success(f"Done — **{r['incident_id']}**")
                c1, c2, c3 = st.columns(3)
                c1.metric("Confidence",  f"{round(r['confidence']*100)}%")
                c2.metric("Eval score",  f"{round(r['evaluation_score']*100)}%")
                c3.metric("Semantic",    f"{round(r['semantic_score']*100)}%" if r.get('semantic_score') else "n/a")

                st.info(r["issue"])
                st.error(r["root_cause"])

                for i, s in enumerate(r["solution"].split(". "), 1):
                    if s.strip():
                        st.write(f"**{i}.** {s.strip()}")

                st.caption(f"Incident ID for chat: `{r['incident_id']}`")

                with st.expander("Raw JSON"):
                    st.json(r)

            except Exception as e:
                st.error(f"Scenario failed: {e}")


# ── Tab 5: Webhooks ───────────────────────────────────────────────────────────

with tab_webhooks:
    st.header("🔗 Webhook integration")
    st.caption("Test reactive alert ingestion from Grafana or PagerDuty.")

    st.subheader("Grafana alert webhook")
    st.markdown(f"""
Configure in Grafana: **Alerting → Contact points → Add webhook**

```
URL:    {api_url}/webhook/grafana
Method: POST
```

Or test it directly:
""")

    graf_alert = st.selectbox(
        "Simulate alert type",
        ["High CPU", "High Latency", "High Memory", "High Error Rate"],
    )

    GRAFANA_PAYLOADS = {
        "High CPU": {
            "status": "firing",
            "alerts": [{
                "labels": {"alertname": "HighCPU", "severity": "critical", "service": "api"},
                "annotations": {"summary": "CPU above 95% for 5 minutes"},
                "values": {"cpu": 96, "memory": 60, "latency": 400, "error_rate": 3},
            }],
        },
        "High Latency": {
            "status": "firing",
            "alerts": [{
                "labels": {"alertname": "HighLatency", "severity": "warning", "service": "database"},
                "annotations": {"summary": "P99 latency above 2s"},
                "values": {"cpu": 70, "memory": 65, "latency": 2200, "error_rate": 8},
            }],
        },
        "High Memory": {
            "status": "firing",
            "alerts": [{
                "labels": {"alertname": "HighMemory", "severity": "critical", "service": "worker"},
                "annotations": {"summary": "Memory above 90%, possible leak"},
                "values": {"cpu": 40, "memory": 93, "latency": 500, "error_rate": 5},
            }],
        },
        "High Error Rate": {
            "status": "firing",
            "alerts": [{
                "labels": {"alertname": "HighErrorRate", "severity": "critical", "service": "api"},
                "annotations": {"summary": "Error rate above 20%"},
                "values": {"cpu": 55, "memory": 60, "latency": 900, "error_rate": 22},
            }],
        },
    }

    with st.expander("Preview payload"):
        st.json(GRAFANA_PAYLOADS[graf_alert])

    if st.button("🚨 Fire Grafana alert", type="primary"):
        with st.spinner("Sending alert..."):
            try:
                resp = requests.post(
                    f"{api_url}/webhook/grafana",
                    json=GRAFANA_PAYLOADS[graf_alert],
                    headers=headers(), timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()
                results = result.get("results", [])

                if results and results[0].get("status") == "deduplicated":
                    st.warning(
                        f"Deduplicated — same alert seen recently "
                        f"(last: {results[0].get('last_seen','')}). "
                        f"Cooldown prevents duplicate analysis."
                    )
                else:
                    for r in results:
                        if r.get("incident_id"):
                            st.success(f"Incident **{r['incident_id']}** created automatically")
                            st.write(f"**Root cause:** {r.get('root_cause','')}")
                            st.caption(f"Use incident ID in Chat tab to ask follow-up questions")

                with st.expander("Full response"):
                    st.json(result)

            except Exception as e:
                st.error(f"Webhook failed: {e}")

    st.divider()
    st.subheader("Deduplication status")
    if st.button("Check dedup store"):
        try:
            resp = requests.get(
                f"{api_url}/system/dedup",
                headers=headers(), timeout=5,
            )
            st.json(resp.json())
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.subheader("Grafana alerting setup guide")
    st.markdown(f"""
1. In Grafana, go to **Alerting → Contact points**
2. Click **Add contact point**
3. Set type to **Webhook**
4. Set URL to `{api_url}/webhook/grafana`
5. Save and assign to an alert rule

When the alert fires, OpenSRE will:
- Auto-analyze the incident
- Post RCA to Slack (if severity ≥ threshold)
- Store in Firestore with full lifecycle tracking
- Deduplicate repeat alerts within {10}-minute cooldown
""")


# ── Live Feed tab (appended) ──────────────────────────────────────────────────
# Note: In production, add this as a 6th tab in the tabs declaration above.
# For now, expose as a standalone page via st.sidebar navigation.

st.sidebar.divider()
if st.sidebar.button("📡 Open Live Feed"):
    st.session_state["show_live"] = True

if st.session_state.get("show_live"):
    st.divider()
    st.header("📡 Live Incident Feed")
    st.caption(
        "Incidents auto-detected by the correlation engine appear here. "
        "Refreshes every 5 seconds."
    )

    col_stop, col_test = st.columns([1, 3])
    if col_stop.button("⏹ Stop feed"):
        st.session_state["show_live"] = False
        st.rerun()

    with col_test:
        st.caption("Trigger a test burst to see the pipeline in action:")
        test_service = st.selectbox(
            "Service",
            ["opensre-mini", "payments-api", "auth-service", "database"],
            key="live_service",
        )
        test_msg = st.selectbox(
            "Signal",
            [
                "database timeout: connection pool exhausted",
                "OOM killer invoked — process killed",
                "CPU saturation: worker threads blocked",
                "ERROR crash loop detected: service restarting",
                "429 rate limit exceeded",
            ],
            key="live_msg",
        )
        test_count = st.slider("Burst count", 1, 50, 25, key="live_count")

        if st.button("🚨 Fire test burst", type="primary"):
            try:
                resp = requests.post(
                    f"{api_url}/ingest/test",
                    json={
                        "service":  test_service,
                        "severity": "ERROR",
                        "message":  test_msg,
                        "count":    test_count,
                    },
                    headers=headers(),
                    timeout=5,
                )
                resp.raise_for_status()
                r = resp.json()
                st.success(
                    f"✅ {r['events_queued']} events queued. "
                    f"Correlation engine processing... watch the feed below."
                )
            except Exception as e:
                st.error(f"Failed: {e}")

    # Auto-refresh loop
    import time as _time
    placeholder = st.empty()
    for _ in range(60):   # run for ~5 minutes then stop
        try:
            resp = requests.get(
                f"{api_url}/incidents?limit=10",
                headers=headers(),
                timeout=5,
            )
            incidents = resp.json() if resp.status_code == 200 else []
        except Exception:
            incidents = []

        # Show correlation engine status
        try:
            status_resp = requests.get(
                f"{api_url}/ingest/status",
                headers=headers(),
                timeout=3,
            )
            engine_stats = status_resp.json() if status_resp.status_code == 200 else {}
        except Exception:
            engine_stats = {}

        with placeholder.container():
            if engine_stats:
                w = engine_stats.get("windows", {})
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric(
                    "Active windows",
                    w.get("active_windows", 0),
                )
                sc2.metric(
                    "Events in windows",
                    w.get("total_active_events", 0),
                )
                c = engine_stats.get("candidates", {})
                sc3.metric(
                    "Incidents triggered",
                    c.get("total_triggered", 0),
                )

            st.divider()
            st.subheader(f"Recent incidents ({len(incidents)})")

            if not incidents:
                st.info(
                    "No incidents yet. "
                    "Fire a test burst above to trigger the correlation engine."
                )
            else:
                for inc in incidents[:5]:
                    full   = inc.get("full_response", {})
                    source = full.get("source", "api")
                    itype  = full.get("incident_type", "manual")
                    status = full.get("lifecycle", {}).get("status", "open")
                    emoji  = STATUS_COLORS.get(status, "⚪")
                    ts     = inc.get("timestamp", "")[:19].replace("T", " ")

                    source_label = (
                        "🤖 auto-detected" if source == "correlation_engine"
                        else f"📥 {source}"
                    )

                    with st.expander(
                        f"{emoji} **{inc.get('id','?')}** · {ts} · "
                        f"{itype} · {source_label}"
                    ):
                        st.write(f"**Root cause:** {inc.get('root_cause','—')}")
                        st.write(
                            f"Confidence: `{round(inc.get('confidence',0)*100)}%` · "
                            f"Eval: `{round(inc.get('evaluation_score',0)*100)}%`"
                        )
                        if full.get("rule"):
                            st.caption(f"Rule: `{full['rule']}`")

        _time.sleep(5)
        if not st.session_state.get("show_live"):
            break
