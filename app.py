"""Telegram -> X. Single worker; deterministic selection and durable publication ledger."""
import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
import media_bundles

UTC = timezone.utc
LOG = logging.getLogger("tipster")
FREE = re.compile(r"\bfree[\s_-]+(?:pick|parlay)\b", re.I)
URL = re.compile(r"https?://\S+")


def stamp(value):
    return value.astimezone(UTC).isoformat()


def parse_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Inicio requiere zona horaria: 2026-10-05T19:00:00-06:00")
    return result.astimezone(UTC)


@dataclass
class Config:
    db: str = "bot.sqlite3"
    zone: str = "America/Mexico_City"
    dry: bool = True
    authorized: bool = False
    fallback: bool = False
    chat: str = ""
    senders: tuple = ()
    cta: str = "https://t.me/REEMPLAZAR"
    lead: int = 30
    daily: int = 3
    gap: int = 30
    free_mode: str = "immediate"
    slots: tuple = ("11:00", "13:00", "14:30")
    cutoff: str = "15:00"
    grace: int = 15
    delivery: str = "telegram_draft"
    bundle_window: int = 300
    bundle_settle: int = 90

    @classmethod
    def env(cls):
        truth = lambda key: os.getenv(key, "false").lower() == "true"
        cfg = cls(db=os.getenv("DB_PATH", "bot.sqlite3"),
                  zone=os.getenv("TIMEZONE", "America/Mexico_City"),
                  dry=os.getenv("DRY_RUN", "true").lower() == "true",
                  authorized=truth("SOURCE_AUTHORIZED"), fallback=truth("ALLOW_FALLBACK"),
                  chat=os.getenv("TELEGRAM_CHAT_ID", ""),
                  senders=tuple(x.strip() for x in os.getenv("TELEGRAM_SENDER_IDS", "").split(",") if x.strip()),
                  cta=os.getenv("CTA_URL", "https://t.me/REEMPLAZAR"),
                  lead=int(os.getenv("MIN_LEAD_MINUTES", "30")),
                  daily=int(os.getenv("MAX_POSTS_PER_DAY", "3")),
                  gap=int(os.getenv("MIN_GAP_MINUTES", "30")),
                  free_mode=os.getenv("FREE_MODE", "immediate"),
                  slots=tuple(os.getenv("FREE_SLOTS", "11:00,13:00,14:30").split(",")),
                  cutoff=os.getenv("FALLBACK_TIME", "15:00"),
                  grace=int(os.getenv("FALLBACK_GRACE_MINUTES", "15")),
                  delivery=os.getenv("DELIVERY", "telegram_draft"),
                  bundle_window=int(os.getenv("BUNDLE_WINDOW_SECONDS", "300")),
                  bundle_settle=int(os.getenv("BUNDLE_SETTLE_SECONDS", "90")))
        ZoneInfo(cfg.zone)
        for slot in (*cfg.slots, cfg.cutoff):
            datetime.strptime(slot, "%H:%M")
        if cfg.free_mode not in ("immediate", "slots") or min(cfg.lead, cfg.daily, cfg.gap, cfg.grace) <= 0:
            raise ValueError("Configuracion de horarios o limites invalida")
        if not re.fullmatch(r"https://[^\s]+", cfg.cta):
            raise ValueError("CTA_URL debe ser un enlace HTTPS")
        if cfg.delivery not in ("telegram_draft", "x_api"):
            raise ValueError("DELIVERY invalido")
        if not 30 <= cfg.bundle_settle <= cfg.bundle_window <= 900:
            raise ValueError("Usar 30 <= BUNDLE_SETTLE_SECONDS <= BUNDLE_WINDOW_SECONDS <= 900")
        return cfg


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY, created TEXT NOT NULL, raw TEXT NOT NULL,
      free INTEGER NOT NULL, ever_free INTEGER NOT NULL, payload TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS posts (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL UNIQUE,
      day TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
      at TEXT NOT NULL, x_id TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS editorial (
      id TEXT PRIMARY KEY, text TEXT NOT NULL, due TEXT NOT NULL, expires TEXT NOT NULL);
    """)
    media_bundles.init(db)
    if "media_json" not in {r[1] for r in db.execute("PRAGMA table_info(posts)")}:
        db.execute("ALTER TABLE posts ADD COLUMN media_json TEXT")
    return db


def getmeta(db, key, default=""):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def setmeta(db, key, value):
    db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def parse_pick(raw):
    # No model is allowed to invent teams, markets, prices or event start times.
    fields = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().casefold()
            if key in fields:
                raise ValueError("Campo duplicado: " + key)
            fields[key] = value.strip()
    required = ("evento", "pick", "cuota", "inicio")
    if not all(fields.get(key) for key in required):
        raise ValueError("Formato pendiente: faltan Evento, Pick, Cuota o Inicio")
    fields = {key: fields[key] for key in required}
    fields["inicio"] = stamp(parse_time(fields["inicio"]))
    if any("@" in fields[k] or URL.search(fields[k]) for k in ("evento", "pick", "cuota")):
        raise ValueError("Revisar menciones o enlaces dentro del pick")
    return fields


def ingest(db, cfg, update):
    msg = next((update[k] for k in ("message", "channel_post", "edited_message", "edited_channel_post") if k in update), None)
    if not msg or str(msg["chat"]["id"]) != cfg.chat:
        return
    if msg["chat"].get("type") != "channel":
        if str(msg.get("from", {}).get("id", "")) not in cfg.senders:
            return
    raw = msg.get("text") or msg.get("caption") or ""
    media_bundles.record(db, cfg, msg)
    free = bool(FREE.search(raw))
    try:
        payload, error = json.dumps(parse_pick(raw), ensure_ascii=False), None
    except ValueError as exc:
        payload, error = None, str(exc)
    if msg.get("photo") or msg.get("document", {}).get("mime_type", "").startswith("image/"):
        payload, error = None, "Imagen: pendiente de agrupacion con enlace y etiqueta"
    source = f'{cfg.chat}:{msg["message_id"]}'
    created = stamp(datetime.fromtimestamp(msg["date"], UTC))
    db.execute("""INSERT INTO messages VALUES (?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET raw=excluded.raw, free=excluded.free,
      ever_free=MAX(messages.ever_free,excluded.ever_free), payload=excluded.payload,error=excluded.error""",
      (source, created, raw, free, free, payload, error))
    if error:
        LOG.info("Mensaje %s pendiente de formato", source)
    elif db.execute("SELECT 1 FROM posts WHERE source=?", (source,)).fetchone():
        LOG.warning("Mensaje %s editado o repetido tras preparar/publicar: revisar si requiere correccion", source)


def record_poll(db, now):
    last = getmeta(db, "last_poll")
    if not last or (now - parse_time(last)).total_seconds() > 180:
        setmeta(db, "coverage_since", stamp(now))
    setmeta(db, "last_poll", stamp(now))


def weighted_length(text):
    # Conservative upper bound: overcounts emoji sequences, never truncates picks.
    without_urls = URL.sub("", text)
    weight = sum(1 if ord(c) <= 0x10FF or 0x2000 <= ord(c) <= 0x200D or
                 0x2010 <= ord(c) <= 0x201F or 0x2032 <= ord(c) <= 0x2037 else 2 for c in without_urls)
    return weight + 23 * len(URL.findall(text))


def fingerprint(pick):
    canonical = json.dumps({k: re.sub(r"\s+", " ", v.strip().casefold()) for k, v in pick.items()}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def compose(cfg, pick, free, key):
    phrases = json.loads(Path(__file__).with_name("phrases.json").read_text(encoding="utf-8"))
    phrase = phrases[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(phrases)]
    event_time = parse_time(pick["inicio"]).astimezone(ZoneInfo(cfg.zone)).strftime("%d/%m %H:%M %z")
    body = f'{"Free pick" if free else "Pick abierto"}\n{pick["evento"]}\n{pick["pick"]} | Cuota: {pick["cuota"]}\nInicio: {event_time}\nGratis: {cfg.cta}\nPremium: escribe por DM.\n18+ | Sin ganancias garantizadas.'
    if weighted_length(phrase + "\n" + body) <= 280:
        body = phrase + "\n" + body
    if weighted_length(body) > 280:
        raise ValueError("Supera 280 caracteres ponderados; resumir manualmente sin cambiar el pick")
    return body


def select_pick(db, cfg, now):
    local = now.astimezone(ZoneInfo(cfg.zone))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    h, m = map(int, cfg.cutoff.split(":"))
    cutoff = midnight.replace(hour=h, minute=m)
    rows = db.execute("SELECT * FROM messages WHERE created>=? AND created<=? ORDER BY created,id", (stamp(midnight), stamp(now))).fetchall()
    free_seen = any(r["ever_free"] for r in rows)
    coverage = getmeta(db, "coverage_since")
    last = getmeta(db, "last_poll")
    complete = bool(coverage and last and parse_time(coverage) <= midnight.astimezone(UTC)
                    and (now - parse_time(last)).total_seconds() <= 90)
    fallback_due = (cfg.fallback and not free_seen and complete and cutoff <= local < cutoff + timedelta(minutes=cfg.grace))
    fallback_done = db.execute("SELECT 1 FROM posts WHERE day=? AND kind='fallback'", (local.date().isoformat(),)).fetchone()
    for row in sorted(rows, key=lambda r: (not r["free"], r["created"])):
        if not row["payload"] or db.execute("SELECT 1 FROM posts WHERE source=?", (row["id"],)).fetchone():
            continue
        pick = json.loads(row["payload"])
        is_image = pick.get("kind") == "image"
        # Image-only tickets lack a trusted event time. Deliver for manual review,
        # never silently publish them via the optional X API text adapter.
        if is_image and cfg.delivery != "telegram_draft":
            continue
        latest = midnight + timedelta(days=1) if is_image else parse_time(pick["inicio"]) - timedelta(minutes=cfg.lead)
        if latest < now:
            continue
        if row["free"]:
            due = parse_time(row["created"])
            if cfg.free_mode == "slots":
                slots = [midnight.replace(hour=int(s.split(":")[0]), minute=int(s.split(":")[1])).astimezone(UTC) for s in cfg.slots]
                following = [s for s in slots if s >= due]
                # Never wait beyond the useful publication deadline.
                due = min(min(following), latest - timedelta(minutes=1)) if following else due
            if now < due:
                continue
        elif not fallback_due or fallback_done or parse_time(row["created"]) >= cutoff:
            continue
        fp = media_bundles.identity(pick) if is_image else fingerprint(pick)
        if db.execute("SELECT 1 FROM posts WHERE fingerprint=?", (fp,)).fetchone():
            continue
        try:
            text = media_bundles.compose(cfg, pick) if is_image else compose(cfg, pick, row["free"], row["id"])
            if weighted_length(text) > 280:
                raise ValueError("Borrador demasiado largo")
        except ValueError as exc:
            LOG.warning("Mensaje %s requiere revision: %s", row["id"], exc)
            continue
        return row["id"], fp, "free" if row["free"] else "fallback", text
    return None


class PublishFailure(Exception):
    def __init__(self, status):
        self.status = status


def publish_x(text):
    import requests
    from requests_oauthlib import OAuth1
    auth = OAuth1(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                  os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_TOKEN_SECRET"])
    response = requests.post("https://api.x.com/2/tweets", json={"text": text}, auth=auth, timeout=(10, 30))
    if response.status_code != 201:
        raise PublishFailure(response.status_code)
    return response.json()["data"]["id"]


def draft_payload(text, media=None):
    payload = {"chat_id": os.environ["TELEGRAM_REVIEW_CHAT_ID"],
            "text": "BORRADOR — revisa vigencia y cuota antes de publicar.\n\n" + text,
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {"inline_keyboard": [[{"text": "Abrir borrador en X", "url":
                "https://twitter.com/intent/tweet?" + urlencode({"text": text})}]]}}
    if media:
        payload.pop("link_preview_options")
        payload["caption"] = ("BORRADOR — horario no verificado. Revisa la imagen y cuota. "
                              "Guarda esta imagen y adjúntala en X; el botón solo carga el texto.\n\n" + text)
        payload.pop("text")
        payload[media["type"]] = media["file_id"]
    return payload


def send_draft(text, media=None):
    import requests
    method = "sendPhoto" if media and media["type"] == "photo" else "sendDocument" if media else "sendMessage"
    response = requests.post(f'https://api.telegram.org/bot{os.environ["TELEGRAM_BOT_TOKEN"]}/{method}',
                             json=draft_payload(text, media), timeout=(10, 30))
    if response.status_code != 200:
        raise PublishFailure(response.status_code)
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram no confirmo el borrador")
    return "telegram:" + str(result["result"]["message_id"])


def tick(db, cfg, now, publisher=None):
    if not cfg.dry and not cfg.authorized:
        raise ValueError("SOURCE_AUTHORIZED debe confirmar permiso de republicacion")
    # Commit an intent before a network write. Ambiguous outcomes never auto-retry.
    db.execute("BEGIN IMMEDIATE")
    try:
        media_bundles.assemble(db, cfg, now)
        day = now.astimezone(ZoneInfo(cfg.zone)).date().isoformat()
        count = db.execute("SELECT COUNT(*) FROM posts WHERE day=?", (day,)).fetchone()[0]
        last = db.execute("SELECT MAX(at) FROM posts").fetchone()[0]
        if count >= cfg.daily or (last and now - parse_time(last) < timedelta(minutes=cfg.gap)):
            db.commit()
            return
        item = select_pick(db, cfg, now)
        if not item:
            row = db.execute("""SELECT * FROM editorial WHERE due<=? AND expires>?
              AND NOT EXISTS (SELECT 1 FROM posts WHERE source='editorial:'||editorial.id)
              ORDER BY due LIMIT 1""", (stamp(now), stamp(now))).fetchone()
            if row:
                fp = hashlib.sha256(row["text"].strip().encode()).hexdigest()
                if not db.execute("SELECT 1 FROM posts WHERE fingerprint=?", (fp,)).fetchone():
                    item = ("editorial:" + row["id"], fp, "editorial", row["text"])
        if not item:
            db.commit()
            return
        source, fp, kind, text = item
        message = db.execute("SELECT payload FROM messages WHERE id=?", (source,)).fetchone()
        media = json.loads(message["payload"]).get("media") if message else None
        cursor = db.execute("INSERT INTO posts(source,fingerprint,day,kind,text,status,at,media_json) VALUES (?,?,?,?,?,?,?,?)",
                            (source, fp, day, kind, text, "dry_run" if cfg.dry else "sending", stamp(now),
                             json.dumps(media) if media else None))
        post_id = cursor.lastrowid
        db.commit()
    except Exception:
        db.rollback()
        raise
    if cfg.dry:
        LOG.info("SIMULACION post %s: %s", post_id, text)
        return
    try:
        publisher = publisher or (send_draft if cfg.delivery == "telegram_draft" else publish_x)
        x_id = publisher(text, media=media) if media else publisher(text)
        status, error = "draft_sent" if cfg.delivery == "telegram_draft" else "published", None
    except PublishFailure as exc:
        x_id, status, error = None, "failed" if 400 <= exc.status < 500 else "unknown", "HTTP " + str(exc.status)
    except Exception as exc:
        x_id, status, error = None, "unknown", type(exc).__name__
    with db:
        db.execute("UPDATE posts SET status=?,x_id=?,error=? WHERE id=?", (status, x_id, error, post_id))
    LOG.info("Post %s: %s %s", post_id, status, error or "")


def run(db, cfg):
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or not cfg.chat:
        raise ValueError("Configurar TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
    if not cfg.dry:
        if not cfg.authorized or "REEMPLAZAR" in cfg.cta:
            raise ValueError("Confirmar permiso y configurar CTA_URL antes de publicar")
        keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET") if cfg.delivery == "x_api" else ("TELEGRAM_REVIEW_CHAT_ID",)
        for key in keys:
            if not os.environ.get(key):
                raise ValueError("Falta " + key)
    LOG.info("Worker iniciado; simulacion=%s; zona=%s", cfg.dry, cfg.zone)
    while True:
        try:
            response = requests.post(f"https://api.telegram.org/bot{token}/getUpdates", json={
                "offset": int(getmeta(db, "offset", "0")), "timeout": 20, "limit": 100,
                "allowed_updates": ["message", "channel_post", "edited_message", "edited_channel_post"]}, timeout=(10, 30))
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise RuntimeError("Telegram rechazo getUpdates")
            updates = result["result"]
            now = datetime.now(UTC)
            with db:
                for update in updates:
                    ingest(db, cfg, update)
                    setmeta(db, "offset", update["update_id"] + 1)
                if len(updates) < 100:
                    record_poll(db, now)
            # Drain pending batches before deciding that no free pick exists.
            if len(updates) < 100:
                tick(db, cfg, now)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            # requests exception strings can contain Telegram tokens: never log them.
            LOG.error("Ciclo detenido (%s); revisar configuracion/conectividad. Reintento de lectura en 30s", type(exc).__name__)
            time.sleep(30)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("status")
    sub.add_parser("export")
    sub.add_parser("discover", help="Mostrar IDs recibidos; ejecutar con el worker detenido")
    ed = sub.add_parser("editorial", help="Agregar texto revisado manualmente")
    ed.add_argument("--file", required=True)
    ed.add_argument("--due", required=True)
    ed.add_argument("--expires", required=True)
    args = parser.parse_args()
    cfg = Config.env()
    db = connect(cfg.db)
    if args.command == "run":
        run(db, cfg)
    elif args.command == "discover":
        import requests
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("Falta TELEGRAM_BOT_TOKEN")
        try:
            result = requests.post(f"https://api.telegram.org/bot{token}/getUpdates",
                                   json={"timeout": 5}, timeout=(10, 15)).json()
            if not result.get("ok"):
                raise RuntimeError("Telegram rechazo la consulta")
            for update in result["result"]:
                msg = update.get("message") or update.get("channel_post") or {}
                print(json.dumps({"chat_id": msg.get("chat", {}).get("id"),
                                  "type": msg.get("chat", {}).get("type"),
                                  "sender_id": msg.get("from", {}).get("id")}, ensure_ascii=False))
        except Exception as exc:
            raise SystemExit("No se pudieron consultar IDs: " + type(exc).__name__) from None
    elif args.command == "status":
        print(json.dumps({"posts": [dict(r) for r in db.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 30")],
                          "image_bundles": [dict(r) for r in db.execute("SELECT * FROM bundle_state ORDER BY rowid DESC LIMIT 30")],
                          "pending_format": [dict(r) for r in db.execute("SELECT id,error FROM messages WHERE error IS NOT NULL ORDER BY created DESC LIMIT 30")]}, ensure_ascii=False, indent=2))
    elif args.command == "export":
        import sys
        rows = db.execute("SELECT * FROM posts ORDER BY id")
        writer = csv.writer(sys.stdout)
        writer.writerow([col[0] for col in rows.description])
        writer.writerows(rows)
    elif args.command == "editorial":
        text = Path(args.file).read_text(encoding="utf-8").strip()
        due, expires = parse_time(args.due), parse_time(args.expires)
        if not text or weighted_length(text) > 280 or "@" in text or expires <= due:
            raise ValueError("Texto vacio/largo, menciones automaticas o fechas invalidas")
        key = hashlib.sha256((text + stamp(due)).encode()).hexdigest()[:16]
        with db:
            db.execute("INSERT OR IGNORE INTO editorial VALUES (?,?,?,?)", (key, text, stamp(due), stamp(expires)))
        print("Editorial programado:", key)


if __name__ == "__main__":
    main()
