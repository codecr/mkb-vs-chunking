#!/usr/bin/env python3
"""
Crea la Amazon Bedrock Managed Knowledge Base (configs B, C, D).

knowledgeBaseConfiguration.type = "MANAGED" es una feature nueva: se lanzo
junto con AgenticRetrieveStream en botocore 1.43.32 (confirmado en
README.md, seccion "Afirmaciones - estado 2026-09-01"). No hay ejemplos de
codigo publicados en la doc revisada; los parametros de abajo salen de
inspeccionar el shape real de CreateKnowledgeBase/CreateDataSource en
botocore >= 1.43.32, no de un blog o tutorial:

  - storageConfiguration NO es requerido cuando type=MANAGED (el shape no
    lo marca como required_member). Se omite: "el vector store lo
    administra AWS" (README) significa literalmente no especificarlo.
  - managedKnowledgeBaseConfiguration.embeddingModelType admite CUSTOM o
    MANAGED (mismo patron que foundationModelType en agentic retrieve). Se
    usa MANAGED a proposito: el benchmark mide la experiencia gestionada
    por defecto, no una hibrida con embeddings propios.
GOTCHAS CONFIRMADOS 2026-09-01 (por ejecucion real, no por doc leida antes):

  1. CreateDataSource con type=S3 falla en una KB MANAGED:
     "ValidationException: Unsupported data source type for MANAGED
     knowledge base type." El shape de botocore lo insinuaba
     (DataSourceType incluye MANAGED_KNOWLEDGE_BASE_CONNECTOR) pero no
     bastaba para saberlo sin probar. La forma correcta, confirmada contra
     un ejemplo real de AWS (docs.aws.amazon.com/kendra/latest/dg/
     kendra-availability-change.html, seccion de migracion a Managed KB):
     dataSourceConfiguration.type = "MANAGED_KNOWLEDGE_BASE_CONNECTOR" con
     managedKnowledgeBaseConnectorConfiguration.connectorParameters =
     {"type": "S3", "version": "1", "connectionConfiguration": {...}}.
  2. CreateDataSource tambien es asincrono para Managed KB (a diferencia de
     la config A): transiciona CREATING -> AVAILABLE, "tipicamente en 2-5
     minutos" segun ese mismo ejemplo. Se espera antes de continuar.
  3. CreateKnowledgeBase en si tambien es asincrono (CREATING -> ACTIVE):
     llamar CreateDataSource mientras la KB sigue CREATING falla con
     "ConflictException: The Knowledge Base is not in a valid status."
     Por eso el orden aqui es: crear KB, esperar ACTIVE, crear data
     source, esperar AVAILABLE -- no al reves.

No se fija inclusionPrefixes en el filterConfiguration: se ingesta corpus/
Y side-test/ (el PDF escaneado prueba Smart Parsing, ver README - no entra
al set puntuado pero si a la KB).

GOTCHA PENDIENTE: se reutiliza KB_ROLE_ARN (el rol IAM creado para la
config A / S3 Vectors en 01-terraform-apply.sh) porque su trust policy ya
admite cualquier knowledge-base/* de la cuenta. No esta confirmado si un
embeddingModelType=MANAGED o el connector S3 gestionado necesitan permisos
de IAM distintos. Si falla por AccessDenied, el error exacto es material
del articulo, no se adivina un fix de antemano.
"""
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
PROJECT = os.environ.get("PROJECT", "mkb-vs-chunking")
CORPUS_BUCKET = os.environ["CORPUS_BUCKET"]
KB_ROLE_ARN = os.environ.get("KB_ROLE_ARN", "").strip()

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATED_ENV = os.path.join(HERE, "generated.env")

agent = boto3.client("bedrock-agent", region_name=REGION)


def append_generated_env(pairs: dict) -> None:
    """Actualiza generated.env preservando las claves ya escritas por otros
    scripts (01-terraform-apply.sh, este mismo en una corrida anterior)."""
    lines = []
    if os.path.exists(GENERATED_ENV):
        with open(GENERATED_ENV, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                key = line.replace("export ", "").split("=", 1)[0].strip()
                if key and key not in pairs:
                    lines.append(line)
    for k, v in pairs.items():
        lines.append(f'export {k}="{v}"')
    with open(GENERATED_ENV, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def wait_for_kb_active(kb_id: str) -> str:
    while True:
        kb = agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        status = kb["status"]
        print(f"[create-kb] status={status}")
        if status != "CREATING":
            if status == "FAILED":
                print(f"[create-kb] failureReasons: {kb.get('failureReasons')}")
            return status
        time.sleep(5)


def wait_for_ds_available(kb_id: str, ds_id: str) -> str:
    while True:
        ds = agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)["dataSource"]
        status = ds["status"]
        print(f"[create-kb] data source status={status}")
        if status not in ("CREATING", "UPDATING"):
            if status == "FAILED":
                print(f"[create-kb] failureReasons: {ds.get('failureReasons')}")
            return status
        time.sleep(10)


def main():
    if not KB_ROLE_ARN:
        raise SystemExit(
            "KB_ROLE_ARN sin definir. Corre 01-terraform-apply.sh primero "
            "(crea el rol IAM compartido) o exportalo a mano en generated.env."
        )
    parts = KB_ROLE_ARN.split(":")
    account_id = parts[4] if len(parts) > 4 else ""

    name = f"{PROJECT}-managed"
    print(f"[create-kb] creando Managed Knowledge Base '{name}'...")
    try:
        resp = agent.create_knowledge_base(
            name=name,
            description="mkb-vs-chunking: configs B/C/D, Amazon Bedrock Managed Knowledge Base",
            roleArn=KB_ROLE_ARN,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {
                    "embeddingModelType": "MANAGED",
                },
            },
        )
    except ClientError as exc:
        err = exc.response["Error"]
        print(f"[FALLA] CreateKnowledgeBase: {err['Code']}: {err['Message']}")
        raise

    kb = resp["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    print(f"[create-kb] KB creada: {kb_id} (status inicial={kb['status']})")

    # GOTCHA CONFIRMADO 2026-09-01: a diferencia de la config A (VECTOR +
    # S3 Vectors, que en terraform acepta CreateDataSource justo despues de
    # CreateKnowledgeBase), una Managed KB es asincrona. Llamar
    # CreateDataSource mientras status=CREATING falla con:
    #   ConflictException: The Knowledge Base is not in a valid status.
    #   Wait for the knowledge base to reach a valid status and try again.
    # Hay que esperar ACTIVE primero.
    status = wait_for_kb_active(kb_id)
    if status != "ACTIVE":
        raise SystemExit(f"KB en estado inesperado tras esperar: {status}")

    print(f"[create-kb] creando data source (MANAGED_KNOWLEDGE_BASE_CONNECTOR) "
          f"contra s3://{CORPUS_BUCKET}...")
    try:
        ds_resp = agent.create_data_source(
            knowledgeBaseId=kb_id,
            name=f"{name}-ds",
            dataSourceConfiguration={
                "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                "managedKnowledgeBaseConnectorConfiguration": {
                    "connectorParameters": {
                        "type": "S3",
                        "version": "1",
                        "connectionConfiguration": {
                            "bucketName": CORPUS_BUCKET,
                            "bucketOwnerAccountId": account_id,
                        },
                    }
                },
            },
            vectorIngestionConfiguration={
                "parsingConfiguration": {"parsingStrategy": "SMART_PARSING"},
            },
        )
    except ClientError as exc:
        err = exc.response["Error"]
        print(f"[FALLA] CreateDataSource: {err['Code']}: {err['Message']}")
        raise
    ds_id = ds_resp["dataSource"]["dataSourceId"]
    print(f"[create-kb] data source creado: {ds_id} (status inicial="
          f"{ds_resp['dataSource']['status']})")

    ds_status = wait_for_ds_available(kb_id, ds_id)
    if ds_status != "AVAILABLE":
        raise SystemExit(f"Data source en estado inesperado tras esperar: {ds_status}")

    append_generated_env({
        "KB_MANAGED_ID": kb_id,
        "KB_MANAGED_DATA_SOURCE_ID": ds_id,
    })
    print(f"[create-kb] KB_MANAGED_ID={kb_id} escrito en generated.env")
    print("[create-kb] Continua con 03-ingest-and-wait.py")


if __name__ == "__main__":
    main()
