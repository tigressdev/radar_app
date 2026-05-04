import argparse
import os
import time
from typing import Any, Dict

from dotenv import load_dotenv
from pymongo import MongoClient
from redis import Redis
from redis.exceptions import ResponseError

from event_transformer import (
    abastecimento_value_key,
    geo_key,
    hash_key,
    normalize_event,
    price_hash_fields,
    price_ranking_key,
    price_ranking_key_by_region,
    redis_hash_payload,
    search_fuel_key,
    search_neighborhood_key,
    ts_key,
    variation_hash_key,
    variation_hash_payload,
)

# Load .env.local first for local development, fallback to .env.
load_dotenv(".env.local")
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
MONGO_DB = os.getenv("MONGO_DB", "radar_combustivel")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "events")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

VALID_EVENT_TYPES = {"view", "search", "price_update", "rating", "abastecimento"}


def ensure_ts_add(
    redis: Redis,
    key: str,
    ts: int,
    value: float,
    labels: Dict[str, str],
    retention_ms: int = 604800000,
) -> None:
    """
    Adds a point to RedisTimeSeries.

    If the key does not exist, creates it first.
    Default retention: 7 days.
    """
    try:
        redis.execute_command("TS.ADD", key, ts, value, "ON_DUPLICATE", "LAST")
    except ResponseError as exc:
        msg = str(exc)
        if "key does not exist" not in msg and "TSDB: the key does not exist" not in msg:
            raise

        label_args = []
        for k, v in labels.items():
            label_args.extend([k, v])

        redis.execute_command(
            "TS.CREATE",
            key,
            "RETENTION",
            retention_ms,
            "DUPLICATE_POLICY",
            "LAST",
            "LABELS",
            *label_args,
        )
        redis.execute_command("TS.ADD", key, ts, value, "ON_DUPLICATE", "LAST")


def apply_base_station(redis: Redis, event: Dict[str, Any]) -> None:
    """
    Updates the main station hash and GEO index.
    This runs for every event type because the event is denormalized.
    """
    r_hash = hash_key(event)
    redis.hset(r_hash, mapping=redis_hash_payload(event))

    # Native Redis GEO index by city.
    # redis-py supports geoadd(name, values) in recent versions.
    # values = (longitude, latitude, member)
    if event.get("lon") and event.get("lat") and event.get("posto_id"):
        try:
            redis.geoadd(geo_key(event), (event["lon"], event["lat"], event["posto_id"]))
        except TypeError:
            # Compatibility with older redis-py signatures.
            redis.execute_command(
                "GEOADD",
                geo_key(event),
                event["lon"],
                event["lat"],
                event["posto_id"],
            )


def apply_view(redis: Redis, event: Dict[str, Any]) -> None:
    r_hash = hash_key(event)

    score = redis.zincrby("ranking:postos:views", 1, event["posto_id"])
    redis.hincrby(r_hash, "views", 1)

    # Optional: views by fuel and neighborhood for richer dashboards.
    if event.get("combustivel"):
        redis.zincrby(f"ranking:postos:views:{event['combustivel']}", 1, event["posto_id"])
    if event.get("bairro_key"):
        redis.zincrby(f"ranking:bairros:views", 1, event["bairro"])

    ensure_ts_add(
        redis,
        ts_key(event, "views"),
        event["ts"],
        1,
        {
            "posto_id": event["posto_num"],
            "metric": "views",
            "cidade": event.get("cidade_key", ""),
            "bairro": event.get("bairro_key", ""),
        },
    )

    print(f"[REDIS] ZINCRBY ranking:postos:views 1 {event['posto_id']} -> score: {int(float(score))}")


def apply_search(redis: Redis, event: Dict[str, Any]) -> None:
    # Searches are about demand, so the member is bairro/fuel, not station.
    bairro = event.get("bairro") or event.get("bairro_key") or "desconhecido"
    combustivel = event.get("combustivel") or "desconhecido"

    score_bairro = redis.zincrby(search_neighborhood_key(), 1, bairro)
    score_fuel = redis.zincrby(search_fuel_key(), 1, combustivel)

    # Also count searches in the station hash because each event carries a station.
    redis.hincrby(hash_key(event), "searches", 1)

    print(
        f"[REDIS] ZINCRBY {search_neighborhood_key()} 1 {bairro} -> score: {int(float(score_bairro))}"
    )
    print(
        f"[REDIS] ZINCRBY {search_fuel_key()} 1 {combustivel} -> score: {int(float(score_fuel))}"
    )


def apply_price_update(redis: Redis, event: Dict[str, Any]) -> None:
    r_hash = hash_key(event)
    combustivel = event.get("combustivel")
    preco = float(event.get("preco") or 0)

    if not combustivel or preco <= 0:
        print(f"[WARN] price_update ignorado por combustível/preço inválido: {event}")
        return

    # Updates station hash with current price.
    redis.hset(r_hash, mapping=price_hash_fields(event))

    # Global price ranking by fuel. Lower score = cheaper.
    redis.zadd(price_ranking_key(event), {event["posto_id"]: preco})

    # Regional price ranking by city + neighborhood + fuel.
    redis.zadd(price_ranking_key_by_region(event), {event["posto_id"]: preco})

    # Price variation hash.
    redis.hset(variation_hash_key(event), mapping=variation_hash_payload(event))

    # Ranking by absolute variation percentage. Higher = larger movement.
    redis.zadd(
        f"ranking:postos:variacao:{combustivel}",
        {event["posto_id"]: abs(float(event.get("delta_pct") or 0))},
    )

    # Time series stores actual price as R$/L.
    ensure_ts_add(
        redis,
        ts_key(event, "preco"),
        event["ts"],
        preco,
        {
            "posto_id": event["posto_num"],
            "metric": "preco",
            "combustivel": combustivel,
            "cidade": event.get("cidade_key", ""),
            "bairro": event.get("bairro_key", ""),
        },
    )

    print(f"[REDIS] ZADD {price_ranking_key(event)} {preco} {event['posto_id']}")
    print(f"[REDIS] HSET {variation_hash_key(event)} delta_pct {event.get('delta_pct')}")


def apply_rating(redis: Redis, event: Dict[str, Any]) -> None:
    r_hash = hash_key(event)
    stars = float(event.get("stars") or 0)

    if stars <= 0:
        print(f"[WARN] rating ignorado por stars inválido: {event}")
        return

    redis.hincrbyfloat(r_hash, "rating_sum", stars)
    redis.hincrby(r_hash, "rating_count", 1)

    rating_sum = float(redis.hget(r_hash, "rating_sum") or 0.0)
    rating_count = int(redis.hget(r_hash, "rating_count") or 1)
    avg = round(rating_sum / max(rating_count, 1), 2)

    redis.hset(r_hash, "stars", avg)
    redis.zadd("ranking:postos:avaliacoes", {event["posto_id"]: avg})

    print(f"[REDIS] HSET {r_hash} stars {avg}")


def apply_abastecimento(redis: Redis, event: Dict[str, Any]) -> None:
    r_hash = hash_key(event)
    valor_total = float(event.get("valor_total") or 0)
    litros = float(event.get("litros") or 0)
    combustivel = event.get("combustivel") or "desconhecido"

    score_count = redis.zincrby("ranking:postos:abastecimentos", 1, event["posto_id"])
    redis.zincrby(abastecimento_value_key(), valor_total, event["posto_id"])
    redis.zincrby(f"ranking:combustiveis:abastecimentos", litros, combustivel)

    redis.hincrby(r_hash, "abastecimentos", 1)
    redis.hincrbyfloat(r_hash, "litros_total", litros)
    redis.hincrbyfloat(r_hash, "valor_total_abastecido", valor_total)

    ensure_ts_add(
        redis,
        ts_key(event, "abastecimentos"),
        event["ts"],
        1,
        {
            "posto_id": event["posto_num"],
            "metric": "abastecimentos",
            "cidade": event.get("cidade_key", ""),
            "bairro": event.get("bairro_key", ""),
        },
    )

    ensure_ts_add(
        redis,
        ts_key(event, "valor_abastecido"),
        event["ts"],
        valor_total,
        {
            "posto_id": event["posto_num"],
            "metric": "valor_abastecido",
            "cidade": event.get("cidade_key", ""),
            "bairro": event.get("bairro_key", ""),
        },
    )

    print(
        f"[REDIS] ZINCRBY ranking:postos:abastecimentos 1 {event['posto_id']} -> score: {int(float(score_count))}"
    )


def apply_to_redis(redis: Redis, event: Dict[str, Any]) -> None:
    apply_base_station(redis, event)

    event_type = event["type"]

    if event_type == "view":
        apply_view(redis, event)
    elif event_type == "search":
        apply_search(redis, event)
    elif event_type == "price_update":
        apply_price_update(redis, event)
    elif event_type == "rating":
        apply_rating(redis, event)
    elif event_type == "abastecimento":
        apply_abastecimento(redis, event)


def handle_event(redis: Redis, raw_event: Dict[str, Any]) -> None:
    try:
        event = normalize_event(raw_event)
    except Exception as exc:
        print(f"[WARN] Evento inválido ignorado: {exc} | raw={raw_event}")
        return

    if event["type"] not in VALID_EVENT_TYPES:
        return

    if event["type"] == "search":
        print(
            f"[EVENT] search | bairro={event['bairro']} | combustivel={event['combustivel']} | termo={event['termo']}"
        )
    elif event["type"] == "price_update":
        print(
            f"[EVENT] price_update | {event['posto_id']} | {event['posto_nome']} | "
            f"{event['combustivel']} | {event['preco_anterior']} -> {event['preco']}"
        )
    elif event["type"] == "abastecimento":
        print(
            f"[EVENT] abastecimento | {event['posto_id']} | {event['combustivel']} | "
            f"R$ {event['valor_total']}"
        )
    else:
        print(f"[EVENT] {event['type']} | {event['posto_id']} | {event['posto_nome']} | {event['bairro']}")

    apply_to_redis(redis, event)


def backfill_existing(col, redis: Redis, limit: int = 50000) -> None:
    processed = 0
    for doc in col.find({}).sort("ts", 1).limit(limit):
        handle_event(redis, doc)
        processed += 1

    print(f"[CONSUMER] Backfill concluído: {processed} eventos.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consome eventos do MongoDB Change Stream e publica no Redis — Radar Combustível."
    )
    parser.add_argument("--skip-backfill", action="store_true", help="Não processa eventos já existentes.")
    parser.add_argument("--flush-redis", action="store_true", help="Limpa Redis antes de processar.")
    parser.add_argument("--limit", type=int, default=50000, help="Limite de eventos no backfill.")
    args = parser.parse_args()

    mongo = MongoClient(MONGO_URI)
    redis = Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )

    try:
        mongo.admin.command("ping")
        redis.ping()
    except Exception as exc:
        print(f"[ERRO] Falha ao conectar MongoDB/Redis: {exc}")
        raise SystemExit(1)

    col = mongo[MONGO_DB][MONGO_COLLECTION]

    print("[CONSUMER] Radar Combustível — MongoDB -> Redis")
    print(f"[CONSUMER] MongoDB: {MONGO_DB}.{MONGO_COLLECTION}")
    print(f"[CONSUMER] Redis: {REDIS_HOST}:{REDIS_PORT}")

    if args.flush_redis:
        redis.flushdb()
        print("[CONSUMER] Redis limpo com FLUSHDB.")

    if not args.skip_backfill:
        backfill_existing(col, redis, limit=args.limit)

    print("[CONSUMER] Conectado ao MongoDB Change Stream")
    print("[CONSUMER] Aguardando novos eventos...")

    while True:
        try:
            with col.watch([{"$match": {"operationType": "insert"}}], full_document="updateLookup") as stream:
                for change in stream:
                    handle_event(redis, change["fullDocument"])
        except KeyboardInterrupt:
            print("\n[CONSUMER] Encerrado pelo usuário.")
            break
        except Exception as exc:
            print(f"[CONSUMER] Reconectando após erro: {exc}")
            time.sleep(2)


if __name__ == "__main__":
    main()
