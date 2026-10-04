"""Conservative association of a ticket image and subsequent Telegram messages.

No OCR or inferred event times. Image tickets are for manual review only.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qs

UTC = timezone.utc
FREE = re.compile(r"\bfree[\s_-]+(?:pick|parlay)\b", re.I)
URL = re.compile(r"https?://[^\s<>]+")


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS fragments (
      id TEXT PRIMARY KEY, sender TEXT NOT NULL, topic TEXT NOT NULL,
      message_id INTEGER NOT NULL, created TEXT NOT NULL, changed TEXT NOT NULL,
      raw TEXT NOT NULL, links TEXT NOT NULL, media TEXT, album TEXT,
      reply_to INTEGER);
    CREATE TABLE IF NOT EXISTS bundle_state (id TEXT PRIMARY KEY, state TEXT NOT NULL);
    """)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def valid_link(value):
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.hostname in ("playdoit.mx", "www.playdoit.mx")
                and not parts.username and not parts.password and parts.port in (None, 443)
                and len(parse_qs(parts.query).get("shareCode", [])) == 1)
    except ValueError:
        return False


def record(db, cfg, msg):
    raw = msg.get("text") or msg.get("caption") or ""
    links = URL.findall(raw)
    for entity in msg.get("entities", []) + msg.get("caption_entities", []):
        if entity.get("type") == "text_link":
            links.append(entity.get("url", ""))
    links = sorted({link for link in links if valid_link(link)})
    media = None
    if msg.get("photo"):
        photo = max(msg["photo"], key=lambda p: p.get("width", 0) * p.get("height", 0))
        media = {"type": "photo", "file_id": photo["file_id"],
                 "unique_id": photo.get("file_unique_id", photo["file_id"])}
    elif msg.get("document", {}).get("mime_type") in ("image/jpeg", "image/png", "image/webp"):
        doc = msg["document"]
        media = {"type": "document", "file_id": doc["file_id"],
                 "unique_id": doc.get("file_unique_id", doc["file_id"])}
    # Store only accepted source messages; different senders/topics never share a bundle.
    sender = str(msg.get("from", {}).get("id") or msg.get("sender_chat", {}).get("id") or cfg.chat)
    source = f'{cfg.chat}:{msg["message_id"]}'
    db.execute("""INSERT INTO fragments VALUES (?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET raw=excluded.raw,links=excluded.links,
      media=excluded.media,changed=excluded.changed,album=excluded.album,reply_to=excluded.reply_to""",
      (source, sender, str(msg.get("message_thread_id", "")), msg["message_id"],
       iso(msg["date"]), iso(msg.get("edit_date", msg["date"])), raw,
       json.dumps(links), json.dumps(media) if media else None, msg.get("media_group_id"),
       msg.get("reply_to_message", {}).get("message_id")))


def assemble(db, cfg, now):
    rows = db.execute("SELECT * FROM fragments WHERE created>=? ORDER BY message_id",
                      ((now - timedelta(days=2)).isoformat(),)).fetchall()
    groups = {}
    for row in rows:
        groups.setdefault((row["sender"], row["topic"]), []).append(row)
    # Rebuild unsent bundles from persisted fragments, including edits/removals.
    db.execute("DELETE FROM messages WHERE id LIKE 'bundle:%' AND id NOT IN (SELECT source FROM posts)")
    for fragments in groups.values():
        active = None
        bundles = []
        for row in fragments:
            at = datetime.fromisoformat(row["created"])
            if active and (at - datetime.fromisoformat(active["root"]["created"])).total_seconds() > cfg.bundle_window:
                active = None
            if row["media"]:
                # Two images before a completed free ticket are ambiguous; don't guess.
                ambiguous = bool(active and not (active["links"] and active["free"]))
                if ambiguous:
                    active["ambiguous"] = True
                active = {"root": row, "links": set(), "free": False, "parlay": False,
                          "members": [], "ambiguous": ambiguous or bool(row["album"])}
                bundles.append(active)
            if active is None:
                continue
            if row["reply_to"] and row["reply_to"] not in [active["root"]["message_id"], *[r["message_id"] for r in active["members"]]]:
                active["ambiguous"] = True
            active["members"].append(row)
            active["links"].update(json.loads(row["links"]))
            active["free"] |= bool(FREE.search(row["raw"]))
            active["parlay"] |= bool(re.search(r"\bfree[\s_-]+parlay\b", row["raw"], re.I))
        for bundle in bundles:
            root = bundle["root"]
            source = "bundle:" + root["id"]
            last = max(datetime.fromisoformat(r["changed"]) for r in bundle["members"])
            age = (now - last).total_seconds()
            if bundle["ambiguous"] or len(bundle["links"]) > 1:
                state = "ambiguous: revisar asociacion de imagen/enlace"
            elif not bundle["links"]:
                state = "pending: falta enlace Playdoit con shareCode"
            elif age < cfg.bundle_settle:
                state = "pending: esperando mensajes posteriores"
            else:
                state = "ready: revisar vigencia manualmente"
                media = json.loads(root["media"])
                payload = {"kind": "image", "media": media,
                           "playdoit_url": next(iter(bundle["links"])),
                           "label": "Free Parlay" if bundle["parlay"] else "Free Pick" if bundle["free"] else "Pick abierto"}
                db.execute("""INSERT INTO messages VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET free=excluded.free,
                  ever_free=MAX(messages.ever_free,excluded.ever_free),payload=excluded.payload""",
                  (source, root["created"], "[Ticket agrupado]", int(bundle["free"]),
                   int(bundle["free"]), json.dumps(payload, ensure_ascii=False), None))
            db.execute("INSERT INTO bundle_state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state", (source, state))


def identity(pick):
    # Preserve case-sensitive share codes. Telegram unique IDs survive file_id changes.
    value = pick["media"]["unique_id"]
    return hashlib.sha256(("image:" + value).encode()).hexdigest()


def compose(cfg, pick):
    text = (f'{pick["label"]}\nSelección en la imagen.\n'
            f'Playdoit: {pick["playdoit_url"]}\nGratis: {cfg.cta}\n'
            'Premium: escribe por DM.\n18+ | Sin ganancias garantizadas.')
    # Leave room for the review instructions in Telegram's 1024-character caption.
    if len(text.encode("utf-16-le")) // 2 > 800:
        raise ValueError("Enlace/texto demasiado largo para el pie de imagen; revisar manualmente")
    return text
