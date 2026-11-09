"""recall: local hybrid index of the user's technical books.

Components: indexer (FTS5 + embeddinggemma in SQLite), searcher (vector
first, RRF with lower-weighted FTS5), read-only HTTP service on loopback,
stdio MCP server and, separately, the ``recall_hook.py`` hook (stdlib).

Setting ``HF_HUB_OFFLINE`` here, before any third-party import, is
deliberate: nothing in this package may touch the network. The model is loaded
from the local snapshot path and ``huggingface-hub`` is not even installed (see
``requirements.excludes``); the variable is a second barrier.
"""

import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

__version__ = "0.3.0"
