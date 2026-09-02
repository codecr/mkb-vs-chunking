#!/usr/bin/env python3
"""
Lanza y espera los ingestion jobs de config A (S3 Vectors + FIXED_SIZE) y de
la Managed KB (configs B/C/D), y reporta las estadisticas reales.

Esto es lo que confirma o refuta una de las afirmaciones pendientes del
README: si Smart Parsing ingesta sin quejarse bedrock-agentcore-dg.pdf
(30.4 MB hoy) y wellarchitected-framework.pdf (14.2 MB), que en abril
reventaron las estrategias SEMANTIC y NONE bajo chunking manual. La columna
`numberOfDocumentsFailed` / `numberOfDocumentsSkipped` de la respuesta real
es el dato, no una suposicion.

Uso:
    source config.sh && python3 03-ingest-and-wait.py --all
    source config.sh && python3 03-ingest-and-wait.py --kb fixed
    source config.sh && python3 03-ingest-and-wait.py --kb managed
"""
import argparse
import json
import os
import pathlib
import time

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULTS = pathlib.Path(os.environ.get("RESULTS_DIR", "results"))

agent = boto3.client("bedrock-agent", region_name=REGION)

TARGETS = {
    "fixed": dict(kb_env="KB_FIXED_ID", ds_env="KB_FIXED_DATA_SOURCE_ID"),
    "managed": dict(kb_env="KB_MANAGED_ID", ds_env="KB_MANAGED_DATA_SOURCE_ID"),
}


def env_or_die(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(
            f"{name} sin definir. Corre 01-terraform-apply.sh y/o "
            f"02-create-managed-kb.py primero (escriben generated.env)."
        )
    return val


def ingest(target: str) -> dict:
    spec = TARGETS[target]
    kb_id = env_or_die(spec["kb_env"])
    ds_id = env_or_die(spec["ds_env"])

    print(f"[{target}] StartIngestionJob kb={kb_id} ds={ds_id}")
    try:
        resp = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    except ClientError as exc:
        err = exc.response["Error"]
        print(f"[{target}] [FALLA] StartIngestionJob: {err['Code']}: {err['Message']}")
        raise
    job_id = resp["ingestionJob"]["ingestionJobId"]
    print(f"[{target}] ingestionJobId={job_id}")

    t0 = time.perf_counter()
    while True:
        job = agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )["ingestionJob"]
        status = job["status"]
        elapsed = round(time.perf_counter() - t0, 1)
        print(f"[{target}] {elapsed}s status={status} stats={job.get('statistics')}")
        if status in ("COMPLETE", "FAILED", "STOPPED"):
            if status != "COMPLETE":
                print(f"[{target}] failureReasons: {job.get('failureReasons')}")
            return {
                "target": target,
                "knowledge_base_id": kb_id,
                "data_source_id": ds_id,
                "ingestion_job_id": job_id,
                "status": status,
                "statistics": job.get("statistics"),
                "failure_reasons": job.get("failureReasons"),
                "elapsed_s": elapsed,
            }
        time.sleep(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", choices=list(TARGETS))
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    targets = list(TARGETS) if args.all else ([args.kb] if args.kb else None)
    if not targets:
        ap.error("usa --all o --kb {fixed,managed}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "ingestion-report.json"
    reports = []
    for t in targets:
        reports.append(ingest(t))

    out.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[ingest] reporte -> {out}")

    failed = [r for r in reports if r["status"] != "COMPLETE"]
    if failed:
        raise SystemExit(
            f"{len(failed)} ingestion job(s) no llegaron a COMPLETE. "
            f"Ver {out} para el detalle exacto -- es material del articulo."
        )


if __name__ == "__main__":
    main()
