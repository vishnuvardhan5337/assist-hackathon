"""Local memory: one SQLite file. Each command is a new process, so everything we
need between calls lives here.

Tables
  records   every record ingested so far (raw JSON + the day it arrived)
  feedback  feedback lines, read one day late (day N feedback arrives with ingest N+1)
  forgets   forget requests from the `forget` command
  meta      small key/values, e.g. the last ingested day
  llm_facts facts the model found in a record (checked by code), see extract.py
  llm_cache model answers keyed by prompt hash, so replays cost nothing
"""
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, day INTEGER, at TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS feedback (day INTEGER, person TEXT, type TEXT, json TEXT, UNIQUE(day, json));
CREATE TABLE IF NOT EXISTS forgets (person TEXT, words TEXT, day INTEGER, UNIQUE(person, words));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS llm_facts (record_id TEXT, key TEXT, json TEXT, PRIMARY KEY (record_id, key));
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, resp TEXT, in_tok INTEGER, out_tok INTEGER);
"""


def pack_root() -> str:
    """Where the data pack lives: $R3_PACK, else the current folder if it has data/days."""
    env = os.environ.get("R3_PACK")
    if env and os.path.isdir(os.path.join(env, "data", "days")):
        return os.path.abspath(env)
    here = os.getcwd()
    if os.path.isdir(os.path.join(here, "data", "days")):
        return here
    # convenience: a pack unzipped one level down, e.g. data/BuildSprint-R3-candidate
    for base in (here, os.path.join(here, "data")):
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                cand = os.path.join(base, name)
                if os.path.isdir(os.path.join(cand, "data", "days")):
                    return cand
    return here


def store_dir() -> str:
    d = os.environ.get("R3_STORE") or os.path.join(os.getcwd(), ".assist_store")
    os.makedirs(d, exist_ok=True)
    return d


def db_path() -> str:
    return os.path.join(store_dir(), "assist.db")


def connect(fresh: bool = False) -> sqlite3.Connection:
    path = db_path()
    if fresh and os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def get_meta(con, key, default=None):
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return json.loads(row["v"]) if row else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
