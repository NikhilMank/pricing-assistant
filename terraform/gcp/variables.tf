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
  description = "Gemini model used by the agents"
  type        = string
  default     = "gemini-4-hydrogen"
}

variable "image_tag" {
  description = "Image tag. Change it (v2, v3, ...) to rebuild and deploy new code."
  type        = string
  default     = "v1"
}
