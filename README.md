# recall

Recuperación automática y local de los libros técnicos de Andrés para Claude,
en español e inglés. Decisión del 2026-10-27 (opción 2): **MemPalace se queda
para conversaciones y decisiones; los libros pasan a este índice propio.**

Todo es local: el modelo se carga de la caché de HF en disco, `HF_HUB_OFFLINE=1`
se fija al importar el paquete y `huggingface-hub` ni siquiera se instala.
El servicio escucha solo en 127.0.0.1 con token. Instalación en Claude:
[INSTALL.md](INSTALL.md) (no aplicada).

Desde 2026-11-03 vive en su propio repo (antes `mempalace-ops\recall`, historial
conservado) y no depende de MemPalace en nada.

## Qué hay (resumen)

| Pieza | Qué hace |
|---|---|
| `recall index` | Trocea los `.md` y guarda texto + vector en SQLite (incremental) |
| `recall serve` | Servicio local con el modelo cargado (127.0.0.1, token) |
| `recall_hook.py` | Antes de cada prompt de Claude Code busca e inyecta ≤3 fragmentos (sombra por defecto) |
| `recall mcp` | Herramienta `recall_books_search` para Code y Desktop, con filtros por tema y año |
| `data/books_meta.json` | Año (de la página de copyright), edición, formato y temas de cada libro |
| `tools/build_pages.py` | Mapa línea → página del PDF para citar "p. 199" |
| `experiments/` | Indexa con otro modelo o troceo sin tocar el índice de producción, y compara |

## Arquitectura

```
 Books TI\_md\<libro>\NN-capitulo.md            (roots en orden de precedencia;
              │                                   _md_v2 delante reemplaza por libro)
              ▼
 recall index ── chunking por sección (~700–1000 car., solape ~150, libro/cap/sección/líneas)
              │  embeddinggemma-300m q8 ONNX (DirectML)  "title: libro — capítulo | text: …"
              ▼
 %LOCALAPPDATA%\recall\index.sqlite3   (WAL)
   files(path, sha256, size, mtime)  chunks(texto, procedencia, idioma, vec BLOB f32×768)
   fts (FTS5 contenido externo, triggers)   meta(model_identity, generation)
              │ solo lectura (mode=ro)
              ▼
 recall serve ── 1 proceso, modelo caliente (DirectML, cae a CPU), 127.0.0.1:<efímero>, Bearer token
   │  GET /health   POST /search {queries[], k, collections}
   │                vector primero → RRF con FTS5 (0.25 mismo idioma / 0.02 cruce)
   │                → dedup (archivo, sección) → cos para umbral
   ├──────────── recall_hook.py (UserPromptSubmit, stdlib, python -I)
   │               sombra por defecto: shadow.log (hash, cos, rutas); activo: ≤3×700 car.
   └──────────── recall mcp (stdio; Claude Code y Desktop)
                   recall_books_search(queries[ES, EN], k)  ·  instructions ≤ 4096
                   si no hay servicio, carga un buscador en proceso
 start-recall.ps1 (SessionStart): arranca `serve` desacoplado si no está sano; siempre exit 0
```

## Decisiones y por qué

**Índice propio en vez de MemPalace para libros.** El benchmark de mpeval midió,
sobre las mismas 20 consultas de libros: vector con prefijos de recuperación
R@5 = 1.00 también en el cruce ES→EN, frente a grep agéntico monolingüe 0.00 en
el cruce. Las búsquedas de MemPalace mezclan libros con conversaciones y código.

**Vector primero, FTS5 casi apagado en el cruce.** En el prototipo FTS5 solo
dio R@5 = 0.40 en ES→EN y el híbrido con peso igual bajó el cruce de 1.00 a 0.90.
FTS5 queda con peso 0.25 cuando consulta y chunk comparten idioma (ayuda con
siglas y términos literales) y 0.02 cuando no.

**Prefijos de embeddinggemma.** Consulta `task: search result | query: …`,
documento `title: <libro — capítulo> | text: …`. Cambiarlos cambia la identidad
del modelo y obliga a `--rebuild`.

**768 dimensiones.** Prototipo: 768d MRR 1.00 / cruce 1.00; 384d MRR 0.93 /
cruce 0.85. RAM del servicio ~0.35 GB de vectores para ~112 k chunks.

**Vectores como BLOB en SQLite, no sqlite-vec ni memmap.** sqlite-vec no está en
la caché local de uv (instalarlo exigía red) y su versión estable también es
exhaustiva; un memmap aparte no comparte transacción con los metadatos y hay
que compactarlo en cada baja. Como BLOB, alta/cambio/baja de un archivo es una
transacción y un indexado interrumpido no deja nada desalineado. El buscador
carga la matriz en RAM y el producto punto cuesta decenas de ms; lo caro es
embeber la consulta.

**Identidad del modelo.** El índice guarda repo, revisión del snapshot, SHA-256
de `model_quantized.onnx`, `.onnx_data` y `tokenizer.json`, dimensión y prefijos.
Indexar o buscar con otra identidad falla con `ModelMismatchError`; un índice con
chunks pero sin identidad también (asumir el modelo ya costó caro en otro
índice de esta máquina).

**Incremental de verdad.** Tamaño+mtime iguales → no se lee; si cambian, SHA-256;
solo si cambia el contenido se re-trocea. Cada chunk reutiliza el vector de
cualquier chunk previo con el mismo texto de entrada, así que reconvertir un
libro o moverlo de `_md` a `_md_v2` re-embebe solo lo que cambió. Las bajas se
aplican al final para poder reutilizar sus vectores. Commit por grupos de ~512
chunks: interrumpir pierde como mucho un grupo.

**Libros reconvertidos (`Books TI\_md_v2`).** Cuando estén listos, pon la raíz
nueva delante en `%LOCALAPPDATA%\recall\config.json`:

```json
{"collections": {"books": {"roots": [
  "C:\\Users\\<usuario>\\Downloads\\Books TI\\_md_v2",
  "C:\\Users\\<usuario>\\Downloads\\Books TI\\_md"]}}}
```

y corre `recall index`: cada libro presente en `_md_v2` sustituye al de `_md`
(bajas + altas, con reutilización de vectores). No lo he puesto por defecto: un
libro a medio convertir en `_md_v2` ocultaría al completo de `_md`. Si en
cambio se sobrescriben los archivos dentro de `_md`, el hash lo detecta solo.

**Front matter omitido con nombres anclados.** El prototipo filtraba por
subcadena y se comía capítulos reales: "disaster-**recover**y" contiene
"cover", más "service-discovery" y "anatomy-of-an-index". Ahora se compara el
nombre completo (test `test_skip_rules_are_anchored`).

**DirectML para indexar y para consultar.** Medido el 2026-10-27 con chunks
reales (~200 tokens): CPU 1.3–2.8 chunks/s; DirectML 3.6–5.8 chunks/s con la
GPU compartida con un `mempalace mine` del otro agente, y ~20 chunks/s en lote
32–64 con la GPU libre. El adaptador se elige con `--dml-device` (aquí 1 rindió
más que 0; hay Iris Xe y RTX 3070 Ti). Una consulta costó 2.2 s (p50) en CPU con la máquina cargada y 81 ms en
DirectML; el hook solo tiene 1.5 s, así que el servicio también usa DirectML
(si no arranca, cae a CPU). Vectores DML y CPU coinciden (coseno ≥ 0.9999999,
test `test_dml_and_cpu_vectors_agree`), así que mezclar proveedores no cambia
la identidad.

**MCP sin SDK.** El SDK oficial arrastra pydantic, httpx, starlette, anyio… para
cuatro métodos. El subconjunto (initialize, tools/list, tools/call, ping) está
implementado a mano y cubierto por tests; menos superficie de cadena de
suministro en un proceso que lee datos locales.

**Hook en modo sombra, fail-open, ASCII.** Ver docstring de `recall_hook.py`.
La salida va en bytes ASCII porque en Windows stdout puede ser cp1252 y el
prototipo perdía la inyección en silencio. El log de sombra guarda el hash del
prompt, nunca su texto.

**Datos de clientes.** El hook y el MCP solo consultan la colección `books`, que
no contiene datos de clientes; no hay nada que pueda cruzar de un proyecto a
otro. Si algún día se indexan repos de cliente, cada uno irá en su colección y
el hook filtrará por `cwd` antes de añadirlas.

**Filtros por tema y año (2026-11-03).** `data/books_meta.json` trae para los
126 libros el año de **esa edición** sacado de la página de copyright (115) o de
metadatos confirmados por otra fuente (4); 7 quedan en `null` porque no hubo
evidencia, y no se adivinan. Los temas (1–3 por libro) son de una taxonomía
cerrada (`metadata.DOMAINS`). Un libro sin año no pasa un filtro de año: "desde
2020" no se puede afirmar de él. Sin filtros nada cambia.

**Página en la cita (2026-11-03).** `tools/build_pages.py` busca el texto
normalizado de cada párrafo en el PDF original (cursor que avanza por el libro;
si no aparece cerca, en todo el libro; si no, hereda la página anterior y se
marca aproximada, `p. ~N`). 88 PDF, 96.1 % de párrafos exactos. Se cita la
numeración impresa cuando el PDF la define (`page`), y se guarda también la del
archivo (`pdf_page`). Va en `pages.sqlite3`, aparte del índice: vale para
cualquier troceo o modelo y es opcional. Los EPUB (38) no tienen páginas fijas.
Regenerar tras reconvertir libros:

```powershell
& 'C:\Users\<usuario>\Downloads\Books TI\_tools\.venv\Scripts\python.exe' -I tools\build_pages.py
```

**Modelo y troceo configurables (2026-11-03).** `config.json` puede cambiar
`model` (nombre, snapshot, prefijos, pooling `sentence_embedding`/`mean`/`last`,
relleno izquierdo, EOS) y `chunking`. Ambos entran en la identidad del índice,
así que cambiarlos exige `--rebuild`. Con los valores por defecto la identidad
es la de siempre (verificado: mismos vectores, coseno 1.000000). Para comparar
sin tocar producción: `experiments\run.ps1` indexa cada `experiments\configs\*.json`
en `%LOCALAPPDATA%\recall-exp\<nombre>` y `experiments\evaluate.py` los compara.

**Dependencias.** `requirements.lock` (uv pip compile, con hashes, solo
win/amd64) generado e instalado sin red desde la caché de uv. CPython 3.12.13 de
uv. `huggingface-hub` excluido a propósito (`requirements.excludes`).

## Uso

```powershell
$py = 'C:\Users\<usuario>\source\repos\recall\.venv\Scripts\python.exe'
& $py -I -m recall index [--rebuild] [--dml-device 1] [--cpu]
& $py -I -m recall search "tasa de consumo del presupuesto de errores" "error budget burn rate" --text
& $py -I -m recall status
& $py -I -m recall eval [--queries tests\data\queries_books_80.json]
& $py -I experiments\evaluate.py   # produccion vs experimentos
& $py -I -m pytest            # unitarios (sin modelo)
& $py -I -m pytest -m model   # regresión con el índice y el modelo reales
```

## Métricas (2026-10-27, índice completo)

**Indexado.** 126 libros, 3133 capítulos (155 archivos de front matter
omitidos), 111 648 chunks (media ~780 car.), índice de 0.64 GB. Primer
indexado: 23 267 s = **6 h 28 min** a 4.8 chunks/s en DirectML, con la GPU
compartida todo el rato con un `mempalace mine` de otro agente (con la GPU
libre se midieron ~20 chunks/s → ~1.5 h estimada). Viable sin cambiar de
modelo: es una vez, reanudable, y luego incremental. Pasada sin cambios:
0.18 s de trabajo (7 s con arranque del proceso y del modelo).

**Incremental real.** Mientras tanto la reconversión de libros reescribió
capítulos dentro de `_md` (p. ej. `clean-architecture-martin`). El siguiente
`recall index` detectó 97 altas, 135 cambios y 46 bajas; embebió 8533 chunks y
reutilizó 2666 por texto idéntico, en 21 min. Índice resultante: 3184
archivos, 111 407 chunks. Métricas idénticas tras el cambio.

**Recuperación** (`recall eval`, 20 consultas de libros de mpeval):

| Grupo | n | R@5 | MRR@10 |
|---|---|---|---|
| TOTAL | 20 | 0.95 | 0.86 |
| EN→EN | 10 | 1.00 | 0.93 |
| ES→EN (cruce) | 10 | 0.90 | 0.78 |

Más bajo que el prototipo (R@5 1.00) porque el prototipo solo tenía 24 libros.
Con 126 aparecen capítulos pertinentes de otros libros que las etiquetas de
mpeval no cuentan: el único fallo (B04es, *"qué es un contexto delimitado en
DDD"*) devuelve primero el capítulo de DDD de *Architectural Patterns* (Raj);
los MRR de 0.33 (B05, B07) ponen arriba *Microservices Patterns* de Richardson
sobre mensajería y eventos. No he relajado las etiquetas: los pisos de
`tests/test_regression.py` son lo medido.

**FTS5.** Barrido sobre el índice completo: pesos 0 / 0.25 / 0.5 dan las mismas
métricas (el vector domina); peso 1 en ambos idiomas baja el cruce a R@5 0.80.
Se queda en 0.25 / 0.02.

**Umbral del hook.** Con 126 libros un umbral de coseno solo no separa:
*"crea un archivo .gitignore…"* (0.618), *"haz commit…"* (0.608) y *"dame el diff…"*
(0.600) casan con *Pro Git* por encima de consultas legítimas (mín. 0.524). Por
eso el hook salta antes las peticiones mecánicas (git, archivos, edición;
`MECHANICAL` en `recall_hook.py`): de 28 negativos salta 20, y los que quedan
tienen ≤ 0.535. Con umbral **0.58** pasan 24 de 30 positivos (20 consultas +
10 prompts realistas) y ningún negativo. Ojo: filtro y umbral se calibraron
con estos mismos prompts; el modo sombra existe para validarlos con prompts
reales antes de activar.

**Latencia.** Servicio caliente: búsqueda p50 0.29 s; hook completo (arranque
de `python -I` + HTTP + búsqueda) 0.6–0.7 s; MCP con consulta ES+EN 0.96 s.

## Métricas con 80 consultas (2026-11-03)

20 originales + 60 nuevas sobre 30 temas (arquitectura, datos, SRE, seguridad,
cloud, testing, IA, agile, requisitos…), en `tests/data/queries_books_80.json`.
Las nuevas se etiquetaron leyendo los libros (grep, encabezados), sin ningún
buscador, para no sesgar el set. Incluyen siglas (ADR, BFF, mTLS, SBOM, CSRF,
DORA…) y 5 conceptuales que no nombran el término. Se descartaron temas que
aparecen en más de 8 capítulos (CQRS, sagas, strangler fig…) por genéricos.

| Modo | R@5 | EN→EN | ES→EN | MRR |
|---|---|---|---|---|
| hook (una consulta tal cual) | 0.97 | 0.97 | 0.97 | 0.88 |
| MCP (ES + EN en la misma llamada) | 0.97 | — | — | 0.92 |

Fallos: B04es (contexto delimitado, ver arriba) y C05en (módulos profundos,
conceptual). Con el umbral 0.58 pasa el 82 % de los positivos; el 94 % queda
por encima del negativo más alto (0.535). Pisos en `tests/test_regression_80.py`.

## Experimentos de troceo y modelo (2026-11-05)

`experiments/run.ps1` + `experiments/evaluate.py`, mismas 80 consultas.
"Separación" = parte de los positivos por encima del negativo más alto (lo que
permite un umbral automático limpio en el hook).

| Índice | Chunks | R@5 | ES→EN | MRR | MCP R@5 | Separación |
|---|---|---|---|---|---|---|
| producción (gemma-300m, ~900 car.) | 111 407 | 0.97 | 0.97 | 0.88 | 0.97 | 0.94 |
| E1 gemma-300m, ~2000 car. | 55 074 | 0.99 | 0.97 | 0.90 | 1.00 | 0.80 |
| E2 gemma-300m, ~3500 car. | 34 721 | 1.00 | 1.00 | 0.91 | 1.00 | 0.91 |
| E3 embeddinggemma-2, ~900 car. | 111 407 | 0.97 | 0.97 | 0.88 | 1.00 | 1.00 (negativo máx 0.71, positivo mín 0.73) |

**Qwen3-Embedding-0.6B (E4) descartado en esta máquina.** El ONNX cuantizado de
onnx-community no es invariante al relleno: a la izquierda cambia el vector
(coseno 0.93 con 1 token de relleno, en CPU y GPU); a la derecha queda exacto
en CPU pero no en DirectML (0.95). En GPU cada vector depende de con quién cae
en el lote; en CPU tarda ~46 h (0.7 chunks/s). Además colgó la GPU con lotes de
16 (DXGI 887A0006). Lo detectó `tools/verify_index.py --sample`.

**Producción desde el 2026-11-05: E5** (embeddinggemma-2, trozos de ~3500).
Va en `%LOCALAPPDATA%\recall\config.json` (copia de
`experiments/configs/e5-gemma2-chunk3500.json` más `"hook": {"mode": "shadow",
"threshold": 0.75}`); los `DEFAULTS` del código siguen en gemma-300m. El índice
anterior quedó en `%LOCALAPPDATA%\recall-backup\gemma300m-900-20261105` (con su
`shadow.log`): para volver, se restaura y se borra `config.json`. Con trozos
grandes el hook pide `snippet_chars` y el servicio devuelve la ventana del
trozo que más términos comparte con la pregunta (re-embeber ventanas costaba
~2 s por resultado en DirectML). Medido en producción: 80 consultas R@5 0.988,
MRR 0.898; umbral 0.75 → 96.7 % de positivos, 0 de 28 negativos. Paper con todo
el estudio: [`docs/paper.md`](docs/paper.md).

**Integridad.** `tools/verify_index.py [HOME…] --sample 30` revisa en solo
lectura: `quick_check`, contabilidad archivos↔chunks, vectores (largo, finitos,
norma 1), texto = entrada embebida (sha256), FTS, cobertura de la biblioteca y
re-embebido de una muestra. Producción, E1, E2 y E3 pasaron todo (2026-11-05).
