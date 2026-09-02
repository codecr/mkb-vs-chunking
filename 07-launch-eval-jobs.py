#!/usr/bin/env python3
"""
Lanza un eval job BYO-inference (RAG, retrieve-and-generate) por cada
(config, set) usando los datasets de 06-build-eval-datasets.py.

Mecanismo BYOI confirmado contra el shape real de CreateEvaluationJob (no
un tutorial): inferenceConfig.ragConfigs[].precomputedRagSourceConfig.
retrieveAndGenerateSourceConfig.ragSourceIdentifier -- un config DISTINTO
de knowledgeBaseConfig (que es el path nativo). Esto es evidencia a favor
de que BYOI no invoca la KB en vivo durante el job (el ID de la KB solo
viaja como campo `knowledgeBaseIdentifier` dentro del JSONL), pero sigue
sin confirmarse al 100% hasta ver el resultado real: el rol de eval
mantiene el permiso bedrock:Retrieve/RetrieveAndGenerate de forma
defensiva (ver terraform/main.tf).

Metricas: las mismas 4 builtin de abril (Correctness/Completeness/
Helpfulness/Faithfulness). ContextRelevance/ContextCoverage pertenecen al
set "retrieve"-only y NO se pueden mezclar (gotcha #6 de abril) -- no se
piden aqui.

Gotcha de abril reproducido literalmente: el patron de modelIdentifier del
juez SI permite un ID corto tipo "amazon.nova-pro-v1:0" (confirmado contra
el regex del shape de botocore), pero en abril la invocacion fallo en
tiempo de ejecucion con "on-demand throughput isn't supported" y exigio un
ARN de inference profile. Se intenta primero el ID corto (es lo que dice
evaluation-kb.html); si sale ese error exacto, se reintenta con el
inference profile real (buscado con list_inference_profiles, no adivinado).

Uso:
    source config.sh && python3 07-launch-eval-jobs.py --all
    source config.sh && python3 07-launch-eval-jobs.py --config E            # ambos sets de E
    source config.sh && python3 07-launch-eval-jobs.py --config E --set A    # solo E/set A

--config filtra por prefijo del manifest (results/eval-datasets-manifest.json)
en vez de relanzar TODO lo que haya ahi -- --all vuelve a crear (y facturar)
un job por cada entrada existente, incluidas las configs ya completadas.
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
EVAL_BUCKET = os.environ["EVAL_BUCKET"]
JUDGE_MODEL = os.environ["JUDGE_MODEL"]
EVAL_ROLE_ARN = os.environ.get("EVAL_ROLE_ARN", "").strip()

KB_ID_ENV = {
    "A": "KB_FIXED_ID", "B": "KB_MANAGED_ID", "C": "KB_MANAGED_ID",
    "D": "KB_MANAGED_ID", "E": "KB_MANAGED_ID",
}

METRICS = [
    "Builtin.Correctness",
    "Builtin.Completeness",
    "Builtin.Helpfulness",
    "Builtin.Faithfulness",
]

bedrock = boto3.client("bedrock", region_name=REGION)


def find_inference_profile_for(model_id: str) -> str:
    """Busca un inference profile real que contenga model_id. No se adivina
    el prefijo (us./eu./global.): se lista y se filtra."""
    paginator = bedrock.get_paginator("list_inference_profiles")
    for page in paginator.paginate():
        for profile in page.get("inferenceProfileSummaries", []):
            models = [m.get("modelArn", "") for m in profile.get("models", [])]
            if any(model_id in m for m in models):
                return profile["inferenceProfileArn"]
    raise RuntimeError(f"No se encontro inference profile para {model_id}")


def create_job(config_id: str, set_id: str, dataset_uri: str, judge_identifier: str, kb_id: str) -> dict:
    job_name = f"mkb-vs-chunking-{config_id.lower()}-set{set_id.lower()}-{int(time.time())}"
    output_uri = f"s3://{EVAL_BUCKET}/output/{config_id}-set{set_id}/"

    return bedrock.create_evaluation_job(
        jobName=job_name,
        jobDescription=f"mkb-vs-chunking: config {config_id}, set {set_id} (BYO-inference)",
        roleArn=EVAL_ROLE_ARN,
        applicationType="RagEvaluation",
        evaluationConfig={
            "automated": {
                "datasetMetricConfigs": [
                    {
                        "taskType": "QuestionAndAnswer",
                        "dataset": {
                            "name": f"{config_id}-set{set_id}",
                            "datasetLocation": {"s3Uri": dataset_uri},
                        },
                        "metricNames": METRICS,
                    }
                ],
                "evaluatorModelConfig": {
                    "bedrockEvaluatorModels": [{"modelIdentifier": judge_identifier}]
                },
            }
        },
        # GOTCHA CONFIRMADO 2026-09-01: ragSourceIdentifier NO es una
        # etiqueta libre. Con f"{config_id}-set{set_id}" el servicio
        # rechaza el job:
        #   ValidationException: The provided dataset in your RAG
        #   evaluation configuration has different KnowledgeBaseIdentifiers
        #   in output than your RAGConfig.
        # Debe ser IGUAL al knowledgeBaseIdentifier que trae cada linea del
        # dataset (06-build-eval-datasets.py ya lo fija = kb_id real).
        inferenceConfig={
            "ragConfigs": [
                {
                    "precomputedRagSourceConfig": {
                        "retrieveAndGenerateSourceConfig": {
                            "ragSourceIdentifier": kb_id
                        }
                    }
                }
            ]
        },
        outputDataConfig={"s3Uri": output_uri},
    )


def create_job_with_fallback(config_id: str, set_id: str, dataset_uri: str, kb_id: str) -> dict:
    try:
        resp = create_job(config_id, set_id, dataset_uri, JUDGE_MODEL, kb_id)
        print(f"[{config_id}/set{set_id}] job creado con modelIdentifier corto: {JUDGE_MODEL}")
        return resp
    except ClientError as exc:
        err = exc.response["Error"]
        msg = f"{err['Code']}: {err['Message']}"
        print(f"[{config_id}/set{set_id}] fallo con ID corto: {msg}")
        if "on-demand throughput" not in err["Message"].lower():
            raise  # error distinto, no es el gotcha conocido de abril
        profile_arn = find_inference_profile_for(JUDGE_MODEL)
        print(f"[{config_id}/set{set_id}] reintentando con inference profile: {profile_arn}")
        return create_job(config_id, set_id, dataset_uri, profile_arn, kb_id)


def poll(job_arn: str, config_id: str, set_id: str) -> dict:
    while True:
        job = bedrock.get_evaluation_job(jobIdentifier=job_arn)
        status = job["status"]
        print(f"[{config_id}/set{set_id}] status={status}")
        if status in ("Completed", "Failed", "Stopped"):
            return {"status": status, "failure_messages": job.get("failureMessages", [])}
        time.sleep(30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="lanza un job por cada entrada del manifest")
    ap.add_argument("--config", choices=list(KB_ID_ENV), help="filtra a una sola config")
    ap.add_argument("--set", dest="set_id", choices=["A", "B"], help="requiere --config")
    ap.add_argument("--no-wait", action="store_true", help="no bloquear esperando resultados")
    args = ap.parse_args()
    if not args.all and not args.config:
        ap.error("usa --all o --config (opcionalmente con --set)")
    if args.set_id and not args.config:
        ap.error("--set requiere --config")

    if not EVAL_ROLE_ARN:
        raise SystemExit("EVAL_ROLE_ARN sin definir. Corre 01-terraform-apply.sh primero.")

    manifest_path = RESULTS / "eval-datasets-manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Falta {manifest_path}. Corre 06-build-eval-datasets.py primero.")
    datasets = json.loads(manifest_path.read_text(encoding="utf-8"))

    if args.config:
        wanted = f"{args.config}-set{args.set_id}" if args.set_id else f"{args.config}-set"
        datasets = {k: v for k, v in datasets.items() if k.startswith(wanted)}
        if not datasets:
            ap.error(f"nada en el manifest para config={args.config} set={args.set_id or '(cualquiera)'}")

    out = RESULTS / "eval-jobs.json"
    # Merge, no overwrite: con --config solo se toca un subconjunto de keys,
    # y pisar el archivo entero borraria el registro de jobs de otras
    # configs ya lanzados (p.ej. A-D al agregar E despues).
    jobs = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}

    for key, uri in datasets.items():
        config_id, set_part = key.split("-set")
        kb_id = os.environ.get(KB_ID_ENV[config_id], "").strip()
        if not kb_id:
            print(f"[{key}] [FALLA] {KB_ID_ENV[config_id]} sin definir")
            jobs[key] = {"error": f"{KB_ID_ENV[config_id]} sin definir", "dataset_uri": uri}
            continue
        try:
            resp = create_job_with_fallback(config_id, set_part, uri, kb_id)
            jobs[key] = {"job_arn": resp["jobArn"], "dataset_uri": uri}
        except ClientError as exc:
            err = exc.response["Error"]
            print(f"[{key}] [FALLA] CreateEvaluationJob: {err['Code']}: {err['Message']}")
            jobs[key] = {"error": f"{err['Code']}: {err['Message']}", "dataset_uri": uri}

    out.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[jobs] manifest -> {out}")

    if args.no_wait:
        print("[jobs] --no-wait: no se espera resultado. Corre 08-collect-results.py mas tarde.")
        return

    # Solo lo lanzado en ESTA corrida -- jobs puede traer historial de otras
    # configs por el merge de arriba, y no hay que volver a esperar sobre eso.
    for key in datasets:
        info = jobs[key]
        if "job_arn" not in info:
            continue
        config_id, set_part = key.split("-set")
        info.update(poll(info["job_arn"], config_id, set_part))

    out.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[jobs] manifest final -> {out}")


if __name__ == "__main__":
    main()
