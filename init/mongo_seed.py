"""
mongo_seed.py — Radar Combustível
Popula o MongoDB com postos de combustível e eventos fake.

Uso:
    python init/mongo_seed.py
    python init/mongo_seed.py --postos 50 --events 8000
    python init/mongo_seed.py --stress --events 2000
    python init/mongo_seed.py --reset

Este seed cria:
    - Collection postos
    - Collection events
    - Eventos: view, search, price_update, rating, abastecimento
    - Índices MongoDB, incluindo 2dsphere para localização
"""

import argparse
import os
import random
import time
from datetime import datetime, timedelta, timezone

from faker import Faker
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env.local", override=False)
    load_dotenv(dotenv_path=".env", override=False)
except ImportError:
    pass


# ─── Config ────────────────────────────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
MONGO_DB = os.getenv("RADAR_MONGO_DB", "radar_combustivel")  # força o banco correto do projeto

fake = Faker("pt_BR")
random.seed(42)


# ─── Dados de domínio ──────────────────────────────────────────────────────────

COMBUSTIVEIS = [
    "gasolina_comum",
    "gasolina_aditivada",
    "etanol",
    "diesel",
    "diesel_s10",
    "gnv",
]

BANDEIRAS = [
    "Ipiranga",
    "Shell",
    "Petrobras BR",
    "Raízen",
    "Vibra",
    "Ale",
    "Independente",
    "Repsol",
    "Branca",
]

# bairro, cidade, estado, lat_centro, lon_centro
REGIOES = [
    ("Pinheiros", "São Paulo", "SP", -23.5634, -46.6830),
    ("Vila Madalena", "São Paulo", "SP", -23.5553, -46.6893),
    ("Itaim Bibi", "São Paulo", "SP", -23.5861, -46.6792),
    ("Moema", "São Paulo", "SP", -23.6014, -46.6641),
    ("Perdizes", "São Paulo", "SP", -23.5365, -46.6614),
    ("Lapa", "São Paulo", "SP", -23.5250, -46.7058),
    ("Santo André", "Santo André", "SP", -23.6639, -46.5338),
    ("São Bernardo", "São Bernardo do Campo", "SP", -23.6940, -46.5650),
    ("Guarulhos", "Guarulhos", "SP", -23.4628, -46.5333),
    ("Osasco", "Osasco", "SP", -23.5325, -46.7916),
    ("Diadema", "Diadema", "SP", -23.6863, -46.6225),
    ("Mauá", "Mauá", "SP", -23.6677, -46.4614),
    ("Carapicuíba", "Carapicuíba", "SP", -23.5219, -46.8363),
]

PRECOS_BASE = {
    "gasolina_comum": 5.89,
    "gasolina_aditivada": 6.19,
    "etanol": 3.99,
    "diesel": 6.29,
    "diesel_s10": 6.49,
    "gnv": 4.89,
}

TIPOS_EVENTO = ["view", "search", "price_update", "rating", "abastecimento"]
PESOS_EVENTO = [0.40, 0.25, 0.15, 0.10, 0.10]

TERMOS_BUSCA = [
    "gasolina barata",
    "etanol perto de mim",
    "diesel S10",
    "melhor preço combustível",
    "posto 24h",
    "GNV",
    "gasolina aditivada",
    "posto shell",
    "abastecimento",
    "combustível mais barato",
    "posto petrobras",
    "etanol ou gasolina",
    "diesel comum",
    "posto lavagem",
    "ipiranga mais próximo",
    "gasolina aditivada preço",
    "posto bandeira branca",
    "posto aberto agora",
]

COMENTARIOS = [
    "Ótimo atendimento!",
    "Preço justo e rápido.",
    "Fila grande mas valeu.",
    "Sempre confiável.",
    "Poderia ser mais barato.",
    "Boa localização.",
    "Frentista muito atencioso.",
    "Prefiro outros postos.",
    "Gasolina de qualidade.",
    "Rápido e eficiente.",
    "Preço acima da média.",
    "Bom custo-benefício.",
    None,
    None,
]


# ─── Helpers ───────────────────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def jitter(lat: float, lon: float, radius_km: float = 2.5) -> tuple[float, float]:
    """Adiciona ruído geográfico aproximado dentro de radius_km."""
    delta = radius_km / 111.0
    return (
        round(lat + random.uniform(-delta, delta), 6),
        round(lon + random.uniform(-delta, delta), 6),
    )


def preco_variado(combustivel: str, desvio: float = 0.40) -> float:
    """Retorna preço base com variação realista entre postos."""
    base = PRECOS_BASE[combustivel]
    return round(max(0.01, base + random.uniform(-desvio, desvio)), 2)


def ts_aleatorio(dias_atras: int = 30) -> tuple[int, datetime, str]:
    """Retorna timestamp Unix ms, datetime UTC e ISO string."""
    delta = timedelta(
        days=random.randint(0, dias_atras),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    dt = utc_now() - delta
    ts_ms = int(dt.timestamp() * 1000)
    return ts_ms, dt, dt.isoformat()


def normalizar_valor(valor: str) -> str:
    return valor.lower().replace(" ", "_").replace("ã", "a").replace("é", "e")


# ─── Geração de postos ─────────────────────────────────────────────────────────

def gerar_postos(n: int = 30) -> list[dict]:
    postos = []

    for i in range(1, n + 1):
        bairro, cidade, estado, lat_centro, lon_centro = random.choice(REGIOES)
        lat, lon = jitter(lat_centro, lon_centro)
        bandeira = random.choice(BANDEIRAS)
        combustiveis = random.sample(COMBUSTIVEIS, k=random.randint(2, len(COMBUSTIVEIS)))

        posto = {
            "posto_id": f"posto_{i:03d}",
            "nome": f"Posto {bandeira} {fake.last_name()}",
            "bandeira": bandeira,
            "cnpj": fake.cnpj(),
            "bairro": bairro,
            "bairro_key": normalizar_valor(bairro),
            "cidade": cidade,
            "cidade_key": normalizar_valor(cidade),
            "estado": estado,
            "endereco": fake.street_address(),
            "lat": lat,
            "lon": lon,
            "location": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "telefone": fake.phone_number(),
            "aberto_24h": random.random() > 0.6,
            "servicos": random.sample(
                ["lavagem", "troca_oleo", "calibragem", "loja", "borracharia"],
                k=random.randint(0, 3),
            ),
            "combustiveis": combustiveis,
            "precos": {c: preco_variado(c) for c in combustiveis},
            "stars": round(random.uniform(2.5, 5.0), 1),
            "total_avaliacoes": random.randint(10, 900),
            "created_at": utc_now(),
        }

        postos.append(posto)

    return postos


# ─── Geração de eventos ────────────────────────────────────────────────────────

def gerar_evento(postos: list[dict]) -> dict:
    posto = random.choice(postos)
    tipo = random.choices(TIPOS_EVENTO, weights=PESOS_EVENTO, k=1)[0]
    ts, ts_dt, ts_iso = ts_aleatorio()
    user_id = f"usr_{random.randint(1, 5000):04d}"

    base = {
        "type": tipo,
        "ts": ts,
        "ts_dt": ts_dt,
        "ts_iso": ts_iso,
        "user_id": user_id,
        "posto_id": posto["posto_id"],
        "posto_nome": posto["nome"],
        "bandeira": posto["bandeira"],
        "bairro": posto["bairro"],
        "bairro_key": posto["bairro_key"],
        "cidade": posto["cidade"],
        "cidade_key": posto["cidade_key"],
        "estado": posto["estado"],
        "lat": posto["lat"],
        "lon": posto["lon"],
        "location": posto["location"],
    }

    if tipo == "view":
        combustivel = random.choice(posto["combustiveis"])
        base.update({
            "combustivel": combustivel,
            "preco": posto["precos"][combustivel],
            "origem": random.choice(["busca", "mapa", "favoritos", "notificacao", "direto"]),
            "sessao_id": f"sess_{random.randint(1000, 9999)}",
            "duracao_seg": random.randint(3, 120),
        })

    elif tipo == "search":
        combustivel = random.choice(COMBUSTIVEIS)
        base.update({
            "termo": random.choice(TERMOS_BUSCA),
            "combustivel": combustivel,
            "raio_km": random.choice([1, 2, 5, 10, 20]),
            "resultados": random.randint(1, 30),
            "clicou": random.random() > 0.4,
        })

    elif tipo == "price_update":
        combustivel = random.choice(posto["combustiveis"])
        preco_anterior = posto["precos"][combustivel]
        preco_novo = preco_variado(combustivel, desvio=0.30)
        delta_pct = round(((preco_novo - preco_anterior) / preco_anterior) * 100, 2)

        base.update({
            "combustivel": combustivel,
            "preco": preco_novo,
            "preco_anterior": preco_anterior,
            "delta_abs": round(preco_novo - preco_anterior, 2),
            "delta_pct": delta_pct,
            "fonte": random.choice(["app_posto", "usuario", "parceiro_anp", "admin"]),
        })

        # Atualiza preço em memória para próximos eventos do mesmo posto.
        posto["precos"][combustivel] = preco_novo

    elif tipo == "rating":
        combustivel = random.choice(posto["combustiveis"])
        stars = round(random.uniform(1.0, 5.0), 1)

        base.update({
            "stars": stars,
            "comentario": random.choice(COMENTARIOS),
            "combustivel": combustivel,
            "preco_pago": posto["precos"][combustivel],
        })

        # Atualiza média em memória de forma aproximada.
        n = posto["total_avaliacoes"]
        posto["stars"] = round((posto["stars"] * n + stars) / (n + 1), 1)
        posto["total_avaliacoes"] += 1

    elif tipo == "abastecimento":
        combustivel = random.choice(posto["combustiveis"])
        litros = round(random.uniform(15, 70), 2)
        preco_unitario = posto["precos"][combustivel]

        base.update({
            "combustivel": combustivel,
            "litros": litros,
            "preco_unitario": preco_unitario,
            "valor_total": round(litros * preco_unitario, 2),
            "forma_pagamento": random.choice(["credito", "debito", "pix", "dinheiro"]),
            "cupom_aplicado": random.random() > 0.75,
        })

    return base


# ─── Índices MongoDB ───────────────────────────────────────────────────────────

def criar_indices(db) -> None:
    print("[INDEX] Criando índices em 'postos'...")

    db.postos.create_index([("posto_id", ASCENDING)], unique=True, name="idx_posto_id")
    db.postos.create_index([("bairro", ASCENDING)], name="idx_bairro")
    db.postos.create_index([("cidade", ASCENDING)], name="idx_cidade")
    db.postos.create_index([("bandeira", ASCENDING)], name="idx_bandeira")
    db.postos.create_index([("combustiveis", ASCENDING)], name="idx_combustiveis")
    db.postos.create_index([("location", "2dsphere")], name="idx_location_2dsphere")

    print("[INDEX] Criando índices em 'events'...")

    db.events.create_index([("posto_id", ASCENDING), ("ts", DESCENDING)], name="idx_posto_ts")
    db.events.create_index([("type", ASCENDING), ("ts", DESCENDING)], name="idx_type_ts")
    db.events.create_index([("bairro", ASCENDING), ("type", ASCENDING)], name="idx_bairro_type")
    db.events.create_index([("cidade", ASCENDING), ("type", ASCENDING)], name="idx_cidade_type")
    db.events.create_index([("combustivel", ASCENDING), ("type", ASCENDING)], name="idx_comb_type")
    db.events.create_index([("ts", DESCENDING)], name="idx_ts")
    db.events.create_index([("location", "2dsphere")], name="idx_events_location_2dsphere")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Radar Combustível — seed MongoDB")
    parser.add_argument("--postos", type=int, default=30, help="Número de postos")
    parser.add_argument("--events", type=int, default=5000, help="Número de eventos")
    parser.add_argument("--stress", action="store_true", help="Insere eventos extras sem limpar a base")
    parser.add_argument("--reset", action="store_true", help="Limpa collections antes do seed")
    parser.add_argument("--batch", type=int, default=500, help="Tamanho do batch de inserção")
    args = parser.parse_args()

    sep = "─" * 64
    print(f"\n{sep}")
    print("  Radar Combustível — MongoDB Seed")
    print(f"  URI : {MONGO_URI}")
    print(f"  DB  : {MONGO_DB}")
    if os.getenv("MONGO_DB") and os.getenv("MONGO_DB") != MONGO_DB:
        print(f"  OBS : ignorando MONGO_DB={os.getenv('MONGO_DB')} e usando RADAR_MONGO_DB/default")
    print(f"{sep}\n")

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        print("[OK] Conectado ao MongoDB\n")
    except ConnectionFailure as exc:
        print(f"[ERRO] Não foi possível conectar ao MongoDB: {exc}")
        print("       Verifique se o Docker está rodando: docker compose up -d")
        raise SystemExit(1)

    db = client[MONGO_DB]

    if args.reset:
        print("[RESET] Removendo collections antigas...")
        db.postos.drop()
        db.events.drop()
        db.eventos.drop()  # remove versão antiga, caso exista
        print("[RESET] Collections removidas.\n")

    if args.stress:
        postos = list(db.postos.find({}, {"_id": 0}))
        if not postos:
            print("[AVISO] Nenhum posto encontrado. Gerando postos antes do stress...")
            postos = gerar_postos(args.postos)
            db.postos.insert_many(postos)
        print(f"[STRESS] Usando {len(postos)} postos existentes.\n")
    else:
        print(f"[SEED] Gerando {args.postos} postos...")
        postos = gerar_postos(args.postos)
        db.postos.drop()
        db.postos.insert_many(postos)
        print(f"       {len(postos)} postos inseridos.\n")

    print(f"[SEED] Gerando {args.events} eventos em 'events' (batch={args.batch})...")
    t0 = time.time()
    batch = []
    total = 0

    for _ in range(args.events):
        batch.append(gerar_evento(postos))

        if len(batch) >= args.batch:
            db.events.insert_many(batch)
            total += len(batch)
            batch = []
            elapsed = time.time() - t0
            pct = (total / args.events) * 100
            print(f"       {total:>5}/{args.events} ({pct:5.1f}%) {elapsed:.1f}s")

    if batch:
        db.events.insert_many(batch)
        total += len(batch)

    elapsed = max(time.time() - t0, 0.001)
    print(f"\n[OK] {total} eventos inseridos em {elapsed:.2f}s ({total / elapsed:.0f} ev/s)\n")

    criar_indices(db)
    print("[OK] Índices criados.\n")

    print(sep)
    print(f"  postos : {db.postos.count_documents({})}")
    print(f"  events : {db.events.count_documents({})}")
    print()
    print("  Distribuição por tipo de evento:")
    for item in db.events.aggregate([
        {"$group": {"_id": "$type", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"    ├─ {item['_id']:<18} {item['n']:>5}")

    print(sep)
    print("\n[PRONTO] Próximo passo:")
    print("  python pipeline/mongodb_consumer.py\n")


if __name__ == "__main__":
    main()
