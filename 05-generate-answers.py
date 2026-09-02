#!/usr/bin/env python3
"""
Genera respuestas con Sonnet 4.6 sobre los contextos recuperados por
04-run-retrieval.py, para las configs A, B, C y E (generador constante, solo
cambia la capa de retrieval). La config D no pasa por aqui: su respuesta ya
viene del propio servicio (generateResponse=True en AgenticRetrieveStream).

Usa la Converse API (no InvokeModel), con maxTokens explicito -- ver
Critical Warnings de la skill amazon-bedrock: dejarlo sin definir reserva
por defecto el maximo del modelo y puede disparar ThrottlingException sin
relacion aparente con el volumen real de trafico.

Uso:
    source config.sh && python3 05-generate-answers.py --all
    source config.sh && python3 05-generate-answers.py --config B --set A
"""
import argparse
import json
import os
import pathlib
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULTS = pathlib.Path(os.environ.get("RESULTS_DIR", "results"))
GENERATOR_MODEL = os.environ["GENERATOR_MODEL"]

# Respuesta tipo QA sobre un puñado de pasajes: 1024 tokens es un limite
# generoso para el genero de respuesta esperado, explicito para no heredar
# el maximo del modelo (ver docstring).
MAX_TOKENS = 1024

CONFIGS_TO_GENERATE = ["A", "B", "C", "E"]
SET_IDS = ["A", "B"]

runtime = boto3.client(
    "bedrock-runtime",
    region_name=REGION,
    config=Config(read_timeout=120, retries={"max_attempts": 3, "mode": "adaptive"}),
)

SYSTEM_PROMPT = (
    "Eres un asistente que responde preguntas usando UNICAMENTE los pasajes "
    "de contexto entregados. Si los pasajes no contienen evidencia suficiente "
    "para responder, dilo explicitamente en vez de inventar. Responde en "
    "espanol, de forma directa, sin repetir la pregunta."
)


def build_user_message(question: str, contexts: list) -> str:
    if not contexts:
        passages = "(no se recupero ningun contexto)"
    else:
        passages = "\n\n".join(
            f"[Pasaje {i+1}]\n{c.get('text', '')}" for i, c in enumerate(contexts)
        )
    return f"Contexto:\n{passages}\n\nPregunta: {question}"


def generate(question: str, contexts: list) -> dict:
    t0 = time.perf_counter()
    resp = runtime.converse(
        modelId=GENERATOR_MODEL,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": build_user_message(question, contexts)}]}],
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0},
    )
    elapsed = round(time.perf_counter() - t0, 3)
    text = "".join(
        block.get("text", "")
        for block in resp["output"]["message"]["content"]
        if "text" in block
    )
    return {
        "generated_answer": text,
        "generation_latency_s": elapsed,
        "generation_error": None,
        "stop_reason": resp.get("stopReason"),
        "usage": resp.get("usage"),
    }


def run(config_id: str, set_id: str) -> None:
    src = RESULTS / f"retrieval-{config_id}-set{set_id}.jsonl"
    if not src.exists():
        raise SystemExit(f"Falta {src}. Corre 04-run-retrieval.py primero.")

    out = RESULTS / f"answers-{config_id}-set{set_id}.jsonl"
    print(f"[{config_id}/set{set_id}] generando respuestas desde {src.name}")

    with src.open(encoding="utf-8") as fh_in, out.open("w", encoding="utf-8") as fh_out:
        for i, line in enumerate(fh_in, 1):
            rec = json.loads(line)
            if rec["status"] != "ok":
                # el error de retrieval ya quedo documentado; no generamos
                # sobre un registro que no tiene contextos validos.
                rec.update({"generated_answer": None, "generation_latency_s": None,
                            "generation_error": "skipped: retrieval status != ok"})
            else:
                try:
                    gen = generate(rec["question"], rec["contexts"])
                    rec.update(gen)
                except ClientError as exc:
                    err = exc.response["Error"]
                    msg = f"{err['Code']}: {err['Message']}"
                    print(f"    ! q{i}: {msg}")
                    rec.update({"generated_answer": None, "generation_latency_s": None,
                                "generation_error": msg})

            fh_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("generated_answer"):
                print(f"    q{i} {rec['generation_latency_s']}s "
                      f"{len(rec['generated_answer'])} chars")
            time.sleep(0.3)

    print(f"[{config_id}/set{set_id}] -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=CONFIGS_TO_GENERATE)
    ap.add_argument("--set", dest="set_id", choices=SET_IDS)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        for c in CONFIGS_TO_GENERATE:
            for s in SET_IDS:
                run(c, s)
    elif args.config and args.set_id:
        run(args.config, args.set_id)
    else:
        ap.error("usa --all o (--config y --set)")
