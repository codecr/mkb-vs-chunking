#!/usr/bin/env bash
# Destruye TODO lo que crean 01-terraform-apply.sh y 02-create-managed-kb.py.
#
# Orden importa: primero la Managed KB (config B/C/D/E, creada por boto3 en
# 02-create-managed-kb.py, FUERA de terraform) y DESPUES terraform destroy.
# La Managed KB usa KB_ROLE_ARN, que es un recurso de terraform (aws_iam_role
# "kb", ver terraform/main.tf) -- si el orden se invierte y terraform borra
# el rol primero, la Managed KB queda huerfana en vez de borrarse limpio.
#
# NO usa -auto-approve en terraform destroy: revisa el plan antes de escribir
# "yes", igual que 01-terraform-apply.sh.
#
# GOTCHA CONFIRMADO por ejecucion real: DeleteKnowledgeBase y
# DeleteDataSource son asincronos, igual que su contraparte de creacion (el
# paso 1 de abajo, sobre la Managed KB, corrio limpio esperando
# ResourceNotFoundException -- ~4 min el data source, ~2 min la KB).
#
# GOTCHA CONFIRMADO (el importante, corriendo terraform destroy contra la
# cuenta real): el paso 2 fallo la primera vez con
#   Error: waiting for Bedrock Agent Data Source ... delete
#   unexpected state 'DELETE_UNSUCCESSFUL' ... Unable to delete data from
#   vector store for data source with ID ...
# Causa real: terraform borro la inline policy s3vectors-access del rol de
# la KB en PARALELO con el data source de config A (son recursos hermanos
# sin dependencia explicita entre si -- solo dependen ambos de
# aws_iam_role.kb). El borrado de vectores en el backend de Bedrock no es
# instantaneo; para cuando corrio, el permiso ya no estaba. Arreglado con
# depends_on explicito en terraform/main.tf (module "kb_fixed") -- si
# segui viendo este error con el codigo actual, es un problema nuevo, no
# el mismo.
#
# Recuperacion manual que se necesito una vez (no debiera repetirse con el
# fix de arriba, documentado por si acaso): reponer la inline policy
# borrada, reintentar DeleteDataSource (funciona: retoma un
# DELETE_UNSUCCESSFUL en vez de fallar de nuevo), esperar a que desaparezca,
# recien ahi borrar KB / rol / indice+bucket S3 Vectors / bucket de corpus.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"

TF_DIR="$HERE/terraform"

command -v terraform >/dev/null 2>&1 || {
  echo "[FALLA] terraform no esta en PATH." >&2
  exit 1
}

wait_for_data_source_gone() {
  local kb_id="$1" ds_id="$2"
  while true; do
    if ! aws bedrock-agent get-data-source \
        --knowledge-base-id "$kb_id" --data-source-id "$ds_id" \
        --query 'dataSource.status' --output text 2>/dev/null; then
      echo "[teardown] data source $ds_id ya no existe."
      return 0
    fi
    sleep 10
  done
}

wait_for_kb_gone() {
  local kb_id="$1"
  while true; do
    if ! aws bedrock-agent get-knowledge-base \
        --knowledge-base-id "$kb_id" \
        --query 'knowledgeBase.status' --output text 2>/dev/null; then
      echo "[teardown] KB $kb_id ya no existe."
      return 0
    fi
    sleep 10
  done
}

# --- 1. Managed KB (config B/C/D/E) -----------------------------------------
if [ -n "${KB_MANAGED_ID:-}" ]; then
  echo "[teardown] Managed KB encontrada: $KB_MANAGED_ID"
  read -r -p "Borrar la Managed KB $KB_MANAGED_ID y su data source? (escribe 'yes') " confirm_kb
  if [ "$confirm_kb" = "yes" ]; then
    if [ -n "${KB_MANAGED_DATA_SOURCE_ID:-}" ]; then
      echo "[teardown] DeleteDataSource $KB_MANAGED_DATA_SOURCE_ID"
      aws bedrock-agent delete-data-source \
        --knowledge-base-id "$KB_MANAGED_ID" \
        --data-source-id "$KB_MANAGED_DATA_SOURCE_ID" >/dev/null
      wait_for_data_source_gone "$KB_MANAGED_ID" "$KB_MANAGED_DATA_SOURCE_ID"
    fi
    echo "[teardown] DeleteKnowledgeBase $KB_MANAGED_ID"
    aws bedrock-agent delete-knowledge-base --knowledge-base-id "$KB_MANAGED_ID" >/dev/null
    wait_for_kb_gone "$KB_MANAGED_ID"
    echo "[teardown] Managed KB borrada."
  else
    echo "[teardown] Managed KB conservada -- el terraform destroy de abajo puede"
    echo "           fallar (o dejarla huerfana) si borra el rol IAM que usa."
  fi
else
  echo "[teardown] KB_MANAGED_ID vacio en generated.env -- nada que borrar aqui"
  echo "           (¿ya se borro antes, o nunca se corrio 02-create-managed-kb.py?)."
fi

# --- 2. Todo lo de terraform: config A (KB fixed, S3 Vectors), ambos -------
#        buckets (force_destroy=true, borran objetos tambien), roles IAM ---
export TF_VAR_aws_region="$AWS_REGION"
export TF_VAR_corpus_bucket_name="$CORPUS_BUCKET"
export TF_VAR_eval_bucket_name="$EVAL_BUCKET"
export TF_VAR_judge_model_id="$JUDGE_MODEL"

echo
echo "[terraform] plan -destroy"
terraform -chdir="$TF_DIR" plan -destroy -out=tfdestroy

echo
echo ">>> Revisa el plan. Destruye: bucket de corpus ($CORPUS_BUCKET, con"
echo ">>> force_destroy=true -- borra los objetos tambien), bucket de eval"
echo ">>> ($EVAL_BUCKET, idem), vector bucket + indice S3 Vectors, KB"
echo ">>> FIXED_SIZE, y los roles IAM de KB y de eval jobs."
read -r -p "Destruir? (escribe 'yes' para continuar) " confirm_tf
[ "$confirm_tf" = "yes" ] || {
  echo "Cancelado. Lo borrado en el paso 1 (si aplica) ya no existe;"
  echo "el resto de la infraestructura de terraform sigue en pie."
  exit 1
}

terraform -chdir="$TF_DIR" apply tfdestroy

echo
echo "[teardown] listo. generated.env todavia tiene los IDs viejos -- ya no"
echo "           son validos. Borralo (o dejalo: 01-terraform-apply.sh y"
echo "           02-create-managed-kb.py lo regeneran en la proxima corrida)."
