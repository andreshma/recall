"""Esquema SQLite del indice y guardia de identidad del modelo.

Un solo archivo SQLite en modo WAL guarda todo: archivos indexados (con hash),
chunks con su procedencia, el vector de cada chunk como BLOB float32 y una
tabla FTS5 de contenido externo sincronizada por triggers.

Por que vectores como BLOB y no sqlite-vec ni un memmap aparte (evaluado en el
prototipo y aqui):

* sqlite-vec (0.1.x) no esta en la cache local de uv: instalarlo exigia red,
  y en su version estable tambien es busqueda exhaustiva, igual que numpy.
* Un memmap aparte hay que compactarlo en cada baja y no comparte
  transaccion con los metadatos: un indexado interrumpido podia dejarlos
  desalineados. Como BLOB, alta/cambio/baja de un archivo es UNA transaccion.
* El buscador carga la matriz en RAM al arrancar (N x 768 float32; ~0.3 GB
  para ~100k chunks) y el producto punto exhaustivo cuesta decenas de ms; el
  coste dominante de una consulta es embeber la consulta, no buscar.

``meta.generation`` sube en cada commit de escritura; el servicio la consulta
antes de cada busqueda y recarga si cambio, asi un ``recall index`` se ve sin
reiniciar nada.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .embedder import identity_key

SCHEMA_VERSION = "1"

SCHEMA = """
create table if not exists meta(key text primary key, value text not null);
create table if not exists files(
    path text primary key,
    collection text not null,
    book_id text not null,
    sha256 text not null,
    size integer not null,
    mtime_ns integer not null,
    n_chunks integer not null,
    indexed_at text not null
);
create table if not exists chunks(
    id integer primary key,
    path text not null,
    collection text not null,
    book_id text not null,
    book text not null,
    chapter text not null,
    section text not null,
    line_start integer not null,
    line_end integer not null,
    lang text not null,
    text text not null,
    input_sha text not null,
    vec blob not null
);
create index if not exists chunks_path on chunks(path);
create index if not exists chunks_input_sha on chunks(input_sha);
create virtual table if not exists fts using fts5(
    book, chapter, section, text,
    content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
create trigger if not exists chunks_ai after insert on chunks begin
    insert into fts(rowid, book, chapter, section, text)
    values (new.id, new.book, new.chapter, new.section, new.text);
end;
create trigger if not exists chunks_ad after delete on chunks begin
    insert into fts(fts, rowid, book, chapter, section, text)
    values ('delete', old.id, old.book, old.chapter, old.section, old.text);
end;
"""


class ModelMismatchError(RuntimeError):
    """El indice tiene vectores de otro modelo (o de identidad desconocida)."""


def open_rw(path: Path) -> sqlite3.Connection:
    """Conexion de escritura (crea el esquema si falta)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.executescript(SCHEMA)
    con.execute("insert or ignore into meta values('schema_version', ?)", (SCHEMA_VERSION,))
    con.execute("insert or ignore into meta values('generation', '0')")
    con.commit()
    return con


def open_ro(path: Path) -> sqlite3.Connection:
    """Conexion de SOLO LECTURA (``mode=ro``): el servicio nunca escribe."""
    if not path.is_file():
        raise FileNotFoundError(f"No hay indice en {path}; corre `recall index` primero.")
    uri = "file:" + path.as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    """Valor de ``meta[key]`` o None."""
    r = con.execute("select value from meta where key=?", (key,)).fetchone()
    return r[0] if r else None


def generation(con: sqlite3.Connection) -> int:
    """Contador de escrituras confirmadas."""
    return int(get_meta(con, "generation") or 0)


def check_identity(con: sqlite3.Connection, identity: dict, *, writing: bool) -> None:
    """Falla si el indice tiene vectores de un modelo distinto a ``identity``.

    Al escribir en un indice vacio, registra la identidad. Un indice con chunks
    pero sin identidad registrada tambien falla: "asumir" el modelo actual es
    justo el error silencioso que ya costo caro en otro indice de esta maquina.
    """
    stored = get_meta(con, "model_identity")
    n = con.execute("select count(*) from chunks").fetchone()[0]
    if stored is None:
        if n:
            raise ModelMismatchError(
                "El indice tiene chunks pero ninguna identidad de modelo registrada; "
                "reconstruyelo con `recall index --rebuild`."
            )
        if writing:
            con.execute("insert or replace into meta values('model_identity', ?)", (identity_key(identity),))
            con.commit()
        return
    if stored != identity_key(identity):
        a, b = json.loads(stored), identity
        if _legacy_upgrade(a, b):
            # Indice anterior a que ``doc_max_tokens`` entrara en la identidad: se
            # asume construido con el valor configurado (el que se uso para indexarlo)
            # y la proxima escritura lo registra; desde entonces, cambiarlo falla.
            if writing:
                con.execute("insert or replace into meta values('model_identity', ?)", (identity_key(b),))
                con.commit()
            return
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        raise ModelMismatchError(
            f"El indice se construyo con otro modelo (difiere: {', '.join(diff)}). "
            "No se mezclan vectores de modelos distintos: usa `recall index --rebuild`."
        )


#: Campos agregados a la identidad despues de construir indices en uso. Un indice
#: que no los tiene se acepta (y se actualiza al escribir) si todo lo demas coincide.
LEGACY_OPTIONAL = ("doc_max_tokens",)


def _legacy_upgrade(stored: dict, current: dict) -> bool:
    """True si ``stored`` solo difiere de ``current`` en campos nuevos que no tenia."""
    missing = [k for k in LEGACY_OPTIONAL if k in current and k not in stored]
    if not missing:
        return False
    return stored == {k: v for k, v in current.items() if k not in missing}


def bump_generation(con: sqlite3.Connection) -> None:
    """Incrementa ``meta.generation`` (dentro de la transaccion en curso)."""
    con.execute("update meta set value = cast(value as integer) + 1 where key='generation'")
