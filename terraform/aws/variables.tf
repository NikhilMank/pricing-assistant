variable "region" {
  description = "AWS region for everything (Bedrock Haiku 4.5 works here through the eu. inference profile)"
  type        = string
  default     = "eu-central-1"
}

variable "function_name" {
  description = "Name of the Lambda function"
  type        = string
  default     = "pricing-assistant-api"
}

variable "alert_email" {
  description = "Email for a monthly budget alert. Leave empty to skip the budget."
  type        = string
  default     = ""
}

variable "budget_usd" {
  description = "Monthly budget in USD (alert at 80%)"
  type        = number
  default     = 10
}
