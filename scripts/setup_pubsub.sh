#!/bin/bash
# setup_pubsub.sh — One-time GCP Pub/Sub + Cloud Logging sink setup
# Run this once to wire Cloud Logging → Pub/Sub → OpenSRE ingestion
#
# What this creates:
#   1. Pub/Sub topic:        opensre-logs
#   2. Cloud Logging sink:   opensre-logs-sink (routes Cloud Run logs to topic)
#   3. Push subscription:    opensre-logs-sub (delivers to /ingest/gcp)
#
# Cost: all within GCP free tier
#   - Pub/Sub: 10GB/month free
#   - Cloud Logging: 50GB/month free

set -e

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SERVICE_URL="https://opensre-mini-566951542571.us-central1.run.app"
TOPIC="opensre-logs"
SINK="opensre-logs-sink"
SUBSCRIPTION="opensre-logs-sub"
REGION="us-central1"

echo "=========================================="
echo "OpenSRE Mini — Pub/Sub Setup"
echo "Project: $PROJECT_ID"
echo "Service URL: $SERVICE_URL"
echo "=========================================="

# Step 1: Enable Pub/Sub API
echo ""
echo "Step 1: Enabling Pub/Sub API..."
gcloud services enable pubsub.googleapis.com

# Step 2: Create Pub/Sub topic
echo ""
echo "Step 2: Creating Pub/Sub topic: $TOPIC"
gcloud pubsub topics create $TOPIC \
  --project=$PROJECT_ID 2>/dev/null && echo "Topic created" || echo "Topic already exists"

# Step 3: Get the Cloud Logging service account and grant publish rights
echo ""
echo "Step 3: Granting Cloud Logging permission to publish to topic..."
LOGGING_SA="serviceAccount:cloud-logs@system.gserviceaccount.com"

gcloud pubsub topics add-iam-policy-binding $TOPIC \
  --member=$LOGGING_SA \
  --role="roles/pubsub.publisher" \
  --project=$PROJECT_ID

# Step 4: Create Cloud Logging sink — routes opensre-mini Cloud Run logs
echo ""
echo "Step 4: Creating Cloud Logging sink: $SINK"
echo "Filter: Cloud Run logs from opensre-mini service only"

gcloud logging sinks create $SINK \
  pubsub.googleapis.com/projects/$PROJECT_ID/topics/$TOPIC \
  --log-filter='resource.type="cloud_run_revision" AND resource.labels.service_name="opensre-mini"' \
  --project=$PROJECT_ID 2>/dev/null && echo "Sink created" || echo "Sink already exists"

# Grant the sink's service account publish rights
SINK_SA=$(gcloud logging sinks describe $SINK \
  --project=$PROJECT_ID \
  --format="value(writerIdentity)")

echo "Sink service account: $SINK_SA"
gcloud pubsub topics add-iam-policy-binding $TOPIC \
  --member=$SINK_SA \
  --role="roles/pubsub.publisher" \
  --project=$PROJECT_ID

# Step 5: Create push subscription pointing at /ingest/gcp
echo ""
echo "Step 5: Creating push subscription: $SUBSCRIPTION"
echo "Push endpoint: $SERVICE_URL/ingest/gcp"

gcloud pubsub subscriptions create $SUBSCRIPTION \
  --topic=$TOPIC \
  --push-endpoint="$SERVICE_URL/ingest/gcp" \
  --ack-deadline=30 \
  --message-retention-duration=10m \
  --project=$PROJECT_ID 2>/dev/null && echo "Subscription created" || echo "Subscription already exists"

# Step 6: Verify setup
echo ""
echo "=========================================="
echo "Setup complete. Verifying..."
echo ""
echo "Topic:"
gcloud pubsub topics describe $TOPIC --project=$PROJECT_ID --format="value(name)"
echo ""
echo "Subscription:"
gcloud pubsub subscriptions describe $SUBSCRIPTION --project=$PROJECT_ID \
  --format="value(pushConfig.pushEndpoint)"
echo ""
echo "Logging sink:"
gcloud logging sinks describe $SINK --project=$PROJECT_ID \
  --format="value(destination)"
echo ""
echo "=========================================="
echo "Done. Cloud Run logs from opensre-mini will now flow:"
echo "  Cloud Logging → $TOPIC → $SUBSCRIPTION → $SERVICE_URL/ingest/gcp"
echo ""
echo "To test immediately:"
echo "  curl -X POST '$SERVICE_URL/ingest/test' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"service\":\"opensre-mini\",\"severity\":\"ERROR\",\"message\":\"database timeout\",\"count\":25}'"
echo ""
echo "Then watch for auto-detected incidents:"
echo "  curl '$SERVICE_URL/incidents'"
echo "=========================================="
