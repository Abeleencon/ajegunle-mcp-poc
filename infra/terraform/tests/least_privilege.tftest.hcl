# Offline guardrail tests: `terraform test` plans against mocked providers, so no AWS
# account or credentials are needed. They pin the least-privilege decisions in place.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{}" }
  }
  mock_resource "aws_eks_cluster" {
    override_during = plan
    defaults = {
      arn      = "arn:aws:eks:us-east-1:111122223333:cluster/mcp-agent"
      identity = [{ oidc = [{ issuer = "https://oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE" }] }]
    }
  }
}

mock_provider "tls" {
  mock_data "tls_certificate" {
    defaults = { certificates = [{ sha1_fingerprint = "9e99a48a9960b14926bb7f3b02e22da2b0ab7280" }] }
  }
}

variables {
  github_repository           = "example/mcp-agent-portfolio"
  cluster_public_access_cidrs = ["203.0.113.10/32"]
}

run "cluster_access_is_explicit" {
  command = plan

  assert {
    condition     = aws_eks_cluster.main.access_config[0].authentication_mode == "API"
    error_message = "Cluster must use access entries only."
  }
  assert {
    condition     = aws_eks_cluster.main.access_config[0].bootstrap_cluster_creator_admin_permissions == false
    error_message = "Whoever runs apply must not become cluster admin implicitly."
  }
  assert {
    condition     = !contains(aws_eks_cluster.main.vpc_config[0].public_access_cidrs, "0.0.0.0/0")
    error_message = "Public API endpoint must not be open to the internet."
  }
  assert {
    condition     = one(aws_eks_cluster.main.encryption_config[0].resources) == "secrets"
    error_message = "Kubernetes Secrets must be KMS-encrypted."
  }
}

run "pods_cannot_use_the_node_role" {
  command = plan

  assert {
    condition     = aws_launch_template.node.metadata_options[0].http_tokens == "required"
    error_message = "IMDSv2 must be required."
  }
  assert {
    condition     = aws_launch_template.node.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Hop limit 1 keeps pods from reaching node credentials."
  }
  assert {
    condition     = !contains(keys(aws_iam_role_policy_attachment.node), "AmazonEKS_CNI_Policy")
    error_message = "The CNI policy belongs on the aws-node IRSA role, not the node role."
  }
}

run "irsa_roles_trust_one_service_account_each" {
  command = plan

  assert {
    condition     = toset(one([for c in data.aws_iam_policy_document.irsa_assume["agent"].statement[0].condition : c.values if endswith(c.variable, ":sub")])) == toset(["system:serviceaccount:agent:mcp-agent"])
    error_message = "Agent role must trust only agent/mcp-agent."
  }
  assert {
    condition     = toset(one([for c in data.aws_iam_policy_document.irsa_assume["collector"].statement[0].condition : c.values if endswith(c.variable, ":sub")])) == toset(["system:serviceaccount:agent:otel-collector"])
    error_message = "Collector role must trust only agent/otel-collector."
  }
}

run "agent_permissions_are_scoped" {
  command = plan

  assert {
    condition     = toset(one([for c in data.aws_iam_policy_document.agent.statement[0].condition : c.values if endswith(c.variable, "s3:prefix")])) == toset(["runbooks/", "runbooks/*"])
    error_message = "ListBucket must be limited to the runbook prefix."
  }
  assert {
    condition     = length(data.aws_iam_policy_document.agent.statement) == 4
    error_message = "Agent policy grew; review new permissions deliberately and update this test."
  }
  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.agent.statement :
      !anytrue([for a in s.actions : endswith(a, "*")])
    ])
    error_message = "Agent policy must not use wildcard actions."
  }
}

run "deploy_role_is_namespace_scoped" {
  command = plan

  assert {
    condition     = aws_eks_access_policy_association.github_deploy.access_scope[0].type == "namespace"
    error_message = "Deploy role must be limited to the agent namespace."
  }
  assert {
    condition     = toset(one([for c in data.aws_iam_policy_document.github_assume.statement[0].condition : c.values if endswith(c.variable, ":sub")])) == toset(["repo:example/mcp-agent-portfolio:environment:production"])
    error_message = "Deploy role must trust only the protected production environment."
  }
}

run "rejects_open_endpoint" {
  command = plan

  variables {
    cluster_public_access_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.cluster_public_access_cidrs]
}
