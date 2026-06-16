#!/usr/bin/env python3
"""
Mikail-AI — bir kerelik migration: embed-kesme (>8000 char) mismatch'li chunk'ları
böl + yeniden göm. Phase 2 düzeltmesi #2'nin MEVCUT veriye uygulanması.

>8000 char chunk'larda eski kod yalnız ilk 8000'i embed etmiş ama FTS/saklama tam metni
tutuyor (vektör kuyruk içeriğini görmüyor = sessiz recall kaybı). Bu script onları
CHUNK_CHAR_LIMIT'e bölüp her parçayı tam metniyle yeniden gömerek hizalar.

Kullanım: python resplit_oversized.py [--threshold 8000]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite_vec  # noqa: E402
from embed_daily import (CHUNK_CHAR_LIMIT, EMBED_BATCH, chunk_hash,   # noqa: E402
                         embed_batch, open_index, split_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=8000)
    args = ap.parse_args()

    idx = open_index()
    existing = {h for (h,) in idx.execute(
        "SELECT chunk_hash FROM chunks WHERE chunk_hash IS NOT NULL").fetchall()}
    over = idx.execute(
        """SELECT id, kind, frame_start, frame_end, ts_start, ts_end,
                  app, window, url, n_frames, text
           FROM chunks WHERE length(text) > ?""", (args.threshold,)).fetchall()
    print(f"{len(over)} chunk > {args.threshold} char — bölünecek")
    if not over:
        return

    new, del_ids = [], []
    for (cid, kind, fs, fe, ts0, ts1, app, win, url, nf, text) in over:
        del_ids.append((cid, text))
        for p in split_text(text, CHUNK_CHAR_LIMIT):
            h = chunk_hash(kind, p)
            if h in existing:                       # zaten varsa tekrar gömme
                continue
            existing.add(h)
            new.append((kind, h, fs, fe, ts0, ts1, app, win, url, nf, p))
    print(f"{len(new)} yeni sub-chunk gömülecek, {len(del_ids)} orijinal silinecek")

    # orijinalleri sil (external-content FTS 'delete' + chunks + vec)
    for cid, text in del_ids:
        idx.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)", (cid, text))
        idx.execute("DELETE FROM chunks WHERE id=?", (cid,))
        idx.execute("DELETE FROM chunks_vec WHERE rowid=?", (cid,))
    idx.commit()

    next_id = idx.execute("SELECT COALESCE(MAX(id),0) FROM chunks").fetchone()[0] + 1
    done = 0
    for i in range(0, len(new), EMBED_BATCH):
        batch = new[i:i + EMBED_BATCH]
        embs = embed_batch([c[10] for c in batch])   # c[10] = part text (zaten <= limit)
        for c, e in zip(batch, embs):
            nid = next_id
            next_id += 1
            idx.execute("""INSERT INTO chunks(id,kind,chunk_hash,frame_start,frame_end,
                           ts_start,ts_end,app,window,url,n_frames,text)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (nid, c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10]))
            idx.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (nid, c[10]))
            idx.execute("INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)",
                        (nid, sqlite_vec.serialize_float32(e)))
            done += 1
        idx.commit()
        print(f"  {done}/{len(new)}", end="\r", flush=True)
    print(f"\n✅ {done} sub-chunk gömüldü, {len(del_ids)} oversized chunk bölündü")


if __name__ == "__main__":
    main()
