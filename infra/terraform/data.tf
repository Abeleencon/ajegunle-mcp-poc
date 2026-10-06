# Application data: the runbook bucket, the Anthropic API key secret, and the
# container registry.

resource "aws_kms_key" "data" {
  description         = "${var.name} application data (runbooks bucket, API key secret)"
  enable_key_rotation = true
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

# --- Runbook bucket ----------------------------------------------------------------

resource "aws_s3_bucket" "knowledge" {
  bucket_prefix = "${var.name}-knowledge-"
}

resource "aws_s3_bucket_public_access_block" "knowledge" {
  bucket                  = aws_s3_bucket.knowledge.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "knowledge_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.knowledge.arn,
      "${aws_s3_bucket.knowledge.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id
  policy = data.aws_iam_policy_document.knowledge_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.knowledge]
}

# Seed the bucket with the runbooks bundled in the repo.
resource "aws_s3_object" "runbooks" {
  for_each = fileset("${path.module}/../../src/agent_service/knowledge", "*.md")

  bucket       = aws_s3_bucket.knowledge.id
  key          = "${var.knowledge_prefix}${each.value}"
  source       = "${path.module}/../../src/agent_service/knowledge/${each.value}"
  source_hash  = filemd5("${path.module}/../../src/agent_service/knowledge/${each.value}")
  content_type = "text/markdown"

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.knowledge]
}

# --- Anthropic API key -------------------------------------------------------------
# Terraform creates the empty secret only; the value is set out of band so it never
# lands in state:  aws secretsmanager put-secret-value --secret-id <arn> --secret-string ...

resource "aws_secretsmanager_secret" "anthropic" {
  name                    = "${var.name}/anthropic-api-key"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = 7
}

# --- Container registry ------------------------------------------------------------

resource "aws_ecr_repository" "agent" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 30 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}
