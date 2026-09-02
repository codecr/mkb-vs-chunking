#!/usr/bin/env python3
"""
Ejecuta las 4 configuraciones de retrieval sobre ambos sets de preguntas.

    A  S3 Vectors + FIXED_SIZE  ->  Retrieve            (linea base de abril)
    B  Managed KB              ->  Retrieve
    C  Managed KB              ->  AgenticRetrieveStream(generateResponse=False)  planner MANAGED
    D  Managed KB              ->  AgenticRetrieveStream(generateResponse=True)   planner MANAGED
    E  Managed KB              ->  AgenticRetrieveStream(generateResponse=False)  planner CUSTOM = Sonnet 4.6

A, B, C y E solo cambian la capa de retrieval: la generacion la hace despues
05-generate-answers.py con Sonnet 4.6 para las cuatro. D usa la respuesta que
genera el propio servicio.

E es la prueba pendiente del README ("un planner mas grande que Haiku 4.5"):
mismo generateResponse=False que C, mismo Set A/B, unico cambio es el
planner. Usa Sonnet 4.6 explicito (no la env var PLANNER_MODEL_ARN que usan
C/D) para no depender de estado externo -- correr E nunca debe poder pisar
los resultados oficiales de C. Nova Premier era la otra alternativa citada
en el README pero quedo descartada: confirmado contra la cuenta real
(list_foundation_models, 2026-09-02) que amazon.nova-premier-v1:0 esta
LEGACY con endOfLifeTime 2026-09-14. Sonnet 4.6 como planner colisiona a
proposito con el generador (misma familia) -- desviacion documentada del
aislamiento de roles que mantuvo la corrida exploratoria con Haiku.

Guarda un JSONL por (config, set) en results/ con contextos, latencia y,
cuando aplica, el trace completo del planner.

Uso:
    source config.sh && python3 04-run-retrieval.py --config C --set A
    source config.sh && python3 04-run-retrieval.py --all
"""
import argparse
import json
import os
import pathlib
import time

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULTS = pathlib.Path(os.environ.get("RESULTS_DIR", "results"))
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "10"))
MAX_ITER = int(os.environ.get("MAX_AGENT_ITERATION", "3"))
PLANNER_ARN = os.environ.get("PLANNER_MODEL_ARN", "").strip()

# read_timeout generoso: agentic retrieval hace varias llamadas al planner
# en serie y el default de 60s se queda corto en preguntas de 4 saltos.
runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=REGION,
    config=Config(read_timeout=300, retries={"max_attempts": 3}),
)

QUESTION_SETS = {
    "A": "questions/set-a-singlehop.jsonl",   # las 25 de abril, sin tocar
    "B": "questions/set-b-multihop.jsonl",    # nuevas, multi-hop/comparativas
}


def load_questions(set_id):
    path = pathlib.Path(QUESTION_SETS[set_id])
    if not path.exists():
        raise SystemExit(f"Falta {path}. Ver questions/README.md")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------------
# Configuraciones A y B: Retrieve clasico
# --------------------------------------------------------------------------
def run_retrieve(kb_id, query, managed=False):
    """
    Retrieve de un solo paso. Devuelve contextos + latencia.

    GOTCHA CONFIRMADO 2026-09-01: una Managed KB rechaza
    vectorSearchConfiguration con:
      ValidationException: Incompatible configuration: vectorSearchConfiguration
      is not supported for managed knowledge bases. Use managedSearchConfiguration
      instead.
    Config A (VECTOR + S3 Vectors) usa vectorSearchConfiguration; config B
    (Managed KB) usa managedSearchConfiguration -- mismo shape de
    numberOfResults, distinta clave contenedora.
    """
    t0 = time.perf_counter()
    search_key = "managedSearchConfiguration" if managed else "vectorSearchConfiguration"
    resp = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            search_key: {"numberOfResults": MAX_RESULTS}
        },
    )
    elapsed = time.perf_counter() - t0

    contexts = []
    for r in resp.get("retrievalResults", []):
        contexts.append(
            {
                "text": r.get("content", {}).get("text", ""),
                "score": r.get("score"),
                "location": r.get("location", {}),
            }
        )
    return {
        "contexts": contexts,
        "latency_s": round(elapsed, 3),
        "iterations": 1,
        "planning_actions": 0,
        "generated_answer": None,
        "trace": [],
    }


# --------------------------------------------------------------------------
# Configuraciones C y D: AgenticRetrieveStream
# --------------------------------------------------------------------------
def build_agentic_config(planner_arn=None):
    """
    planner_arn explicito (usado por config E) gana sobre PLANNER_MODEL_ARN
    (env var que solo controlan C/D). Si ninguno esta definido, usamos el
    modelo gestionado por el servicio, que es lo que obtiene un usuario por
    defecto.

    foundationModelType admite CUSTOM | MANAGED (default MANAGED). Confirmado
    contra API_agent-runtime_AgenticRetrieveConfiguration.html y contra el
    shape real de botocore >= 1.43.32 (00-preflight.sh seccion 4). Se fija
    explicito en vez de depender del default por reproducibilidad.
    """
    cfg: dict = {"maxAgentIteration": MAX_ITER}
    arn = planner_arn if planner_arn is not None else PLANNER_ARN
    if arn:
        cfg["foundationModelType"] = "CUSTOM"
        cfg["foundationModelConfiguration"] = {
            "type": "BEDROCK_FOUNDATION_MODEL",
            "bedrockFoundationModelConfiguration": {
                "modelConfiguration": {"modelArn": arn}
            },
        }
    else:
        cfg["foundationModelType"] = "MANAGED"
    return cfg


def run_agentic(kb_id, query, generate_response, planner_arn=None):
    """
    Consume el stream completo. Los eventos llegan en orden:
    traceEvent (SpeculativeRetrieval / Planning / Retrieval o
    FullDocumentExpansion), responseEvent (si generate_response), y result.

    Los scores de relevancia en agentic retrieval SOLO viven en los trace
    events, no en el resultado final: por eso guardamos el trace entero.
    """
    t0 = time.perf_counter()
    resp = runtime.agentic_retrieve_stream(
        messages=[{"role": "user", "content": {"text": query}}],
        retrievers=[
            {
                "configuration": {
                    "knowledgeBase": {
                        "knowledgeBaseId": kb_id,
                        "retrievalOverrides": {"maxNumberOfResults": MAX_RESULTS},
                    }
                }
            }
        ],
        agenticRetrieveConfiguration=build_agentic_config(planner_arn),
        generateResponse=generate_response,
    )

    trace, answer_parts, contexts = [], [], []
    planning_steps = 0
    planning_actions = 0

    for event in resp["stream"]:
        if "traceEvent" in event:
            attrs = event["traceEvent"]["attributes"]
            step = attrs.get("step")
            trace.append(
                {
                    "step": step,
                    "status": attrs.get("status"),
                    # sub-queries: lo que el planner realmente busco.
                    # Esto es lo mas interesante del articulo.
                    "raw": attrs,
                }
            )
            if step == "Planning":
                planning_steps += 1
                # GOTCHA CONFIRMADO 2026-09-01: un step Planning SUCCEEDED
                # puede traer "actions": [] -- el planner decidio NO generar
                # sub-queries adicionales y quedarse con el SpeculativeRetrieval
                # inicial. "iterations" (planning_steps) cuenta el step,
                # aunque no haya aportado nada; planning_actions cuenta las
                # sub-queries reales, que es la senal que de verdad importa
                # para juzgar si el planner "trabajo" en la pregunta.
                if attrs.get("status") == "SUCCEEDED":
                    planning_actions += len(attrs.get("actions") or [])

        elif "responseEvent" in event:
            answer_parts.append(event["responseEvent"]["text"])

        elif "result" in event:
            for chunk in event["result"].get("results", []):
                contexts.append(
                    {
                        "text": chunk.get("content", {}).get("text", ""),
                        "score": None,  # solo disponible en trace events
                        "source_retriever": chunk.get("sourceRetriever", {}).get(
                            "identifier", ""
                        ),
                        "location": chunk.get("location", {}),
                    }
                )

    elapsed = time.perf_counter() - t0
    return {
        "contexts": contexts,
        "latency_s": round(elapsed, 3),
        "iterations": planning_steps,
        "planning_actions": planning_actions,
        "generated_answer": "".join(answer_parts) or None,
        "trace": trace,
    }


# --------------------------------------------------------------------------
# E reusa el modelo del generador como planner CUSTOM -- ver docstring del
# modulo para el porque (Nova Premier descartado por LEGACY/EOL 2026-09-14).
SONNET_PLANNER_ARN = os.environ.get("GENERATOR_MODEL", "global.anthropic.claude-sonnet-4-6")

CONFIGS = {
    "A": dict(kb_env="KB_FIXED_ID", fn=lambda kb, q: run_retrieve(kb, q, managed=False)),
    "B": dict(kb_env="KB_MANAGED_ID", fn=lambda kb, q: run_retrieve(kb, q, managed=True)),
    "C": dict(kb_env="KB_MANAGED_ID", fn=lambda kb, q: run_agentic(kb, q, False)),
    "D": dict(kb_env="KB_MANAGED_ID", fn=lambda kb, q: run_agentic(kb, q, True)),
    "E": dict(kb_env="KB_MANAGED_ID",
              fn=lambda kb, q: run_agentic(kb, q, False, planner_arn=SONNET_PLANNER_ARN)),
}


def run(config_id, set_id):
    spec = CONFIGS[config_id]
    kb_id = os.environ.get(spec["kb_env"], "").strip()
    if not kb_id or kb_id == "REEMPLAZAR":
        raise SystemExit(f"{spec['kb_env']} sin definir para la config {config_id}")

    questions = load_questions(set_id)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"retrieval-{config_id}-set{set_id}.jsonl"

    print(f"[{config_id}/set{set_id}] {len(questions)} preguntas contra KB {kb_id}")
    with out.open("w", encoding="utf-8") as fh:
        for i, q in enumerate(questions, 1):
            try:
                r = spec["fn"](kb_id, q["question"])
                status = "ok"
                err = None
            except Exception as exc:  # el error crudo es material del articulo
                r = {"contexts": [], "latency_s": None, "iterations": None,
                     "generated_answer": None, "trace": []}
                status, err = "error", f"{type(exc).__name__}: {exc}"
                print(f"    ! q{i}: {err}")

            fh.write(json.dumps({
                "question_id": q["id"],
                "question": q["question"],
                "ground_truth": q.get("ground_truth"),
                "hops": q.get("hops"),          # 1 en set A; 2-4 en set B
                "config": config_id,
                "set": set_id,
                "status": status,
                "error": err,
                **r,
            }, ensure_ascii=False) + "\n")

            if status == "ok":
                print(f"    q{i}/{len(questions)} "
                      f"{r['latency_s']}s  iter={r['iterations']}  "
                      f"chunks={len(r['contexts'])}")
            time.sleep(0.5)  # cortesia con las cuotas

    print(f"[{config_id}/set{set_id}] -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=list(CONFIGS))
    ap.add_argument("--set", dest="set_id", choices=list(QUESTION_SETS))
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        for c in CONFIGS:
            for s in QUESTION_SETS:
                run(c, s)
    elif args.config and args.set_id:
        run(args.config, args.set_id)
    else:
        ap.error("usa --all o (--config y --set)")
