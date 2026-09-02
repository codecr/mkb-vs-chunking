#!/usr/bin/env python3
"""
Construye los datasets BYO-inference (formato "Retrieve and Generate") para
las configuraciones A-E y los sube a EVAL_BUCKET.

BYO-inference es obligatorio para TODAS las configuraciones, incluida A: los
scores de abril salieron del path nativo retrieveAndGenerate, que usa un
mecanismo de evaluacion distinto (la KB y el generador se invocan DENTRO del
eval job). Mezclar scores de dos mecanismos distintos seria un confounder.
Ver README.md.

Formato confirmado contra el anuncio oficial de GA (no un tutorial de
tercero ni una suposicion):
https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/
seccion "Bring Your Own Inference responses" (RAG evaluation, Retrieve and
Generate). knowledgeBaseIdentifier debe ser el mismo para todas las lineas
del dataset Y para el job completo -- por eso un job = una config, nunca
mezcladas.

GOTCHA PENDIENTE (no se puede confirmar sin ejecutar 07): el formato exige
un campo `citations`, pero aclara que si no tienes citas reales puedes
poner datos dummy siempre que no selecciones las metricas de citation
precision/coverage (no las usamos: los 4 builtin son Correctness/
Completeness/Helpfulness/Faithfulness). Se manda `citations: []`. Si el
servicio la rechaza por vacia, el error exacto es material del articulo.

Uso:
    source config.sh && python3 06-build-eval-datasets.py --all
"""
import argparse
import json
import os
import pathlib

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULTS = pathlib.Path(os.environ.get("RESULTS_DIR", "results"))
EVAL_BUCKET = os.environ["EVAL_BUCKET"]
GENERATOR_MODEL = os.environ["GENERATOR_MODEL"]

KB_ID_ENV = {
    "A": "KB_FIXED_ID", "B": "KB_MANAGED_ID", "C": "KB_MANAGED_ID",
    "D": "KB_MANAGED_ID", "E": "KB_MANAGED_ID",
}
# A/B/C/E: la respuesta la genera 05-generate-answers.py (Sonnet 4.6) sobre
# el archivo de retrieval. D: la respuesta ya viene del propio servicio
# dentro de retrieval-D-set*.jsonl (generateResponse=True), no pasa por 05.
SOURCE_FILE = {
    "A": "answers-{c}-set{s}.jsonl",
    "B": "answers-{c}-set{s}.jsonl",
    "C": "answers-{c}-set{s}.jsonl",
    "D": "retrieval-{c}-set{s}.jsonl",
    "E": "answers-{c}-set{s}.jsonl",
}
SET_IDS = ["A", "B"]

s3 = boto3.client("s3", region_name=REGION)


def kb_id_for(config_id: str) -> str:
    val = os.environ.get(KB_ID_ENV[config_id], "").strip()
    if not val:
        raise SystemExit(
            f"{KB_ID_ENV[config_id]} sin definir (config {config_id}). "
            f"Corre 01-terraform-apply.sh y/o 02-create-managed-kb.py."
        )
    return val


def build_turn(rec: dict, kb_id: str, config_id: str) -> dict | None:
    if rec.get("status") != "ok" or not rec.get("generated_answer"):
        return None

    turn = {
        "prompt": {"content": [{"text": rec["question"]}]},
        "output": {
            "text": rec["generated_answer"],
            "knowledgeBaseIdentifier": kb_id,
            "retrievedPassages": {
                "retrievalResults": [
                    {"content": {"text": c.get("text", "")}}
                    for c in rec.get("contexts", [])
                    if c.get("text")
                ]
            },
            # Ver docstring: dummy porque no seleccionamos metricas de citas.
            "citations": [],
        },
    }
    if rec.get("ground_truth"):
        turn["output"]["knowledgeBaseIdentifier"] = kb_id  # explicito, ver arriba
        turn["referenceResponses"] = [{"content": [{"text": rec["ground_truth"]}]}]
    if config_id != "D":
        turn["output"]["modelIdentifier"] = GENERATOR_MODEL
    return turn


def build(config_id: str, set_id: str) -> pathlib.Path:
    src = RESULTS / SOURCE_FILE[config_id].format(c=config_id, s=set_id)
    if not src.exists():
        raise SystemExit(f"Falta {src}.")

    kb_id = kb_id_for(config_id)
    out_dir = RESULTS / "eval-datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"eval-{config_id}-set{set_id}.jsonl"

    n_in, n_out = 0, 0
    with src.open(encoding="utf-8") as fh_in, out.open("w", encoding="utf-8") as fh_out:
        for line in fh_in:
            n_in += 1
            rec = json.loads(line)
            turn = build_turn(rec, kb_id, config_id)
            if turn is None:
                continue
            fh_out.write(json.dumps({"conversationTurns": [turn]}, ensure_ascii=False) + "\n")
            n_out += 1

    print(f"[{config_id}/set{set_id}] {n_out}/{n_in} preguntas -> {out}")
    if n_out == 0:
        raise SystemExit(f"[{config_id}/set{set_id}] 0 registros validos, no se sube nada.")
    return out


def upload(path: pathlib.Path, config_id: str, set_id: str) -> str:
    key = f"input/eval-{config_id}-set{set_id}.jsonl"
    s3.upload_file(str(path), EVAL_BUCKET, key)
    uri = f"s3://{EVAL_BUCKET}/{key}"
    print(f"[{config_id}/set{set_id}] -> {uri}")
    return uri


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=list(KB_ID_ENV))
    ap.add_argument("--set", dest="set_id", choices=SET_IDS)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        pairs = [(c, s) for c in KB_ID_ENV for s in SET_IDS]
    elif args.config and args.set_id:
        pairs = [(args.config, args.set_id)]
    else:
        ap.error("usa --all o (--config y --set)")

    manifest_path = RESULTS / "eval-datasets-manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for c, s in pairs:
        path = build(c, s)
        uri = upload(path, c, s)
        manifest[f"{c}-set{s}"] = uri

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n[manifest] -> {manifest_path}")


if __name__ == "__main__":
    main()
