#!/usr/bin/env bash
# Verifica que todo lo que el benchmark asume sea cierto ANTES de gastar.
# Varias de estas verificaciones existen porque son afirmaciones que todavia
# no estan confirmadas contra la cuenta real. Si alguna falla, es material
# para el articulo, no un bug del script.
set -euo pipefail
source "$(dirname "$0")/config.sh"

# Preferir el venv del proyecto: boto3/botocore >= 1.43.32 es requisito duro
# (AgenticRetrieveStream y Managed Knowledge Bases se lanzaron juntos en esa
# version exacta, confirmado contra CHANGELOG.rst de botocore/boto3). El
# Python global de este equipo trae 1.43.10, insuficiente.
VENV_PY="$(dirname "$0")/.venv/Scripts/python.exe"
if [ -x "$VENV_PY" ]; then
  PYBIN="$VENV_PY"
else
  PYBIN="python3"
  command -v python3 >/dev/null 2>&1 || PYBIN="python"
  warn_novenv=1
fi

fail=0
ok()   { echo "  [ok]   $1"; }
warn() { echo "  [WARN] $1"; }
bad()  { echo "  [FALLA] $1"; fail=1; }

echo
echo "=== 1. Identidad y region ==="
aws sts get-caller-identity --output text --query 'Arn' >/dev/null 2>&1 \
  && ok "credenciales validas" || bad "sin credenciales AWS"
ok "region: $AWS_REGION"

echo
echo "=== 2. Versiones de SDK ==="
[ -n "${warn_novenv:-}" ] && warn "no existe .venv/, usando Python del sistema ($PYBIN)"
if ! "$PYBIN" - <<'PY'
import sys
try:
    import boto3, botocore
except ImportError:
    print("  [FALLA] boto3 no instalado"); sys.exit(1)

# Minimo confirmado en CHANGELOG.rst de botocore/boto3: AgenticRetrieveStream
# y Managed Knowledge Bases se lanzaron juntos en 1.43.32 exacto.
MIN = (1, 43, 32)
cur = tuple(int(x) for x in boto3.__version__.split("."))
if cur >= MIN:
    print(f"  [ok]   boto3 {boto3.__version__} / botocore {botocore.__version__} (>= 1.43.32)")
else:
    print(f"  [FALLA] boto3 {boto3.__version__} / botocore {botocore.__version__} "
          f"< 1.43.32 minimo confirmado (AgenticRetrieveStream no existira)")
    sys.exit(1)
PY
then
  bad "boto3/botocore por debajo del minimo confirmado"
fi

echo
echo "=== 3. Existe la operacion agentic_retrieve_stream en este boto3? ==="
# GOTCHA CANDIDATO: si boto3 es viejo, la operacion no existe y el error
# no menciona versiones. Vale la pena documentar la version minima real.
"$PYBIN" - <<'PY'
import boto3, os
c = boto3.client("bedrock-agent-runtime", region_name=os.environ["AWS_REGION"])
ops = c.meta.service_model.operation_names
if "AgenticRetrieveStream" in ops:
    print("  [ok]   AgenticRetrieveStream disponible")
else:
    print("  [FALLA] AgenticRetrieveStream ausente. Actualiza boto3.")
    print(f"          operaciones presentes: {sorted(ops)}")
PY

echo
echo "=== 4. Valores validos de foundationModelType ==="
# NO VERIFICADO: el blog de AWS solo documenta 'CUSTOM'. El enum del modelo
# gestionado no aparece en la doc publica revisada. Lo extraemos del shape.
"$PYBIN" - <<'PY'
import boto3, os
c = boto3.client("bedrock-agent-runtime", region_name=os.environ["AWS_REGION"])
try:
    shape = c.meta.service_model.shape_for("AgenticRetrieveConfiguration")
    fmt = shape.members.get("foundationModelType")
    print(f"  [ok]   foundationModelType admite: {getattr(fmt, 'enum', 'sin enum declarado')}")
    print(f"         miembros del shape: {list(shape.members)}")
except Exception as e:
    print(f"  [WARN] no se pudo inspeccionar el shape: {e}")
PY

echo
echo "=== 5. Sigue Sonnet 4.6 fuera del allowlist de jueces? ==="
# En abril esto fallaba y obligo a usar Nova Pro. Reverificar: si cambio,
# es una nota de actualizacion para el articulo de chunking.
"$PYBIN" - <<'PY'
import boto3, os
c = boto3.client("bedrock", region_name=os.environ["AWS_REGION"])
try:
    ms = c.list_foundation_models()["modelSummaries"]
    ids = {m["modelId"] for m in ms}
    print(f"  [info] {len(ids)} modelos visibles en la cuenta")
    print("  [nota] el allowlist de jueces NO se expone por API: se confirma")
    print("         lanzando un eval job de prueba en 07-launch-eval-jobs.py")
except Exception as e:
    print(f"  [WARN] {e}")
PY

echo
echo "=== 6. Corpus intacto ==="
if aws s3 ls "s3://${CORPUS_BUCKET}/${CORPUS_PREFIX}" >/dev/null 2>&1; then
  aws s3 ls "s3://${CORPUS_BUCKET}/${CORPUS_PREFIX}" --human-readable
  echo "  [ATENCION] verifica que los 3 archivos sean identicos a los de abril."
  echo "             Un PDF re-descargado puede diferir y arruinar la comparacion."
else
  bad "no se encuentra el corpus en s3://${CORPUS_BUCKET}/${CORPUS_PREFIX}"
fi

echo
echo "=== 7. KB de abril (configuracion A) ==="
if [ "$KB_FIXED_ID" = "REEMPLAZAR" ]; then
  warn "KB_FIXED_ID sin definir. Config A no se puede correr."
  warn "Si ya destruiste la KB de abril, recrearla con el Terraform de"
  warn "github.com/codecr/bedrock-chunking-benchmark antes de seguir."
else
  aws bedrock-agent get-knowledge-base --knowledge-base-id "$KB_FIXED_ID" \
    --query 'knowledgeBase.{id:knowledgeBaseId,status:status}' --output table \
    2>/dev/null && ok "KB de abril accesible" || bad "KB $KB_FIXED_ID no accesible"
fi

echo
[ "$fail" -eq 0 ] && echo ">>> Preflight OK. Continua con 01-terraform-apply.sh" \
                  || { echo ">>> Preflight con fallas. Resuelve antes de gastar."; exit 1; }
