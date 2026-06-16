#!/usr/bin/env python3
"""
Mikail-AI — Phase 2 RAG: hibrit arama (FTS5 BM25 + vektör, RRF birleştirme)
Tamamen YEREL. Embedding modeli: bge-m3 (Ollama).

Kullanım:
  python search.py "sorgu metni"
  python search.py "sorgu" -k 8 --json
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.request

HOME = os.path.expanduser("~")
INDEX_DB = os.path.join(HOME, "mikail-ai", "rag", "index.db")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "bge-m3")
RRF_K = 60
POOL = 50  # her kaynaktan kaç aday

import sqlite_vec  # noqa: E402


def embed(text):
    payload = json.dumps({"model": EMBED_MODEL, "input": [text]}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/embed", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["embeddings"][0]


def open_index():
    db = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.row_factory = sqlite3.Row
    return db


VEC_KNN_MAX = 4096   # sqlite-vec KNN k üst sınırı

def vec_search(db, qvec, k, kind=None):
    # kind filtresinde nadir tür (ör. typed ~%2) küçük over-fetch'te aç kalır; izin verilen en
    # geniş komşu havuzunu (KNN üst sınırı) çekip hedef-tür üyeliğine göre süz, sonra k'ya kes.
    if kind:
        total = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        fetch = min(total, VEC_KNN_MAX)
    else:
        fetch = min(k, VEC_KNN_MAX)
    rows = db.execute(
        """SELECT rowid, distance FROM chunks_vec
           WHERE embedding MATCH ? AND k = ? ORDER BY distance""",
        (sqlite_vec.serialize_float32(qvec), max(fetch, 1))).fetchall()
    ids = [r["rowid"] for r in rows]
    if kind and ids:
        keep = {r[0] for r in db.execute("SELECT id FROM chunks WHERE kind=?", (kind,)).fetchall()}
        ids = [i for i in ids if i in keep][:k]
    return ids


def fts_search(db, query, k, kind=None):
    # FTS5 sorgu güvenliği: terimleri tırnakla
    safe = " OR ".join('"' + t.replace('"', '') + '"' for t in query.split() if t)
    if not safe:
        return []
    try:
        if kind:
            rows = db.execute(
                """SELECT f.rowid FROM chunks_fts f JOIN chunks c ON c.id=f.rowid
                   WHERE chunks_fts MATCH ? AND c.kind=?
                   ORDER BY bm25(chunks_fts) LIMIT ?""", (safe, kind, k)).fetchall()
        else:
            rows = db.execute(
                """SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?
                   ORDER BY bm25(chunks_fts) LIMIT ?""", (safe, k)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["rowid"] for r in rows]


def rrf(*rankings):
    score = {}
    for ranking in rankings:
        for rank, rid in enumerate(ranking):
            score[rid] = score.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(score.items(), key=lambda x: -x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--kind", choices=["screen", "typed"], help="kayıt tipiyle filtrele")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(INDEX_DB):
        print("HATA: index yok — önce embed_daily.py çalıştır.", file=sys.stderr)
        sys.exit(1)

    db = open_index()
    vec_ids = vec_search(db, embed(args.query), POOL, args.kind)
    fts_ids = fts_search(db, args.query, POOL, args.kind)
    merged = rrf(vec_ids, fts_ids)   # kind filtresi sonrası k'ya kadar doldurmak için tam liste

    results = []
    for rid, sc in merged:
        row = db.execute(
            "SELECT kind, ts_start, ts_end, app, window, url, n_frames, text FROM chunks WHERE id=?",
            (rid,)).fetchone()
        if not row:
            continue
        if args.kind and row["kind"] != args.kind:
            continue
        results.append({
            "score": round(sc, 4), "kind": row["kind"], "ts": row["ts_start"], "app": row["app"],
            "window": row["window"], "url": row["url"], "n_frames": row["n_frames"],
            "snippet": (row["text"][:280] + ("…" if len(row["text"]) > 280 else "")),
        })
        if len(results) >= args.k:
            break

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print(f"\nSorgu: {args.query!r}  (vec={len(vec_ids)} + fts={len(fts_ids)} → RRF)\n")
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r['score']}  «{r['kind']}»  {r['ts']}  {r['app'] or '?'} / {r['window'] or '?'}")
        if r["url"]:
            print(f"     {r['url']}")
        print(f"     {r['snippet'].replace(chr(10), ' ')}\n")


if __name__ == "__main__":
    main()
