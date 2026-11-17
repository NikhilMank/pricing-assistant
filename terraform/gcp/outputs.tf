output "app_url" {
  description = "The public address of the app on Cloud Run"
  value       = google_cloud_run_v2_service.app.uri
}

# The three values the GitHub workflow needs (set them as repository variables, see the README)
output "github_wif_provider" {
  description = "Value for the GitHub variable GCP_WIF_PROVIDER"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_deploy_service_account" {
  description = "Value for the GitHub variable GCP_DEPLOY_SA"
  value       = google_service_account.deploy.email
}
