# =============================================================================
# Knowledge Base module (S3 Vectors storage)
#
# Simpler than the OpenSearch version: no null_resource, no index bootstrap
# script. Bedrock KB points directly at an S3 Vectors index ARN.
# =============================================================================

variable "name" {
  type        = string
  description = "Unique name for this KB."
}

variable "kb_role_arn" {
  type = string
}

variable "index_arn" {
  type        = string
  description = "ARN of the S3 Vectors index this KB will write/query."
}

variable "source_bucket_arn" {
  type = string
}

# side-test/ es nuevo en este repo (no existia en abril): prueba Smart
# Parsing pero no debe contaminar el indice de la config A (FIXED_SIZE no
# hace OCR; el PDF escaneado solo tiene sentido para la Managed KB). Vacio
# (default) = ingesta el bucket completo, igual que en abril.
variable "source_inclusion_prefixes" {
  type    = list(string)
  default = []
}

variable "intermediate_bucket_arn" {
  type        = string
  description = "Required when chunking_strategy is CUSTOM."
  default     = ""
}

variable "embedding_model_arn" {
  type = string
}

variable "embedding_dimensions" {
  type    = number
  default = 1024
}

variable "chunking_strategy" {
  type = string
  validation {
    condition     = contains(["NONE", "FIXED_SIZE", "HIERARCHICAL", "SEMANTIC", "CUSTOM"], var.chunking_strategy)
    error_message = "chunking_strategy must be one of NONE, FIXED_SIZE, HIERARCHICAL, SEMANTIC, CUSTOM."
  }
}

# Strategy-specific parameters
variable "fixed_size_max_tokens" {
  type    = number
  default = 512
}
variable "fixed_size_overlap_percentage" {
  type    = number
  default = 20
}
variable "hierarchical_parent_max_tokens" {
  type    = number
  default = 1500
}
variable "hierarchical_child_max_tokens" {
  type    = number
  default = 300
}
variable "hierarchical_overlap_tokens" {
  type    = number
  default = 60
}
variable "semantic_max_tokens" {
  type    = number
  default = 300
}
variable "semantic_buffer_size" {
  type    = number
  default = 0
}
variable "semantic_breakpoint_percentile_threshold" {
  type    = number
  default = 95
}
variable "custom_lambda_arn" {
  type    = string
  default = ""
}

locals {
  is_custom                   = var.chunking_strategy == "CUSTOM"
  effective_chunking_strategy = local.is_custom ? "FIXED_SIZE" : var.chunking_strategy
}

# -----------------------------------------------------------------------------
# Knowledge Base (storage_configuration uses S3_VECTORS)
# -----------------------------------------------------------------------------

resource "aws_bedrockagent_knowledge_base" "this" {
  name     = var.name
  role_arn = var.kb_role_arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = var.embedding_model_arn
      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = var.embedding_dimensions
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      index_arn = var.index_arn
    }
  }
}

# -----------------------------------------------------------------------------
# Data source (unchanged - chunking config is independent of storage backend)
# -----------------------------------------------------------------------------

resource "aws_bedrockagent_data_source" "this" {
  name              = "${var.name}-ds"
  knowledge_base_id = aws_bedrockagent_knowledge_base.this.id

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = var.source_bucket_arn
      inclusion_prefixes = length(var.source_inclusion_prefixes) > 0 ? var.source_inclusion_prefixes : null
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = local.effective_chunking_strategy

      dynamic "fixed_size_chunking_configuration" {
        for_each = var.chunking_strategy == "FIXED_SIZE" || local.is_custom ? [1] : []
        content {
          max_tokens         = var.fixed_size_max_tokens
          overlap_percentage = var.fixed_size_overlap_percentage
        }
      }

      dynamic "hierarchical_chunking_configuration" {
        for_each = var.chunking_strategy == "HIERARCHICAL" ? [1] : []
        content {
          overlap_tokens = var.hierarchical_overlap_tokens
          level_configuration {
            max_tokens = var.hierarchical_parent_max_tokens
          }
          level_configuration {
            max_tokens = var.hierarchical_child_max_tokens
          }
        }
      }

      dynamic "semantic_chunking_configuration" {
        for_each = var.chunking_strategy == "SEMANTIC" ? [1] : []
        content {
          max_token                       = var.semantic_max_tokens
          buffer_size                     = var.semantic_buffer_size
          breakpoint_percentile_threshold = var.semantic_breakpoint_percentile_threshold
        }
      }
    }

    dynamic "custom_transformation_configuration" {
      for_each = local.is_custom ? [1] : []
      content {
        intermediate_storage {
          s3_location {
            uri = "s3://${replace(var.intermediate_bucket_arn, "arn:aws:s3:::", "")}/intermediate/${var.name}"
          }
        }
        transformation {
          step_to_apply = "POST_CHUNKING"
          transformation_function {
            transformation_lambda_configuration {
              lambda_arn = var.custom_lambda_arn
            }
          }
        }
      }
    }
  }
}

output "knowledge_base_id" {
  value = aws_bedrockagent_knowledge_base.this.id
}

output "data_source_id" {
  value = aws_bedrockagent_data_source.this.data_source_id
}

output "knowledge_base_arn" {
  value = aws_bedrockagent_knowledge_base.this.arn
}
