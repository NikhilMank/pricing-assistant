output "api_url" {
  description = "Function URL without trailing slash (GitHub secret VITE_API_URL)"
  value       = trimsuffix(aws_lambda_function_url.api.function_url, "/")
}

output "app_url" {
  description = "The public address of the app"
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "github_secrets" {
  description = "Values for the GitHub Actions secrets (AWS_REGION, access keys stay as they are)"
  value = {
    AWS_REGION                 = var.region
    BACKEND_BUCKET_NAME        = aws_s3_bucket.deploy.id
    LAMBDA_FUNCTION_NAME       = aws_lambda_function.api.function_name
    FRONTEND_BUCKET_NAME       = aws_s3_bucket.web.id
    CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.web.id
    VITE_API_URL               = trimsuffix(aws_lambda_function_url.api.function_url, "/")
  }
}

output "next_steps" {
  description = "Upload the frontend (it needs the API address inside, so it is built after apply)"
  value       = <<-EOT
    cd frontend && npm ci && VITE_API_BASE=${trimsuffix(aws_lambda_function_url.api.function_url, "/")} npm run build && cd ..
    aws s3 sync frontend/dist s3://${aws_s3_bucket.web.id} --delete
    Then open https://${aws_cloudfront_distribution.web.domain_name} (the first CloudFront deploy takes a few minutes)
    Update the GitHub secrets from the github_secrets output (the names and IDs are new).
  EOT
}
