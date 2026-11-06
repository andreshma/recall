"""recall: indice hibrido local de los libros tecnicos del usuario.

Componentes: indexador (FTS5 + embeddinggemma en SQLite), buscador (vector
primero, RRF con FTS5 de peso menor), servicio HTTP de solo lectura en
loopback, servidor MCP stdio y, aparte, el hook ``recall_hook.py`` (stdlib).

Fijar ``HF_HUB_OFFLINE`` aqui, antes de cualquier import de terceros, es
deliberado: nada en este paquete debe tocar la red. El modelo se carga de la
ruta local del snapshot y ``huggingface-hub`` ni siquiera se instala (ver
``requirements.excludes``); la variable es una segunda barrera.
"""
import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

__version__ = "0.2.0"
