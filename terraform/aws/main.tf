# AWS setup for the pricing assistant, the same as built by hand in the console:
#   frontend: private S3 bucket -> CloudFront (OAC) -> https://dxxxx.cloudfront.net
#   backend:  Lambda (zip from S3) + public Function URL, calls Bedrock through its IAM role
#
# Use (from this folder):
#   1. cd ../../backend && ../.venv/bin/python build_lambda.py     # creates build/lambda.zip
#   2. terraform init && terraform apply
#   3. build the frontend with the api_url output and upload it (see the `next_steps` output)

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.region
}

# Bucket names must be unique worldwide, so we add a random suffix
resource "random_id" "suffix" {
  byte_length = 3
}

locals {
  deploy_bucket = "pricing-assistant-deploy-${random_id.suffix.hex}"
  web_bucket    = "pricing-assistant-web-${random_id.suffix.hex}"
}

# ---------------------------------------------------------------- backend

# The zip is 56 MB; Lambda accepts direct uploads only up to 50 MB, so it goes through S3
resource "aws_s3_bucket" "deploy" {
  bucket        = local.deploy_bucket
  force_destroy = true # lets `terraform destroy` delete the bucket even when it holds files
}

resource "aws_s3_object" "lambda_zip" {
  bucket = aws_s3_bucket.deploy.id
  key    = "lambda.zip"
  source = "${path.module}/../../build/lambda.zip"
  etag   = filemd5("${path.module}/../../build/lambda.zip")

  # The GitHub workflow uploads newer zips later; Terraform must not undo that
  lifecycle {
    ignore_changes = [etag, source]
  }
}

# The role the Lambda runs as: write logs + call Bedrock (no keys anywhere)
resource "aws_iam_role" "lambda" {
  name = "pricing-assistant-api-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "bedrock" {
  name = "bedrock-invoke"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "*"
    }]
  })
}

resource "aws_lambda_function" "api" {
  function_name = var.function_name
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "lambda_handler.handler"
  memory_size   = 1024
  timeout       = 90

  s3_bucket = aws_s3_bucket.deploy.id
  s3_key    = aws_s3_object.lambda_zip.key

  environment {
    variables = {
      MAX_ANALYSES = "300"
      # CloudFront address, so the browser may call the API (no trailing slash)
      CORS_ORIGINS = "https://${aws_cloudfront_distribution.web.domain_name}"
    }
  }

  # The GitHub workflow pushes new code with `aws lambda update-function-code`
  lifecycle {
    ignore_changes = [source_code_hash, s3_object_version]
  }

  depends_on = [aws_iam_role_policy_attachment.logs]
}

# Public address of the API. Auth NONE: anyone may call it; CORS is handled by the app itself
resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
}

# Since 2025 a public Function URL needs both permissions (the console adds them automatically)
resource "aws_lambda_permission" "url_public" {
  statement_id           = "FunctionURLAllowPublicAccess"
  function_name          = aws_lambda_function.api.function_name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke" {
  statement_id             = "FunctionURLInvokeAllowPublicAccess"
  function_name            = aws_lambda_function.api.function_name
  action                   = "lambda:InvokeFunction"
  principal                = "*"
  invoked_via_function_url = true
}

# ---------------------------------------------------------------- frontend

resource "aws_s3_bucket" "web" {
  bucket        = local.web_bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# OAC = only CloudFront may read the private bucket
resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "pricing-assistant-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # Europe + North America edges only, the cheapest

  origin {
    origin_id                = "web-bucket"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "web-bucket"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # AWS managed policy "CachingOptimized"
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# This is the "copy policy" step from the console: let this distribution read the bucket
resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web.arn } }
    }]
  })
}

# ---------------------------------------------------------------- cost guard (optional)

resource "aws_budgets_budget" "monthly" {
  count        = var.alert_email == "" ? 0 : 1
  name         = "pricing-assistant-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
