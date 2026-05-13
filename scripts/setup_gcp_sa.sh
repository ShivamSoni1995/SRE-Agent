#!/bin/bash
# Run this ONCE to create a service account for GitHub Actions.
# After running, copy the printed JSON into your GitHub secret GCP_SA_KEY.

set -e

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SA_NAME="github-actions-deployer"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Project: $PROJECT_ID"
echo "Creating service account: $SA_EMAIL"

# Create the service account
gcloud iam service-accounts create $SA_NAME \
  --display-name="GitHub Actions Deployer" \
  --project=$PROJECT_ID 2>/dev/null || echo "Service account already exists"

# Grant required roles
ROLES=(
  "roles/run.admin"
  "roles/storage.admin"
  "roles/artifactregistry.admin"
  "roles/secretmanager.secretAccessor"
  "roles/logging.logWriter"
  "roles/iam.serviceAccountUser"
)

for ROLE in "${ROLES[@]}"; do
  echo "Granting $ROLE..."
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --quiet
done

# Generate and print the key
KEY_FILE="github-actions-sa-key.json"
gcloud iam service-accounts keys create $KEY_FILE \
  --iam-account=$SA_EMAIL

echo ""
echo "================================================================"
echo "SUCCESS. Add these secrets to GitHub:"
echo "  Settings → Secrets and variables → Actions → New repository secret"
echo ""
echo "Secret name : GCP_SA_KEY"
echo "Secret value: (contents of ./${KEY_FILE})"
echo ""
echo "Secret name : GCP_PROJECT_ID"
echo "Secret value: ${PROJECT_ID}"
echo ""
echo "Secret name : SLACK_WEBHOOK_URL"
echo "Secret value: your Slack webhook URL"
echo ""
echo "Secret name : API_SERVICE_URL"
echo "Secret value: https://opensre-mini-${PROJECT_NUMBER}.${REGION:-us-central1}.run.app"
echo "================================================================"
echo ""
echo "Key saved to: ./${KEY_FILE}"
echo "WARNING: Do NOT commit this file. It is in .gitignore."
