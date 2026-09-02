# =============================================================================
# Infraestructura para la config A (S3 Vectors + FIXED_SIZE) del benchmark
# mkb-vs-chunking. Recreacion de abril: la KB y el bucket originales fueron
# destruidos por completo (confirmado 2026-09-01, ver README.md). Solo se
# recrea lo que hace falta para la config A -- las otras 4 estrategias de
# abril (NONE, HIERARCHICAL, SEMANTIC, CUSTOM) no se necesitan aqui.
#
# Modulo de KB copiado sin modificar desde
# D:\POC\kb\bedrock-chunking-benchmark\terraform\modules\knowledge-base
# (repo de abril, clonado localmente).
# =============================================================================

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  name_prefix = "${var.project_name}-${random_id.suffix.hex}"
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.region

  embedding_model_arn = "arn:${data.aws_partition.current.partition}:bedrock:${local.region}::foundation-model/${var.embedding_model_id}"
}

# -----------------------------------------------------------------------------
# Bucket del corpus. Nombre FIJO: compartido con las configs B/C/D (Managed
# KB, creada por boto3 en 02-create-managed-kb.py) para que el corpus sea
# el mismo para todas las configuraciones.
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "corpus" {
  bucket        = var.corpus_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "corpus" {
  bucket = aws_s3_bucket.corpus.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# -----------------------------------------------------------------------------
# Bucket de evaluacion. Nombre FIJO: 06-build-eval-datasets.py escribe los
# datasets BYO-inference aqui; 07-launch-eval-jobs.py y el servicio de
# Bedrock Evaluations leen input/ y escriben output/ aqui.
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "eval" {
  bucket        = var.eval_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "eval" {
  bucket = aws_s3_bucket.eval.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# -----------------------------------------------------------------------------
# S3 Vectors: un bucket, un indice (solo config A necesita FIXED_SIZE aqui).
# -----------------------------------------------------------------------------

resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = "${local.name_prefix}-vectors"
}

# GOTCHA CONFIRMADO en abril (ver terraform de abril, main.tf): S3 Vectors
# limita a 2048 bytes los metadatos "filtrables" por vector. Bedrock KB
# escribe el texto del chunk en AMAZON_BEDROCK_TEXT y metadata extra en
# AMAZON_BEDROCK_METADATA, ambos por encima de 2048 bytes para cualquier
# chunk real. Declararlos no-filtrables es obligatorio, no un ajuste fino:
# omitirlo causa fallo del 100% de la ingesta.
resource "aws_s3vectors_index" "fixed" {
  index_name         = "kb-fixed-index"
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  data_type          = "float32"
  dimension          = var.embedding_dimensions
  distance_metric    = "cosine"

  metadata_configuration {
    non_filterable_metadata_keys = [
      "AMAZON_BEDROCK_TEXT",
      "AMAZON_BEDROCK_METADATA",
    ]
  }
}

# -----------------------------------------------------------------------------
# IAM role para la Knowledge Base.
# -----------------------------------------------------------------------------

resource "aws_iam_role" "kb" {
  name = "${local.name_prefix}-kb-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:bedrock:${local.region}:${local.account_id}:knowledge-base/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "kb_bedrock" {
  name = "bedrock-access"
  role = aws_iam_role.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:Rerank"]
      Resource = [local.embedding_model_arn]
    }]
  })
}

resource "aws_iam_role_policy" "kb_s3" {
  name = "s3-access"
  role = aws_iam_role.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.corpus.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.corpus.arn}/*"]
      }
    ]
  })
}

resource "aws_iam_role_policy" "kb_s3vectors" {
  name = "s3vectors-access"
  role = aws_iam_role.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3vectors:PutVectors",
        "s3vectors:GetVectors",
        "s3vectors:DeleteVectors",
        "s3vectors:QueryVectors",
        "s3vectors:ListVectors",
        "s3vectors:GetIndex",
        "s3vectors:ListIndexes",
        "s3vectors:GetVectorBucket",
      ]
      Resource = [
        aws_s3vectors_vector_bucket.kb.vector_bucket_arn,
        "${aws_s3vectors_vector_bucket.kb.vector_bucket_arn}/index/*",
      ]
    }]
  })
}

# -----------------------------------------------------------------------------
# Config A: KB FIXED_SIZE + S3 Vectors.
# Parametros identicos al benchmark de abril, solo cambia el prefijo del
# corpus (corpus/, no todo el bucket) para no mezclar con side-test/.
# -----------------------------------------------------------------------------

module "kb_fixed" {
  source = "./modules/knowledge-base"

  name                          = "${local.name_prefix}-fixed"
  kb_role_arn                   = aws_iam_role.kb.arn
  index_arn                     = aws_s3vectors_index.fixed.index_arn
  embedding_dimensions          = var.embedding_dimensions
  source_bucket_arn             = aws_s3_bucket.corpus.arn
  source_inclusion_prefixes     = ["corpus/"] # side-test/ NO entra a config A
  embedding_model_arn           = local.embedding_model_arn
  chunking_strategy             = "FIXED_SIZE"
  fixed_size_max_tokens         = var.fixed_size_max_tokens
  fixed_size_overlap_percentage = var.fixed_size_overlap_percentage

  # GOTCHA CONFIRMADO corriendo 99-teardown.sh contra la cuenta real: el data
  # source referencia el rol via kb_role_arn (un output de aws_iam_role.kb),
  # pero NO referencia las inline policies del rol -- son recursos hermanos,
  # sin dependencia entre si. En destroy, terraform borro
  # aws_iam_role_policy.kb_s3vectors en paralelo con el data source; el
  # borrado real de los vectores (que corre en el backend de Bedrock, no es
  # instantaneo) todavia necesitaba s3vectors:DeleteVectors y fallo con
  # DELETE_UNSUCCESSFUL: "Unable to delete data from vector store". depends_on
  # explicito fuerza el orden inverso en destroy: el modulo (KB + data
  # source) se borra ANTES que las policies, no en paralelo.
  depends_on = [
    aws_iam_role_policy.kb_bedrock,
    aws_iam_role_policy.kb_s3,
    aws_iam_role_policy.kb_s3vectors,
  ]
}

# -----------------------------------------------------------------------------
# IAM role para los eval jobs (Bedrock RAG Evaluation, path BYO-inference).
#
# GOTCHA PENDIENTE: la doc oficial (rag-eval-service-roles.html) no
# distingue permisos entre modo nativo y BYO-inference. No confirma que se
# pueda omitir bedrock:Retrieve/RetrieveAndGenerate cuando el dataset ya
# trae los contextos. Se otorga de forma defensiva (wildcard sobre
# knowledge-base/* de esta cuenta/region, evita depender del ID de la
# Managed KB que todavia no existe en este paso) hasta confirmar contra la
# ejecucion real en 07-launch-eval-jobs.py.
# -----------------------------------------------------------------------------

resource "aws_iam_role" "eval" {
  name = "${local.name_prefix}-eval-role"

  # Confirmado contra rag-eval-service-roles.html: aws:SourceArn debe estar
  # presente y apuntar a evaluation-job/*; su ausencia hace que el servicio
  # rechace el rol aunque la policy de permisos sea correcta.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnEquals = {
          "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:bedrock:${local.region}:${local.account_id}:evaluation-job/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "eval" {
  name = "eval-access"
  role = aws_iam_role.eval.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeJudgeModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:GetInferenceProfile",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${var.judge_model_id}",
          "arn:${data.aws_partition.current.partition}:bedrock:${local.region}:${local.account_id}:inference-profile/*",
        ]
      },
      {
        Sid    = "KBAccessDefensive"
        Effect = "Allow"
        Action = [
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:bedrock:${local.region}:${local.account_id}:knowledge-base/*",
        ]
      },
      {
        Sid    = "EvalBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:PutObject",
          "s3:GetBucketLocation",
          "s3:AbortMultipartUpload",
          "s3:ListBucketMultipartUploads",
        ]
        Resource = [
          aws_s3_bucket.eval.arn,
          "${aws_s3_bucket.eval.arn}/*",
        ]
      }
    ]
  })
}
