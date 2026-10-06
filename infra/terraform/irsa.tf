# IAM Roles for Service Accounts (IRSA).
#
# Each role trusts exactly one Kubernetes service account in one namespace, through the
# cluster's OIDC issuer, and holds only the actions that workload needs.

data "tls_certificate" "eks_oidc" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks_oidc.certificates[0].sha1_fingerprint]
}

locals {
  oidc_issuer = replace(aws_iam_openid_connect_provider.eks.url, "https://", "")

  irsa_subjects = {
    agent     = "system:serviceaccount:${var.k8s_namespace}:${var.agent_service_account}"
    collector = "system:serviceaccount:${var.k8s_namespace}:${var.collector_service_account}"
    vpc_cni   = "system:serviceaccount:kube-system:aws-node"
  }
}

data "aws_iam_policy_document" "irsa_assume" {
  for_each = local.irsa_subjects

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:sub"
      values   = [each.value]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

# --- Agent: read runbooks under one prefix, read one secret ------------------------

resource "aws_iam_role" "agent" {
  name                 = "${var.name}-agent"
  assume_role_policy   = data.aws_iam_policy_document.irsa_assume["agent"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "agent" {
  statement {
    sid       = "ListRunbookPrefixOnly"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.knowledge.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [var.knowledge_prefix, "${var.knowledge_prefix}*"]
    }
  }

  statement {
    sid       = "ReadRunbooks"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.knowledge.arn}/${var.knowledge_prefix}*"]
  }

  statement {
    sid       = "ReadAnthropicKey"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.anthropic.arn]
  }

  statement {
    sid       = "DecryptViaS3AndSecretsManagerOnly"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "s3.${var.region}.amazonaws.com",
        "secretsmanager.${var.region}.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role_policy" "agent" {
  name   = "agent-least-privilege"
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent.json
}

# --- OpenTelemetry collector: write traces to X-Ray --------------------------------

resource "aws_iam_role" "collector" {
  name               = "${var.name}-otel-collector"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume["collector"].json
}

data "aws_iam_policy_document" "collector" {
  statement {
    sid = "WriteTraces"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    # X-Ray trace APIs do not support resource-level permissions.
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "xray-write"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector.json
}

# --- VPC CNI: moved off the node role ----------------------------------------------

resource "aws_iam_role" "vpc_cni" {
  name               = "${var.name}-vpc-cni"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume["vpc_cni"].json
}

resource "aws_iam_role_policy_attachment" "vpc_cni" {
  role       = aws_iam_role.vpc_cni.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}
