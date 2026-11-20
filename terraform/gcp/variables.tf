variable "project_id" {
  description = "Google Cloud project ID (billing must already be linked)"
  type        = string
}

variable "region" {
  description = "Region for Cloud Run and the image registry"
  type        = string
  default     = "europe-west3"
}

variable "vertex_model" {
  description = "Gemini model the analyst and the strategist try first (the reviewer has its own model, set in the code)"
  type        = string
  default     = "gemini-4-carbon"
}

variable "image_tag" {
  description = "Image tag. Change it (v2, v3, ...) to rebuild and deploy new code."
  type        = string
  default     = "v1"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) whose workflow may deploy to Cloud Run"
  type        = string
  default     = "NikhilMank/pricing-assistant"
}
