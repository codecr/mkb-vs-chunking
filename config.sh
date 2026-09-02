#!/usr/bin/env bash
# Variables compartidas por todos los scripts del benchmark.
# Se carga con: source ./config.sh

export AWS_REGION="${AWS_REGION:-us-east-1}"
export PROJECT="mkb-vs-chunking"

# Windows: sin esto, print() de texto con acentos puede salir mangled en
# consola (los archivos ya se escriben con encoding="utf-8" explicito en
# cada script; esto es solo para lo que se ve en pantalla).
export PYTHONIOENCODING="utf-8"

# Valores generados por 01-terraform-apply.sh (KB_FIXED_ID, ...) y
# 02-create-managed-kb.py (KB_MANAGED_ID). Se cargan ANTES de los defaults
# de abajo para que ${VAR:-default} respete lo ya generado.
GENERATED_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/generated.env"
[ -f "$GENERATED_ENV" ] && source "$GENERATED_ENV"

# --- Bucket del corpus -------------------------------------------------------
# CONFIRMADO 2026-09-01: el bucket de abril (gerardo-chunking-benchmark-corpus)
# ya NO existe (borrado completo por el usuario). Se recrea desde cero en
# 01-terraform-apply.sh con el mismo nombre.
#
# GOTCHA CONFIRMADO: el corpus NO puede ser byte-identico al de abril.
# data/corpus/ esta en .gitignore en el repo de abril (nunca se versiono el
# contenido) y no quedo ningun hash/manifest. Verificado por HTTP HEAD hoy:
#   - bedrock-agentcore-dg.pdf: 30,420,374 bytes hoy vs. ~17 MB en abril
#     (el README de abril menciona 17 MB) -> cambio de tamano confirmado,
#     casi el doble. NO es el mismo archivo.
#   - wellarchitected-framework.pdf: 14,189,927 bytes hoy, similar a los
#     "14 MB" que cita el README de abril, pero Last-Modified es de hoy y
#     sin hash de abril no se puede afirmar identidad, solo similitud de
#     tamano.
#   - blog-rag-evaluation.html: Last-Modified 2026-08-18, posterior a abril.
# Esto va al articulo como limitacion explicita, no se oculta.
export CORPUS_BUCKET="${CORPUS_BUCKET:-gerardo-chunking-benchmark-corpus}"
export CORPUS_PREFIX="corpus/"

# Prueba lateral de Smart Parsing: NO entra al set puntuado.
export SIDE_TEST_PREFIX="side-test/"

# --- KB de abril, configuracion A (FIXED_SIZE + S3 Vectors) ------------------
# CONFIRMADO 2026-09-01: la KB de abril ya no existe. Verificado con
# `aws bedrock-agent list-knowledge-bases` en us-east-1/us-west-2/us-east-2/
# eu-west-1/eu-central-1 (0 resultados en todas), `aws s3vectors
# list-vector-buckets` (0 buckets), y el bucket del corpus (no existe).
# Ningun terraform state sobrevive tampoco (bucket akarui-terraform-state
# sin objetos con "chunking"/"mkb"/"benchmark").
#
# Se recrea en 01-terraform-apply.sh reutilizando SOLO el modulo kb_fixed
# del repo de abril (no las 5 estrategias completas: aqui solo hace falta
# la config A). Parametros confirmados contra ese repo
# (D:\POC\kb\bedrock-chunking-benchmark/terraform):
#   embedding_model_id = amazon.titan-embed-text-v2:0
#   embedding_dimensions = 1024
#   chunking_strategy = FIXED_SIZE
#   fixed_size_max_tokens = 512
#   fixed_size_overlap_percentage = 20
export APRIL_REPO_PATH="${APRIL_REPO_PATH:-/d/POC/kb/bedrock-chunking-benchmark}"
export KB_FIXED_ID="${KB_FIXED_ID:-REEMPLAZAR}"

# --- Managed KB nueva (configuraciones B, C, D) -----------------------------
# Se llena tras correr 02-create-managed-kb.py
export KB_MANAGED_ID="${KB_MANAGED_ID:-}"

# --- Modelos ----------------------------------------------------------------
# Generador: constante en A, B y C para aislar la capa de retrieval.
# Mismo modelo que el benchmark de abril.
export GENERATOR_MODEL="global.anthropic.claude-sonnet-4-6"

# Juez: cross-family. Sonnet 4.6 NO estaba en el allowlist de jueces en abril.
# 00-preflight.sh vuelve a verificarlo: pudo haber cambiado.
export JUDGE_MODEL="amazon.nova-pro-v1:0"

# Planner de agentic retrieval.
# Vacio = modelo gestionado por el servicio ($4/1000 llamadas), que es lo que
# obtiene un usuario por defecto. Si se define, se usa como CUSTOM.
# NO usar Nova Pro aqui: contamina al juez.
export PLANNER_MODEL_ARN="${PLANNER_MODEL_ARN:-}"

# --- Parametros de retrieval ------------------------------------------------
export MAX_RESULTS=10          # equivalente al topK usado en abril
export MAX_AGENT_ITERATION=3   # recomendacion AWS para KB unica

# --- Salidas ----------------------------------------------------------------
export RESULTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/results"
export EVAL_BUCKET="${EVAL_BUCKET:-gerardo-mkb-benchmark-eval}"

echo "[config] region=$AWS_REGION generador=$GENERATOR_MODEL juez=$JUDGE_MODEL"
