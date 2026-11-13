# Google Cloud setup for the pricing assistant: one Cloud Run container (API + built frontend)
# that calls Gemini on Vertex AI with its own service account (no keys).
#
# The project and its billing account are created by hand once; this file builds everything inside it.
#
# Use (from this folder):
#   terraform init
#   terraform apply -var project_id=<your-project-id>
# `apply` builds the image with Cloud Build (needs the gcloud CLI, logged in) and starts the service.
# After code changes, run apply again with a new tag: -var image_tag=v2

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

  depends_on = [terraform_data.build_image, google_project_iam_member.vertex]
}

# --allow-unauthenticated: anyone on the internet may open the app
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
