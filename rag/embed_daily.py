#!/usr/bin/env python3
"""
Mikail-AI — Phase 2 RAG: artımlı gömme (embed-daily)
Denetim planı: PHASE2-PLAN.md

İki kayıt tipi indekslenir (Mikail kararı 2026-06-11: %100 kapsam, kısıtlama yok):
  • kind='screen'  → frames + ocr_text/accessibility_text  ("gördüğüm ekran")
  • kind='typed'   → ui_events (klavye 'text' + 'clipboard')  ("yazdığım metin")

Akış:
  1. Screenpipe DB'sini READ-ONLY açar (kayıt servisinin DB'sine ASLA yazmaz).
  2. rag/state.json watermark'tan devam eder — artımlı:
       last_frame_id     (frames için)
       last_ui_event_id  (ui_events için)
  3. Dedup: content_hash (exact-dup) + ardışık aynı-metin atlanır.
  4. Metin önceliği (screen): accessibility_text (temiz) > ocr_text.text. text_json'a DOKUNULMAZ.
  5. Chunk: aynı app+window ardışık kayıtları "oturum bloğu"na grupla (~512-1024 token).
  6. ui_events app/window atfı: kendi app_name'i > frame_id JOIN > en yakın-zaman frame (fallback).
  7. Embedding: Ollama bge-m3 (1024-dim, TR/FR/EN çok dilli).
  8. Yazım: rag/index.db — sqlite-vec (vec0) + FTS5 (BM25 hibrit için). HER ŞEY YEREL.

Kullanım:
  python embed_daily.py                  # her iki watermark'tan itibaren tümünü işle
  python embed_daily.py --max-frames 4000   # frames'i ilk koşuda sınırla
  python embed_daily.py --only-ui        # sadece ui_events pass'i (frames'i atla)
  python embed_daily.py --skip-ui        # sadece frames pass'i (ui_events'i atla)
  python embed_daily.py --reset          # her iki watermark'ı sıfırla (index'i silmez)
"""
import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
SRC_DB = os.path.join(HOME, ".screenpipe", "db.sqlite")
RAG_DIR = os.path.join(HOME, "mikail-ai", "rag")
INDEX_DB = os.path.join(RAG_DIR, "index.db")
STATE = os.path.join(RAG_DIR, "state.json")
LOCK_PATH = os.path.join(RAG_DIR, ".embed.lock")

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "bge-m3")
EMBED_DIM = 1024
CHUNK_CHAR_LIMIT = 2200       # ~512-700 token oturum-bloğu sınırı (hard-split üst sınırı)
EMBED_BATCH = 24              # tek ollama çağrısında kaç chunk
MIN_TEXT_LEN = 12             # bundan kısa metni atla (gürültü)
UI_EVENT_TYPES = ("text", "clipboard")  # RAG'a giren ui_events tipleri (yazılan/yapıştırılan metin)

import sqlite_vec  # noqa: E402


# ---------- idempotensi: chunk metninin kararlı hash'i (kind+text) ----------
def chunk_hash(kind, text):
    """Süreçler arası kararlı (blake2b) hash. Aynı metin -> aynı hash -> çift-gömme önlenir."""
    return hashlib.blake2b((kind + "\x00" + text).encode("utf-8"), digest_size=12).hexdigest()


# ---------- uzun metni embed/FTS hizalı kalacak şekilde böl (8000-char kesme bug'ı) ----------
def split_text(text, limit):
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                 # tek satır limitten uzunsa sert böl
            if buf:
                parts.append(buf); buf = ""
            parts.append(line[:limit]); line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            parts.append(buf); buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        parts.append(buf)
    return parts


def finalize_chunks(chunks, kind):
    """Uzun chunk'ları CHUNK_CHAR_LIMIT'e böl (embed=FTS=saklama hizalı) ve chunk_hash ata."""
    out = []
    for c in chunks:
        for j, p in enumerate(split_text(c["text"], CHUNK_CHAR_LIMIT)):
            d = dict(c)
            d["text"] = p
            d["chunk_hash"] = chunk_hash(kind, p)
            d["src_hashes"] = c.get("src_hashes", []) if j == 0 else []   # bir kez yeter
            out.append(d)
    return out


# ---------- state ----------
def load_state():
    st = {"last_frame_id": 0, "last_ui_event_id": 0, "chunks": 0, "runs": 0, "last_run": None}
    if os.path.exists(STATE):
        with open(STATE) as f:
            st.update(json.load(f))
    st.setdefault("last_ui_event_id", 0)   # eski state.json uyumu
    return st


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)


# ---------- index db ----------
def open_index():
    db = sqlite3.connect(INDEX_DB)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute(f"""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec
                   USING vec0(embedding float[{EMBED_DIM}])""")
    db.execute("""CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY,
                    kind TEXT DEFAULT 'screen',
                    chunk_hash TEXT,
                    frame_start INTEGER, frame_end INTEGER,
                    ts_start TEXT, ts_end TEXT,
                    app TEXT, window TEXT, url TEXT,
                    n_frames INTEGER, text TEXT)""")
    # Geriye dönük migration: eski index'lerde eksik sütunları ekle.
    cols = {r[1] for r in db.execute("PRAGMA table_info(chunks)").fetchall()}
    if "kind" not in cols:
        db.execute("ALTER TABLE chunks ADD COLUMN kind TEXT DEFAULT 'screen'")
    if "chunk_hash" not in cols:
        db.execute("ALTER TABLE chunks ADD COLUMN chunk_hash TEXT")
    # chunk_hash backfill (idempotensi anahtarı) — mevcut satırlar da çift-gömmeye karşı korunur.
    missing = db.execute(
        "SELECT id, COALESCE(kind,'screen'), text FROM chunks WHERE chunk_hash IS NULL").fetchall()
    if missing:
        db.executemany("UPDATE chunks SET chunk_hash=? WHERE id=?",
                       [(chunk_hash(k, t or ""), i) for (i, k, t) in missing])
    db.execute("CREATE INDEX IF NOT EXISTS ix_chunks_hash ON chunks(chunk_hash)")
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                  USING fts5(text, content='chunks', content_rowid='id',
                             tokenize='unicode61 remove_diacritics 2')""")
    db.execute("""CREATE TABLE IF NOT EXISTS seen_hashes(h INTEGER PRIMARY KEY)""")
    db.commit()
    return db


# ---------- read-only source ----------
def open_source_ro():
    uri = f"file:{SRC_DB}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=10)
    db.execute("PRAGMA busy_timeout=5000")
    db.row_factory = sqlite3.Row
    return db


# ========== FRAMES (kind='screen') ==========
def fetch_frames(src, last_id, max_frames):
    q = """SELECT f.id, f.timestamp, f.app_name, f.window_name, f.browser_url,
                  f.content_hash, f.accessibility_text, o.text AS ocr
           FROM frames f
           LEFT JOIN ocr_text o ON o.frame_id = f.id
           WHERE f.id > ?
           ORDER BY f.id"""
    if max_frames:
        q += f" LIMIT {int(max_frames)}"
    return src.execute(q, (last_id,)).fetchall()


def pick_text(row):
    at = (row["accessibility_text"] or "").strip()
    if len(at) >= MIN_TEXT_LEN:
        return at
    oc = (row["ocr"] or "").strip()
    return oc if len(oc) >= MIN_TEXT_LEN else ""


def build_chunks(rows, seen):
    """Ardışık aynı app+window frame'lerini oturum bloğuna grupla; content_hash dedup."""
    chunks = []
    cur = None

    def flush():
        nonlocal cur
        if cur and len(cur["text"]) >= MIN_TEXT_LEN:
            chunks.append(cur)
        cur = None

    last_text = None
    for r in rows:
        ch = r["content_hash"]
        if ch is not None:
            if ch in seen:
                continue            # exact-dup frame
            seen.add(ch)
        txt = pick_text(r)
        if not txt:
            continue
        if txt == last_text:        # ardışık birebir aynı metin
            continue
        last_text = txt
        key = (r["app_name"], r["window_name"])
        if cur is None or cur["key"] != key or len(cur["text"]) >= CHUNK_CHAR_LIMIT:
            flush()
            cur = {"key": key, "app": r["app_name"], "window": r["window_name"],
                   "url": r["browser_url"], "frame_start": r["id"], "frame_end": r["id"],
                   "ts_start": r["timestamp"], "ts_end": r["timestamp"],
                   "n_frames": 0, "text": "", "src_hashes": []}
        cur["text"] = (cur["text"] + "\n" + txt).strip() if cur["text"] else txt
        cur["frame_end"] = r["id"]
        cur["ts_end"] = r["timestamp"]
        cur["n_frames"] += 1
        if ch is not None:                          # bu chunk'ı oluşturan content_hash'ler
            cur["src_hashes"].append(ch)            # (seen_hashes batch ile atomik kalıcılaşsın diye)
    flush()
    return chunks


# ========== UI_EVENTS (kind='typed') ==========
def fetch_ui_events(src, last_uid):
    """Yazılan/yapıştırılan metin. app/window: kendi alanı > frame_id JOIN (fallback en-yakın-zaman main'de)."""
    placeholders = ",".join("?" * len(UI_EVENT_TYPES))
    q = f"""SELECT u.id, u.timestamp, u.event_type, u.text_content,
                   COALESCE(NULLIF(u.app_name,''),     ff.app_name)    AS app,
                   COALESCE(NULLIF(u.window_title,''), ff.window_name) AS window,
                   COALESCE(NULLIF(u.browser_url,''),  ff.browser_url) AS url
            FROM ui_events u
            LEFT JOIN frames ff ON ff.id = u.frame_id
            WHERE u.event_type IN ({placeholders})
              AND u.text_content IS NOT NULL
              AND LENGTH(TRIM(u.text_content)) >= ?
              AND u.id > ?
            ORDER BY u.id"""
    return src.execute(q, (*UI_EVENT_TYPES, MIN_TEXT_LEN, last_uid)).fetchall()


def nearest_frame_ctx(src, ts, window_min=5):
    """Atfı boş ui_event için ±window_min dk içindeki en yakın-zaman frame bağlamı.
    Sargable (idx_frames_timestamp + ISO-T metin aralığı); pencerede frame yoksa None
    döner (alakasız uzak frame'e yanlış atıf yapmaz). julianday yalnız LIMIT 1 satırda."""
    try:
        t = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    lo_b = (t - timedelta(minutes=window_min)).isoformat()
    hi_b = (t + timedelta(minutes=window_min)).isoformat()
    lo = src.execute(
        """SELECT app_name, window_name, browser_url,
                  ABS(julianday(timestamp) - julianday(?)) AS d FROM frames
           WHERE timestamp <= ? AND timestamp >= ?
           ORDER BY timestamp DESC LIMIT 1""", (ts, ts, lo_b)).fetchone()
    hi = src.execute(
        """SELECT app_name, window_name, browser_url,
                  ABS(julianday(timestamp) - julianday(?)) AS d FROM frames
           WHERE timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC LIMIT 1""", (ts, ts, hi_b)).fetchone()
    cands = [c for c in (lo, hi) if c is not None]
    return min(cands, key=lambda c: c["d"]) if cands else None


def build_ui_chunks(src, rows):
    """Ardışık aynı app+window typed-event'leri oturum bloğuna grupla."""
    chunks = []
    cur = None

    def flush():
        nonlocal cur
        if cur and len(cur["text"]) >= MIN_TEXT_LEN:
            chunks.append(cur)
        cur = None

    last_text = None
    for r in rows:
        txt = (r["text_content"] or "").strip()
        if len(txt) < MIN_TEXT_LEN or txt == last_text:
            continue
        last_text = txt
        app, window, url = r["app"], r["window"], r["url"]
        if not app:                                   # fallback: en yakın-zaman frame
            nf = nearest_frame_ctx(src, r["timestamp"])
            if nf:
                app = app or nf["app_name"]
                window = window or nf["window_name"]
                url = url or nf["browser_url"]
        key = (app, window)
        if cur is None or cur["key"] != key or len(cur["text"]) >= CHUNK_CHAR_LIMIT:
            flush()
            cur = {"key": key, "app": app, "window": window, "url": url,
                   "frame_start": r["id"], "frame_end": r["id"],
                   "ts_start": r["timestamp"], "ts_end": r["timestamp"],
                   "n_frames": 0, "text": ""}
        cur["text"] = (cur["text"] + "\n" + txt).strip() if cur["text"] else txt
        cur["frame_end"] = r["id"]
        cur["ts_end"] = r["timestamp"]
        cur["n_frames"] += 1
    flush()
    return chunks


# ---------- embedding ----------
def embed_batch(texts, retries=3):
    """Ollama bge-m3 embed. Geçici hataları (HTTP 500/timeout — gece job'unda gözlendi) backoff'la retry'ler.
    Per-batch watermark + chunk_hash idempotensi sayesinde son çare hata bile çift-gömme üretmez."""
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/embed", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            embs = data.get("embeddings")
            if not embs or len(embs) != len(texts):
                raise RuntimeError(f"embed mismatch: got {len(embs) if embs else 0} for {len(texts)}")
            return embs
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))       # 2s, 4s backoff
    raise RuntimeError(f"embed {retries} denemede başarısız: {last_err}")


def embed_and_store(idx, chunks, kind, existing, st, wm_key):
    """chunks'ı gömüp index.db'ye yazar — chunk_hash ile İDEMPOTENT (çökme/retry'de çift-gömme yok).
    Her batch'ten sonra watermark'ı (wm_key) batch'in en yüksek kaynak id'sine ilerletip kaydeder,
    böylece kesinti olursa sonraki koşu işlenmemiş yerden devam eder. Döndürür: yazılan yeni chunk."""
    if not chunks:
        return 0
    next_id = (idx.execute("SELECT COALESCE(MAX(id),0) FROM chunks").fetchone()[0]) + 1
    t0 = time.time()
    done = 0
    skipped = 0
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        fresh = [c for c in batch if c["chunk_hash"] not in existing]   # zaten gömülmüşü atla
        skipped += len(batch) - len(fresh)
        if fresh:
            embs = embed_batch([c["text"][:8000] for c in fresh])
            for c, e in zip(fresh, embs):
                cid = next_id
                next_id += 1
                idx.execute("""INSERT INTO chunks(id,kind,chunk_hash,frame_start,frame_end,
                               ts_start,ts_end,app,window,url,n_frames,text)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (cid, kind, c["chunk_hash"], c["frame_start"], c["frame_end"],
                             c["ts_start"], c["ts_end"], c["app"], c["window"], c["url"],
                             c["n_frames"], c["text"]))
                idx.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, c["text"]))
                idx.execute("INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)",
                            (cid, sqlite_vec.serialize_float32(e)))
                existing.add(c["chunk_hash"])
                done += 1
        # content_hash dedup'ı chunk yazımıyla AYNI transaction'da kalıcılaştır (çökme-güvenli sınır)
        batch_hashes = [h for c in batch for h in c.get("src_hashes", [])]
        if batch_hashes:
            idx.executemany("INSERT OR IGNORE INTO seen_hashes(h) VALUES(?)", [(h,) for h in batch_hashes])
        if wm_key:                                  # resumable: watermark = batch'in en yüksek kaynak id'si
            st[wm_key] = max(st.get(wm_key, 0), batch[-1]["frame_end"])
        idx.commit()
        if wm_key:
            save_state(st)
        print(f"  [{kind}] yeni {done} / atlanan {skipped} / {len(chunks)}  "
              f"({(done+skipped)/max(time.time()-t0,1e-6):.1f} chunk/s)", end="\r", flush=True)
    print(f"\n  [{kind}] ✅ {done} yeni chunk gömüldü, {skipped} zaten vardı ({time.time()-t0:.1f}s)")
    return done


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--only-ui", action="store_true", help="sadece ui_events pass'i")
    ap.add_argument("--skip-ui", action="store_true", help="ui_events pass'ini atla")
    args = ap.parse_args()

    if not os.path.exists(SRC_DB):
        print(f"HATA: kaynak DB yok: {SRC_DB}", file=sys.stderr)
        sys.exit(1)

    # tek-yazar kilidi: gece launchd job'u ile elle koşunun aynı anda yazmasını önler (#7)
    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("başka bir embed koşusu sürüyor — bu koşu atlandı.")
        return

    st = load_state()
    idx = open_index()

    if args.reset:                                  # tam yeniden inşa (artık dürüst no-op değil)
        idx.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')")  # external-content FTS5
        idx.execute("DELETE FROM chunks")
        idx.execute("DELETE FROM chunks_vec")
        idx.execute("DELETE FROM seen_hashes")
        idx.commit()
        st["last_frame_id"] = 0
        st["last_ui_event_id"] = 0
        print("RESET: index tabloları + watermark sıfırlandı (tam yeniden inşa)")

    seen = {h for (h,) in idx.execute("SELECT h FROM seen_hashes").fetchall()}
    existing = {h for (h,) in idx.execute(
        "SELECT chunk_hash FROM chunks WHERE chunk_hash IS NOT NULL").fetchall()}
    src = open_source_ro()

    # ---- FRAMES pass (kind='screen') ----
    if not args.only_ui:
        rows = fetch_frames(src, st["last_frame_id"], args.max_frames)
        if rows:
            max_id = max(r["id"] for r in rows)
            print(f"[screen] okunan frame: {len(rows)} (id {st['last_frame_id']+1}..{max_id})")
            chunks = finalize_chunks(build_chunks(rows, seen), "screen")
            print(f"[screen] oluşan chunk: {len(chunks)} (dedup+gruplama+split sonrası)")
            embed_and_store(idx, chunks, "screen", existing, st, "last_frame_id")
            # seen_hashes artık embed_and_store içinde batch-başı atomik kalıcılaşıyor.
            st["last_frame_id"] = max_id            # trailing atlanan frame'leri de kapsa
            st["last_run"] = rows[-1]["timestamp"]
            idx.commit()
            save_state(st)
        else:
            print(f"[screen] yeni frame yok (watermark id={st['last_frame_id']}).")

    # ---- UI_EVENTS pass (kind='typed') ----
    if not args.skip_ui:
        urows = fetch_ui_events(src, st["last_ui_event_id"])
        if urows:
            umax = max(r["id"] for r in urows)
            print(f"[typed] okunan ui_event: {len(urows)} (id {st['last_ui_event_id']+1}..{umax})")
            uchunks = finalize_chunks(build_ui_chunks(src, urows), "typed")
            print(f"[typed] oluşan chunk: {len(uchunks)} (gruplama+split sonrası)")
            embed_and_store(idx, uchunks, "typed", existing, st, "last_ui_event_id")
            st["last_ui_event_id"] = umax
            save_state(st)
        else:
            print(f"[typed] yeni ui_event yok (watermark id={st['last_ui_event_id']}).")

    st["chunks"] = idx.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    st["runs"] = st.get("runs", 0) + 1
    save_state(st)
    by_kind = dict(idx.execute("SELECT kind, COUNT(*) FROM chunks GROUP BY kind").fetchall())
    print(f"\n✅ toplam index chunk={st['chunks']}  {by_kind}  "
          f"watermark frame={st['last_frame_id']} ui_event={st['last_ui_event_id']}")


if __name__ == "__main__":
    main()
