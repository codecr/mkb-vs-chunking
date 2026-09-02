# mkb-vs-chunking

Código y datos crudos del benchmark que compara Amazon Bedrock Managed
Knowledge Base (Smart Parsing + retrieval gestionado, incluyendo
`AgenticRetrieveStream`) contra la línea base de abril 2026 (S3 Vectors +
chunking `FIXED_SIZE` manual). Continuación de
[github.com/codecr/bedrock-chunking-benchmark](https://github.com/codecr/bedrock-chunking-benchmark).

El análisis, las hipótesis y la discusión de resultados están en el
artículo publicado, no en este repo. Esto es el pipeline reproducible y
los datos crudos de la corrida (`results/`, `results-run2/`) para quien
quiera verificar los números o re-correr el benchmark.

## Qué se compara

| Config | Retrieval | Generación |
|---|---|---|
| A | S3 Vectors + `FIXED_SIZE` (abril) | Sonnet 4.6 |
| B | Managed KB, `Retrieve` | Sonnet 4.6 |
| C | Managed KB, `AgenticRetrieveStream` (planner MANAGED) | Sonnet 4.6 |
| D | Managed KB, `AgenticRetrieveStream` (planner MANAGED, genera el servicio) | servicio |
| E | Managed KB, `AgenticRetrieveStream` (planner CUSTOM = Sonnet 4.6) | Sonnet 4.6 |

Juez: Nova Pro (cross-family respecto al generador), sobre las 4 métricas
builtin de Bedrock Evaluations (`Correctness`, `Completeness`,
`Faithfulness`, `Helpfulness`).

## Prerequisitos

- Cuenta AWS con acceso a Bedrock (Managed Knowledge Base, Agentic
  Retrieve, Bedrock Evaluations) en `us-east-1`.
- Python 3.10+ y `boto3`/`botocore` **>= 1.43.32 exacto** -- versión mínima
  en la que `AgenticRetrieveStream` y las Managed Knowledge Bases se
  lanzaron juntas. Versiones anteriores no exponen la operación.
- Terraform >= 1.5.
- AWS CLI configurado (`aws sts get-caller-identity` debe funcionar).

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Costo

Esto crea recursos facturables reales: una Managed Knowledge Base, un
vector store S3 Vectors, llamadas a Sonnet 4.6 y Nova Pro, y eval jobs de
Bedrock Evaluations. Orden de magnitud esperado: **USD 20-40** para correr
el pipeline completo una vez. Revisá `01-terraform-apply.sh` (pide
confirmación explícita antes de crear nada) y corré `09-cost-report.py`
para el número real.

## Orden de ejecución

```bash
source config.sh
./00-preflight.sh            # verifica supuestos antes de gastar
./01-terraform-apply.sh      # buckets, roles IAM, KB config A
python3 02-create-managed-kb.py
python3 03-ingest-and-wait.py
python3 04-run-retrieval.py --all
python3 05-generate-answers.py
python3 06-build-eval-datasets.py
python3 07-launch-eval-jobs.py
python3 08-collect-results.py
python3 09-cost-report.py
./99-teardown.sh             # cuando termines -- borra TODO lo anterior
```

Cada script imprime su propio `--help` con las banderas `--config`/`--set`
para correr una sola combinación en vez de `--all`. `config E` (planner
CUSTOM = Sonnet 4.6) no forma parte de `--all`: se corre puntual con
`--config E`, para no reprocesar A-D.

Para repetir una corrida sin pisar la anterior, apuntá `RESULTS_DIR` a otra
carpeta antes de correr los mismos comandos -- todos los scripts lo
respetan, y `08-collect-results.py` aísla los resultados por `job_id`
aunque compartan el mismo prefijo de S3.

## Estructura

- `00`–`09` -- pipeline numerado, en orden de ejecución.
- `99-teardown.sh` -- destruye toda la infraestructura creada.
- `config.sh` -- variables compartidas (modelos, buckets, límites).
- `terraform/` -- infraestructura de la config A (bucket de corpus, bucket
  de eval, S3 Vectors, roles IAM). La Managed KB (config B/C/D/E) se crea
  fuera de terraform, vía `02-create-managed-kb.py`.
- `questions/` -- los dos sets de preguntas (`set-a-singlehop.jsonl`,
  25 preguntas de un salto; `set-b-multihop.jsonl`, 15 multi-hop) y las
  reglas con las que se escribieron.
- `corpus-manifest.json` -- URLs y hashes SHA256 de los 3 documentos del
  corpus. El corpus no se versiona aquí: se descarga de esas URLs.
- `results/`, `results-run2/` -- salida cruda del pipeline (retrieval,
  respuestas generadas, datasets de evaluación, scores agregados) tal como
  quedó en la corrida real. `results-run2/` es una segunda corrida
  independiente de C-setB/E-setB para chequear reproducibilidad.

## Licencia

_(pendiente -- agregar la que corresponda)_
