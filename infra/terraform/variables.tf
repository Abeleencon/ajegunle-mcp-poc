variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for resources; also the EKS cluster name."
  type        = string
  default     = "mcp-agent"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must be 3-31 lowercase letters, digits or hyphens."
  }
}

variable "kubernetes_version" {
  description = "EKS control plane version."
  type        = string
  default     = "1.33"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3 (EKS needs at least two)."
  }
}

variable "cluster_public_access_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach the public EKS API endpoint. Required, with no default, so the
    endpoint is never left open to 0.0.0.0/0 by accident. Use your office or VPN range;
    see the README for GitHub-hosted runner options.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.cluster_public_access_cidrs) > 0 && !contains(var.cluster_public_access_cidrs, "0.0.0.0/0")
    error_message = "Provide at least one CIDR, and not 0.0.0.0/0."
  }
}

variable "cluster_admin_principal_arns" {
  description = "IAM role/user ARNs granted cluster-admin through EKS access entries."
  type        = list(string)
  default     = []
}

variable "node_instance_types" {
  type    = list(string)
  default = ["m6i.large"]
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "k8s_namespace" {
  description = "Namespace the agent runs in. IRSA trust and deploy access are scoped to it."
  type        = string
  default     = "agent"
}

variable "agent_service_account" {
  type    = string
  default = "mcp-agent"
}

variable "collector_service_account" {
  type    = string
  default = "otel-collector"
}

variable "knowledge_prefix" {
  description = "S3 prefix holding the runbooks. The agent can read nothing outside it."
  type        = string
  default     = "runbooks/"
}

variable "github_repository" {
  description = "GitHub repository allowed to deploy, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "github_environment" {
  description = "GitHub Actions environment whose jobs may assume the deploy role."
  type        = string
  default     = "production"
}

variable "create_github_oidc_provider" {
  description = "Set false if the account already has the token.actions.githubusercontent.com provider."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  type    = number
  default = 30
}
