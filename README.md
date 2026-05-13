# OpenSRE Mini

AI-powered SRE incident analysis assistant — runs entirely on the GCP free tier.

Ingests logs + metrics → builds structured incident context → runs RCA via Gemini → evaluates accuracy.

## Architecture

```
POST /analyze
      ↓
  Log Parser          (extract errors, detect services)
  Metrics Parser      (anomaly thresholds, severity)
      ↓
  Context Builder     (correlate signals → compact JSON)
      ↓
  Gemini Agent        (structured RCA: issue, root cause, solution, confidence)
      ↓
  Evaluation Engine   (keyword + completeness scoring)
      ↓
  SQLite Storage      (persist incident + evaluation)
      ↓
  JSON Response
```

## Quickstart (local)

```bash
# 1. Clone and enter the project
cd opensre-mini

# 2. Set up environment
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY (optional — works without it)

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the server
uvicorn app.main:app --reload --port 8080

# 5. Test it
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "logs": "ERROR database timeout\nERROR retry failed\nERROR connection pool exhausted",
    "metrics": {"cpu": 92, "memory": 84, "latency": 1200, "error_rate": 15},
    "events": ["deployment_started"]
  }'
```

Open `http://localhost:8080/docs` for the interactive API docs.

## Docker

```bash
docker-compose up --build
```

## Run tests

```bash
pip install pytest
pytest tests/ -v
```

## Getting a Gemini API key (free)

1. Go to https://aistudio.google.com/app/apikey
2. Create a key (free tier, no credit card required)
3. Add it to `.env` as `GEMINI_API_KEY=your-key-here`

Without a key, the system uses a built-in rule-based fallback — useful for development.

## GCP Cloud Run deployment

```bash
# 1. Enable APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

# 2. Build and push
gcloud builds submit --tag gcr.io/YOUR_PROJECT/opensre-mini

# 3. Deploy
gcloud run deploy opensre-mini \
  --image gcr.io/YOUR_PROJECT/opensre-mini \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=your-key-here
```

## Incident scenarios

Pre-built test scenarios are in `scenarios/`:

- `db_timeout.json` — database connection timeout
- `cpu_exhaustion.json` — CPU resource exhaustion
- `memory_leak.json` — OOM memory leak

Run a scenario:
```bash
cat scenarios/db_timeout.json | python -c "
import json, sys, urllib.request
data = json.load(sys.stdin)
payload = json.dumps({
  'logs': data['logs'],
  'metrics': data['metrics'],
  'events': data['events']
}).encode()
req = urllib.request.Request('http://localhost:8080/analyze',
  data=payload, headers={'Content-Type': 'application/json'}, method='POST')
print(json.loads(urllib.request.urlopen(req).read()))
"
```

## Project structure

```
opensre-mini/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── routes/analyze.py    # API endpoints
│   ├── parser/
│   │   ├── log_parser.py    # Error extraction, service detection
│   │   └── metrics_parser.py # Anomaly thresholds, severity
│   ├── services/
│   │   ├── context_builder.py # Correlation engine
│   │   └── storage.py       # SQLite persistence
│   ├── agent/gemini_agent.py  # AI RCA + rule-based fallback
│   ├── evaluator/scorer.py    # Keyword + completeness scoring
│   └── models/schemas.py      # Pydantic request/response models
├── scenarios/               # Test incident scenarios
├── data/logs/               # Sample log files
├── data/metrics/            # Sample metric snapshots
├── tests/test_pipeline.py   # Unit tests
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
