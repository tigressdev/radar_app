import os
import re
import time
from datetime import datetime
from typing import List, Tuple

from dotenv import load_dotenv
from redis import Redis

# Load .env.local first for local development, fallback to .env.
load_dotenv(".env.local")
load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None


def get_redis() -> Redis:
    return Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


def extract_numeric_id(value: str) -> str:
    match = re.search(r"(\d+)$", value or "")
    return match.group(1) if match else value


def posto_name(redis: Redis, posto_id: str) -> str:
    numeric = extract_numeric_id(posto_id)
    name = redis.hget(f"posto:{numeric}", "posto_nome")
    return name or posto_id


def top_postos_views(redis: Redis, n: int = 10) -> List[Tuple[str, float]]:
    return redis.zrevrange("ranking:postos:views", 0, n - 1, withscores=True)


def postos_mais_baratos(redis: Redis, combustivel: str = "gasolina_comum", n: int = 10) -> List[Tuple[str, float]]:
    return redis.zrange(f"ranking:postos:preco:{combustivel}", 0, n - 1, withscores=True)


def bairros_mais_buscados(redis: Redis, n: int = 10) -> List[Tuple[str, float]]:
    return redis.zrevrange("ranking:bairros:buscas", 0, n - 1, withscores=True)


def combustiveis_mais_buscados(redis: Redis, n: int = 10) -> List[Tuple[str, float]]:
    return redis.zrevrange("ranking:combustiveis:buscas", 0, n - 1, withscores=True)


def top_abastecimentos(redis: Redis, n: int = 10) -> List[Tuple[str, float]]:
    return redis.zrevrange("ranking:postos:abastecimentos", 0, n - 1, withscores=True)


def serie_views(redis: Redis, posto_numeric_id: str = "001"):
    key = f"ts:posto:{posto_numeric_id}:views"
    return redis.execute_command("TS.RANGE", key, "-", "+", "AGGREGATION", "sum", "60000")


def serie_preco(redis: Redis, posto_numeric_id: str = "001", combustivel: str = "gasolina_comum"):
    key = f"ts:posto:{posto_numeric_id}:preco:{combustivel}"
    return redis.execute_command("TS.RANGE", key, "-", "+", "AGGREGATION", "avg", "60000")


def print_block(title: str) -> None:
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


def main() -> None:
    redis = get_redis()
    redis.ping()
    print("[READER] Radar Combustível — consultas Redis iniciadas.")

    while True:
        print_block("Top 10 postos mais visitados")
        for idx, (member, score) in enumerate(top_postos_views(redis), start=1):
            print(f"{idx:02d}. {posto_name(redis, member)} ({member}) -> {int(score)} views")

        print_block("Top 10 menores preços — gasolina comum")
        for idx, (member, score) in enumerate(postos_mais_baratos(redis, "gasolina_comum"), start=1):
            print(f"{idx:02d}. {posto_name(redis, member)} ({member}) -> R$ {float(score):.2f}/L")

        print_block("Bairros mais buscados")
        for idx, (member, score) in enumerate(bairros_mais_buscados(redis), start=1):
            print(f"{idx:02d}. {member} -> {int(score)} buscas")

        print_block("Combustíveis mais buscados")
        for idx, (member, score) in enumerate(combustiveis_mais_buscados(redis), start=1):
            print(f"{idx:02d}. {member} -> {int(score)} buscas")

        print_block("Postos com mais abastecimentos")
        for idx, (member, score) in enumerate(top_abastecimentos(redis), start=1):
            print(f"{idx:02d}. {posto_name(redis, member)} ({member}) -> {int(score)} abastecimentos")

        print_block("Série temporal de views do posto 001")
        try:
            rows = serie_views(redis, "001")
            if not rows:
                print("Sem dados para ts:posto:001:views.")
            else:
                for ts, value in rows[-10:]:
                    dt = datetime.fromtimestamp(int(ts) / 1000.0)
                    print(f"{dt} -> {value}")
        except Exception as exc:
            print(f"Falha na TimeSeries de views: {exc}")

        print_block("Série temporal de preço do posto 001 — gasolina comum")
        try:
            rows = serie_preco(redis, "001", "gasolina_comum")
            if not rows:
                print("Sem dados para ts:posto:001:preco:gasolina_comum.")
            else:
                for ts, value in rows[-10:]:
                    dt = datetime.fromtimestamp(int(ts) / 1000.0)
                    print(f"{dt} -> R$ {float(value):.2f}")
        except Exception as exc:
            print(f"Falha na TimeSeries de preço: {exc}")

        time.sleep(5)


if __name__ == "__main__":
    main()
