output "region" {
  value = var.region
}

output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.agent.repository_url
}

output "agent_role_arn" {
  description = "Annotate the agent service account with this (eks.amazonaws.com/role-arn)."
  value       = aws_iam_role.agent.arn
}

output "collector_role_arn" {
  value = aws_iam_role.collector.arn
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN variable on the GitHub production environment."
  value       = aws_iam_role.github_deploy.arn
}

output "knowledge_bucket" {
  value = aws_s3_bucket.knowledge.id
}

output "knowledge_prefix" {
  value = var.knowledge_prefix
}

output "anthropic_secret_arn" {
  value = aws_secretsmanager_secret.anthropic.arn
}
