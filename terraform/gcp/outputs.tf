output "app_url" {
  description = "The public address of the app on Cloud Run"
  value       = google_cloud_run_v2_service.app.uri
}
