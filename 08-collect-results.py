#!/usr/bin/env python3
"""
Descarga y agrega los resultados de los eval jobs lanzados por
07-launch-eval-jobs.py.

RESUELTO 2026-09-01 EJECUTANDO (no la doc oficial): "RAG evaluation"
(applicationType=RagEvaluation) usa un esquema de salida DISTINTO al de
"automated MODEL evaluation" documentado (datasets/metric_input-dataset.jsonl,
clave raiz automatedEvaluationResult.scores). El real, confirmado contra un
output de un job BYO-inference retrieve-and-generate real, es:

  {"conversationTurns": [{
    "inputRecord": {...}, "output": {...},
    "results": [{"metricName": "Builtin.Correctness",
                  "evaluatorDetails": [{"modelIdentifier": "amazon.nova-pro-v1:0",
                                        "explanation": "..."}],
                  "result": 1.0}, ...]
  }]}

Un solo archivo JSONL por job, en
output/<jobName>/<jobId>/inference_configs/0/datasets/<datasetName>/<uuid>_output.jsonl
(nombre generado por el servicio, no elegido por nosotros). Este script
sigue sin asumir la ruta exacta: lista TODO bajo el prefijo de salida y
parsea `results`; si algun dia cambia de nuevo, el registro crudo se
imprime tal cual en vez de fallar en silencio.

Uso:
    source config.sh && python3 08-collect-results.py
"""
import json
import os
import pathlib
import statistics

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULTS = pathlib.Path(os.environ.get("RESULTS_DIR", "results"))
EVAL_BUCKET = os.environ["EVAL_BUCKET"]

s3 = boto3.client("s3", region_name=REGION)


def list_jsonl_under(prefix: str) -> list:
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=EVAL_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith((".jsonl", ".json")):
                keys.append(obj["Key"])
    return keys


def download_jsonl(key: str) -> list:
    body = s3.get_object(Bucket=EVAL_BUCKET, Key=key)["Body"].read().decode("utf-8")
    records = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # el archivo podia no ser JSONL (p.ej. un .json unico)
    if not records:
        try:
            records = [json.loads(body)]
        except json.JSONDecodeError:
            print(f"    [WARN] {key} no parseable como JSON/JSONL, se ignora")
    return records


def extract_scores(record: dict) -> list:
    """
    CONFIRMADO 2026-09-01 contra un output real de RAG evaluation (no la
    doc de "automated MODEL evaluation", que usa un esquema distinto):

      {"conversationTurns": [{"inputRecord": ..., "output": ...,
        "results": [{"metricName": "Builtin.Correctness",
                      "evaluatorDetails": [{"modelIdentifier": ...,
                                            "explanation": ...}],
                      "result": 1.0}, ...]}]}

    Devuelve una lista de dicts {metric_name: score}, uno por conversationTurn
    (normalmente 1 por linea, pero el campo es una lista por si hay mas).
    """
    out = []
    for turn in record.get("conversationTurns", []):
        results = turn.get("results")
        if not results:
            continue
        scores = {
            r["metricName"]: r["result"]
            for r in results
            if "metricName" in r and "result" in r
        }
        if scores:
            out.append(scores)
    return out


def collect_for(key: str, prefix: str, job_id: str) -> dict:
    print(f"[{key}] listando s3://{EVAL_BUCKET}/{prefix}")
    all_files = list_jsonl_under(prefix)
    # GOTCHA CONFIRMADO 2026-09-01: el prefijo output/<config>-set<set>/ es
    # compartido por TODAS las corridas historicas de ese mismo config/set
    # (cada corrida crea su propia subcarpeta <jobName>/<jobId>/...). Sin
    # filtrar por jobId, una segunda corrida (p.ej. para validar un fix)
    # mezcla silenciosamente sus scores con los de la corrida anterior.
    files = [f for f in all_files if f"/{job_id}/" in f]
    if len(files) != len(all_files):
        print(f"[{key}] [ATENCION] {len(all_files)} archivo(s) totales bajo el "
              f"prefijo, de corridas anteriores incluidas; filtrado a "
              f"{len(files)} del job actual ({job_id})")
    print(f"[{key}] {len(files)} archivo(s) de este job: {files}")

    all_scores = []
    raw_unmatched = []
    for f in files:
        for rec in download_jsonl(f):
            scores = extract_scores(rec)
            if scores:
                all_scores.extend(scores)
            else:
                raw_unmatched.append(rec)

    if raw_unmatched and not all_scores:
        print(f"[{key}] [ATENCION] ningun registro con la forma esperada "
              f"(automatedEvaluationResult.scores). Ejemplo crudo:")
        print(json.dumps(raw_unmatched[0], indent=2, ensure_ascii=False)[:2000])

    # GOTCHA CONFIRMADO 2026-09-01: el juez (Nova Pro) a veces devuelve un
    # "result" null. Ejemplo real (config D, pregunta sobre la API de
    # AgentCore Memory): el juez respondio con el SCHEMA JSON crudo en vez
    # de un veredicto (confundio instrucciones con schema de salida), y el
    # servicio no pudo parsear un score. Se documenta el conteo de nulls
    # por metrica en vez de ocultarlos promediando solo sobre lo valido.
    metric_names = sorted({m for s in all_scores for m in s})
    null_counts = {
        m: sum(1 for s in all_scores if m in s and s[m] is None)
        for m in metric_names
    }
    averages = {
        m: statistics.mean(s[m] for s in all_scores if s.get(m) is not None)
        for m in metric_names
        if any(s.get(m) is not None for s in all_scores)
    }
    return {
        "files": files,
        "n_records": len(all_scores),
        "n_unmatched": len(raw_unmatched),
        "averages": averages,
        "null_result_counts": {m: n for m, n in null_counts.items() if n > 0},
    }


def main():
    jobs_path = RESULTS / "eval-jobs.json"
    if not jobs_path.exists():
        raise SystemExit(f"Falta {jobs_path}. Corre 07-launch-eval-jobs.py primero.")
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))

    report = {}
    for key, info in jobs.items():
        if info.get("status") != "Completed":
            print(f"[{key}] status={info.get('status')}, no se recolecta (solo Completed)")
            report[key] = {"status": info.get("status"), "error": info.get("error")}
            continue
        # mismo prefijo que se paso en outputDataConfig.s3Uri al crear el job
        config_id, set_part = key.split("-set")
        prefix = f"output/{config_id}-set{set_part}/"
        job_id = info["job_arn"].rsplit("/", 1)[-1]
        report[key] = collect_for(key, prefix, job_id)

    out = RESULTS / "eval-scores.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[collect] -> {out}")

    print("\n=== Promedios por config/set ===")
    for key, r in report.items():
        if "averages" in r:
            print(f"{key:12s} {r['averages']}")


if __name__ == "__main__":
    main()
