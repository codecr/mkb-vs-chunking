variable "aws_region" {
  description = "Debe coincidir con AWS_REGION de config.sh."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "mkb-vs-chunking"
}

# Nombre FIJO (no random-suffixed): 02-create-managed-kb.py y
# 03-ingest-and-wait.py apuntan la Managed KB (config B/C/D) al mismo bucket,
# para que el corpus sea compartido entre la config A (esta terraform) y las
# configs B/C/D (creadas por boto3). Debe coincidir con CORPUS_BUCKET de
# config.sh.
variable "corpus_bucket_name" {
  type        = string
  description = "Debe ser igual a CORPUS_BUCKET en config.sh."
}

# Parametros de la config A, confirmados contra el repo de abril
# (D:\POC\kb\bedrock-chunking-benchmark/terraform/variables.tf). Constantes
# para aislar la capa de retrieval como variable del benchmark.
variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimensions" {
  type    = number
  default = 1024
}

variable "fixed_size_max_tokens" {
  type    = number
  default = 512
}

variable "fixed_size_overlap_percentage" {
  type    = number
  default = 20
}

# El rol de eval necesita permiso de InvokeModel sobre el juez. Debe
# coincidir con JUDGE_MODEL de config.sh (amazon.nova-pro-v1:0).
variable "judge_model_id" {
  type    = string
  default = "amazon.nova-pro-v1:0"
}

# Debe ser igual a EVAL_BUCKET en config.sh. Contiene los datasets
# BYO-inference (input) y los resultados de los eval jobs (output).
variable "eval_bucket_name" {
  type        = string
  description = "Debe ser igual a EVAL_BUCKET en config.sh."
}
