output "aws_region" {
  value = var.aws_region
}

output "corpus_bucket" {
  value = aws_s3_bucket.corpus.bucket
}

output "eval_bucket" {
  value = aws_s3_bucket.eval.bucket
}

output "vector_bucket_name" {
  value = aws_s3vectors_vector_bucket.kb.vector_bucket_name
}

output "kb_role_arn" {
  value = aws_iam_role.kb.arn
}

output "eval_role_arn" {
  value = aws_iam_role.eval.arn
}

output "kb_fixed_id" {
  value = module.kb_fixed.knowledge_base_id
}

output "kb_fixed_arn" {
  value = module.kb_fixed.knowledge_base_arn
}

output "kb_fixed_data_source_id" {
  value = module.kb_fixed.data_source_id
}
