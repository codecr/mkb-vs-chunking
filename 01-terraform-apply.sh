#!/usr/bin/env bash
# Recrea la infraestructura de la config A (S3 Vectors + FIXED_SIZE).
# La KB y el bucket de abril fueron destruidos por completo (confirmado
# 2026-09-01, ver README.md "Estado de la infraestructura"). Este terraform
# es una version recortada del repo de abril: solo la estrategia FIXED_SIZE,
# no las otras 4 (NONE/HIERARCHICAL/SEMANTIC/CUSTOM) que no hacen falta aqui.
#
# NO usa -auto-approve: crea recursos facturables (KB, S3 Vectors, buckets).
# Revisa el plan antes de escribir "yes".
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"

TF_DIR="$HERE/terraform"

command -v terraform >/dev/null 2>&1 || {
  echo "[FALLA] terraform no esta en PATH." >&2
  exit 1
}

export TF_VAR_aws_region="$AWS_REGION"
export TF_VAR_corpus_bucket_name="$CORPUS_BUCKET"
export TF_VAR_eval_bucket_name="$EVAL_BUCKET"
export TF_VAR_judge_model_id="$JUDGE_MODEL"

echo "[terraform] init"
terraform -chdir="$TF_DIR" init -input=false

echo "[terraform] plan"
terraform -chdir="$TF_DIR" plan -out=tfplan

echo
echo ">>> Revisa el plan. Crea: bucket de corpus ($CORPUS_BUCKET), bucket de"
echo ">>> eval ($EVAL_BUCKET), vector bucket + indice S3 Vectors, KB FIXED_SIZE,"
echo ">>> roles IAM para la KB y para eval jobs."
read -r -p "Aplicar? (escribe 'yes' para continuar) " confirm
[ "$confirm" = "yes" ] || { echo "Cancelado."; exit 1; }

terraform -chdir="$TF_DIR" apply tfplan

# --- Persistir outputs para que el resto de los scripts no requieran ------
# copiar/pegar IDs a mano. config.sh carga generated.env si existe.
echo "[terraform] escribiendo $HERE/generated.env"
{
  echo "# Generado por 01-terraform-apply.sh el $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "export KB_FIXED_ID=\"$(terraform -chdir="$TF_DIR" output -raw kb_fixed_id)\""
  echo "export KB_FIXED_DATA_SOURCE_ID=\"$(terraform -chdir="$TF_DIR" output -raw kb_fixed_data_source_id)\""
  echo "export KB_ROLE_ARN=\"$(terraform -chdir="$TF_DIR" output -raw kb_role_arn)\""
  echo "export EVAL_ROLE_ARN=\"$(terraform -chdir="$TF_DIR" output -raw eval_role_arn)\""
  echo "export VECTOR_BUCKET_NAME=\"$(terraform -chdir="$TF_DIR" output -raw vector_bucket_name)\""
} > "$HERE/generated.env"

echo
echo ">>> KB_FIXED_ID = $(terraform -chdir="$TF_DIR" output -raw kb_fixed_id)"
echo ">>> Vuelve a correr 'source config.sh' para cargar generated.env, o"
echo ">>> abre una terminal nueva. Continua con 02-create-managed-kb.py"
