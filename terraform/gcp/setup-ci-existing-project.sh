#!/usr/bin/env bash
# One-time setup of the GitHub deploy (.github/workflows/deploy-cloudrun.yml) for a project where the Cloud Run
# service already exists (it was created with `gcloud run deploy`, so `terraform apply` would clash with it).
# For a project built from scratch with terraform/gcp this is not needed: Terraform creates the same things.
#
# Run it once, logged in with gcloud as the project owner:
#   bash terraform/gcp/setup-ci-existing-project.sh <project-id>
# Safe to run again: steps that already exist are skipped.
set -euo pipefail

PROJECT="${1:?usage: setup-ci-existing-project.sh <project-id>}"
GITHUB_REPO="${GITHUB_REPO:-NikhilMank/pricing-assistant}"
REGION="${REGION:-europe-west3}"
SERVICE="${SERVICE:-pricing-assistant}"
DEPLOY_SA_NAME="pricing-assistant-deploy"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
g() { gcloud --project "$PROJECT" "$@"; }

echo "1. switch on the APIs"
g services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  artifactregistry.googleapis.com run.googleapis.com

echo "2. image registry"
g artifacts repositories describe pricing-assistant --location "$REGION" >/dev/null 2>&1 \
  || g artifacts repositories create pricing-assistant --repository-format=docker --location "$REGION"

echo "3. identity pool that accepts only $GITHUB_REPO on the main branch"
g iam workload-identity-pools describe github --location=global >/dev/null 2>&1 \
  || g iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
g iam workload-identity-pools providers describe github --location=global --workload-identity-pool=github >/dev/null 2>&1 \
  || g iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool=github \
       --display-name="GitHub repository" --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
       --attribute-condition="assertion.repository == '${GITHUB_REPO}' && assertion.ref == 'refs/heads/main'"

echo "4. deploy service account (may push images and update this one service, nothing else)"
g iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 \
  || g iam service-accounts create "$DEPLOY_SA_NAME" --display-name="Pricing assistant (GitHub deploy)"
PROJECT_NUMBER="$(g projects describe "$PROJECT" --format='value(projectNumber)')"
g iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPO}" >/dev/null
g artifacts repositories add-iam-policy-binding pricing-assistant --location "$REGION" \
  --role=roles/artifactregistry.writer --member="serviceAccount:${DEPLOY_SA}" >/dev/null
g run services add-iam-policy-binding "$SERVICE" --region "$REGION" \
  --role=roles/run.developer --member="serviceAccount:${DEPLOY_SA}" >/dev/null
# Starting a revision that runs as the service's own account needs the right to "act as" that account
RUN_SA="$(g run services describe "$SERVICE" --region "$REGION" --format='value(spec.template.spec.serviceAccountName)')"
RUN_SA="${RUN_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"
g iam service-accounts add-iam-policy-binding "$RUN_SA" --role=roles/iam.serviceAccountUser \
  --member="serviceAccount:${DEPLOY_SA}" >/dev/null

PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github"
cat <<DONE

Done. Last step: tell GitHub about it (repository variables, not secrets: they are only names).
  gh variable set GCP_PROJECT_ID   --repo ${GITHUB_REPO} --body "${PROJECT}"
  gh variable set GCP_WIF_PROVIDER --repo ${GITHUB_REPO} --body "${PROVIDER}"
  gh variable set GCP_DEPLOY_SA    --repo ${GITHUB_REPO} --body "${DEPLOY_SA}"
DONE
