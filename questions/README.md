# Los dos sets de preguntas

## `set-a-singlehop.jsonl` — las 25 de abril

Copiar **sin modificar** desde
`github.com/codecr/bedrock-chunking-benchmark`. Cualquier cambio rompe la
comparabilidad, que es el único motivo por el que este set existe.

Formato:

```json
{"id": "a01", "question": "...", "ground_truth": "...", "hops": 1}
```

## `set-b-multihop.jsonl` — nuevas, sobre el mismo corpus

~15 preguntas de 2 a 4 saltos sobre los mismos 3 documentos. Este set existe
porque AWS reporta que agentic retrieval gana menos de 5 puntos en preguntas de
un salto: correr solo el Set A produciría un "no aportó nada" que estaría
predicho de antemano y sería un experimento mal diseñado.

Reglas para escribirlas:

1. **Multi-intent o comparativas.** La pregunta debe requerir evidencia de al
   menos dos lugares distintos del corpus.
2. **Cruzar documentos cuando se pueda.** El Well-Architected Framework y el
   developer guide de AgentCore hablan de temas solapados desde ángulos
   distintos: ahí vive el multi-hop natural.
3. **Ground truth verificable a mano.** Si no puedes señalar los pasajes
   exactos que la sustentan, la pregunta no sirve como referencia.
4. **Anotar `hops` honestamente.** Es la cadena mínima de documentos necesaria,
   no una estimación optimista. Permite reportar resultados desglosados por
   dificultad, que es donde AWS dice que están las ganancias.
5. **Sin preguntas trampa.** El objetivo es medir, no ganarle al servicio.

Ejemplos de forma (redactar las reales contra el corpus):

```json
{"id": "b01", "hops": 2, "question": "¿Qué pilar del Well-Architected Framework cubre el control de acceso a herramientas, y cómo lo implementa concretamente AgentCore?", "ground_truth": "..."}
{"id": "b02", "hops": 3, "question": "Compara cómo el Well-Architected Framework y el developer guide de AgentCore tratan la persistencia de estado. ¿Dónde difieren las recomendaciones?", "ground_truth": "..."}
{"id": "b03", "hops": 2, "question": "¿Cuáles son los tres límites de servicio que más restringen un agente de larga duración, y cuál aplica primero?", "ground_truth": "..."}
```

## Contra-verificación antes de ejecutar

Corre el Set B una vez contra la configuración **B** (`Retrieve` simple). Si
`Retrieve` ya las contesta bien, no son realmente multi-hop y hay que
reescribirlas. Un set B que el retrieval de un paso resuelve sin problema
invalida H3 antes de empezar.
