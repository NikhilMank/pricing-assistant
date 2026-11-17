# Google Cloud setup for the pricing assistant: one Cloud Run container (API + built frontend)
# that calls Gemini on Vertex AI with its own service account (no keys).
#
# The project and its billing account are created by hand once; this file builds everything inside it.
#
# Use (from this folder):
#   terraform init
#   terraform apply -var project_id=<your-project-id>
# `apply` builds the image with Cloud Build (needs the gcloud CLI, logged in) and starts the service.
# After the first apply, code changes are deployed by the GitHub workflow (.github/workflows/deploy-cloudrun.yml),
# which signs in through the identity pool at the bottom of this file. Cloud Run keeps the image CI deployed, so a
# later `apply` does not roll it back (see ignore_changes on the service).

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/pricing-assistant/app:${var.image_tag}"
}

# APIs must be switched on before their resources can be created
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com", # Vertex AI (Gemini)
    "iam.googleapis.com",        # identity pool for GitHub Actions
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "pricing-assistant"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# Builds the Dockerfile in the repo root on Google's servers (no local Docker needed)
resource "terraform_data" "build_image" {
  triggers_replace = [var.image_tag]

  provisioner "local-exec" {
    command = "gcloud builds submit ${path.module}/../.. --tag ${local.image} --project ${var.project_id}"
  }

  depends_on = [google_artifact_registry_repository.images]
}

# The identity the container runs as; it may call Vertex AI and nothing else
resource "google_service_account" "run" {
  account_id   = "pricing-assistant-run"
  display_name = "Pricing assistant (Cloud Run)"
}

resource "google_project_iam_member" "vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.run.email}"
}

resource "google_cloud_run_v2_service" "app" {
  name                = "pricing-assistant"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false # lets `terraform destroy` remove it

  template {
    service_account = google_service_account.run.email
    timeout         = "300s"

    scaling {
      min_instance_count = 0 # scales to zero when nobody uses it (no cost)
      max_instance_count = 2 # cost guard
    }

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "LLM_PROVIDER"
        value = "vertex"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "VERTEX_MODEL"
        value = var.vertex_model
      }
      env {
        name  = "VERTEX_LOCATION"
        value = "global"
      }
      env {
        name  = "MAX_ANALYSES"
        value = "300"
      }
    }
  }

  # The GitHub workflow deploys new images (and Cloud Run records who did it). Terraform must not undo that.
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [terraform_data.build_image, google_project_iam_member.vertex]
}

# --allow-unauthenticated: anyone on the internet may open the app
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---- Deploys from GitHub Actions, without any key ----
# GitHub proves who it is with a short-lived token; Google exchanges it for the deploy identity below.
# Only workflows of this repository, on the main branch, are accepted.
# (A deleted pool or provider keeps its name for 30 days, so destroy + apply right away needs new ids.)
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub repository"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == \"${var.github_repo}\" && assertion.ref == \"refs/heads/main\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# The identity the workflow acts as. It may push images and update the one service, nothing else.
resource "google_service_account" "deploy" {
  account_id   = "pricing-assistant-deploy"
  display_name = "Pricing assistant (GitHub deploy)"
}

resource "google_service_account_iam_member" "github_may_use_deploy" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_artifact_registry_repository_iam_member" "deploy_push" {
  repository = google_artifact_registry_repository.images.name
  location   = google_artifact_registry_repository.images.location
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_cloud_run_v2_service_iam_member" "deploy_update" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deploy.email}"
}

# Cloud Run only lets an identity start a revision that runs as the container's service account if it may "act as" it
resource "google_service_account_iam_member" "deploy_acts_as_run" {
  service_account_id = google_service_account.run.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}
