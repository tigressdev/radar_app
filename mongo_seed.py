"""
mongo_seed.py — Radar Combustível
Popula o MongoDB com postos de combustível e eventos fake.

Uso:
    python mongo_seed.py                        # seed padrão: 30 postos, 5000 eventos
    python mongo_seed.py --postos 50            # mais postos
    python mongo_seed.py --stress --events 2000 # carga extra sem limpar base
    python mongo_seed.py --reset                # limpa e re-seed completo
"""

import argparse
import random
import time
from datetime import datetime, timedelta

from faker import Faker
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure

import os

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env.local", override=False)
    load_dotenv(dotenv_path=".env", override=False)
except ImportError:
    pass

# ─── Config ────────────────────────────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
MONGO_DB  = os.getenv("MONGO_DB",  "radar_combustivel")

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
    "Ipiranga", "Shell", "Petrobras BR", "Raízen",
    "Vibra", "Ale", "Independente", "Repsol", "Branca",
]

# (bairro, lat_centro, lon_centro)
BAIRROS_SP = [
    ("Pinheiros",          -23.5634, -46.6830),
    ("Vila Madalena",      -23.5553, -46.6893),
    ("Itaim Bibi",         -23.5861, -46.6792),
    ("Moema",              -23.6014, -46.6641),
    ("Perdizes",           -23.5365, -46.6614),
    ("Lapa",               -23.5250, -46.7058),
    ("Santo André",        -23.6639, -46.5338),
    ("São Bernardo",       -23.6940, -46.5650),
    ("Guarulhos",          -23.4628, -46.5333),
    ("Osasco",             -23.5325, -46.7916),
    ("ABC Paulista",       -23.6780, -46.5590),
    ("Taboão da Serra",    -23.6103, -46.7586),
    ("Diadema",            -23.6863, -46.6225),
    ("Mauá",               -23.6677, -46.4614),
    ("Carapicuíba",        -23.5219, -46.8363),
]

PRECOS_BASE = {
    "gasolina_comum":     5.89,
    "gasolina_aditivada": 6.19,
    "etanol":             3.99,
    "diesel":             6.29,
    "diesel_s10":         6.49,
    "gnv":                4.89,
}

TIPOS_EVENTO = ["view", "search", "price_update", "rating"]
PESOS_EVENTO = [0.45,   0.30,     0.15,           0.10]

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
    "frentista",
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

def _jitter(lat: float, lon: float, radius_km: float = 2.5) -> tuple:
    """Adiciona ruído geográfico dentro de radius_km."""
    delta = radius_km / 111.0
    return (
        round(lat + random.uniform(-delta, delta), 6),
        round(lon + random.uniform(-delta, delta), 6),
    )


def _preco_variado(combustivel: str, desvio: float = 0.40) -> float:
    """Retorna preço base com variação realista entre postos."""
    base = PRECOS_BASE[combustivel]
    return round(base + random.uniform(-desvio, desvio), 3)


def _ts_aleatorio(dias_atras: int = 30) -> int:
    """Retorna timestamp Unix ms dentro dos últimos N dias."""
    agora = datetime.utcnow()
    delta = timedelta(
        days=random.randint(0, dias_atras),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return int((agora - delta).timestamp() * 1000)


# ─── Geração de postos ─────────────────────────────────────────────────────────

def gerar_postos(n: int = 30) -> list:
    postos = []
    for i in range(1, n + 1):
        bairro_nome, lat_centro, lon_centro = random.choice(BAIRROS_SP)
        lat, lon = _jitter(lat_centro, lon_centro)
        bandeira         = random.choice(BANDEIRAS)
        comb_ofertados   = random.sample(COMBUSTIVEIS, k=random.randint(2, len(COMBUSTIVEIS)))

        posto = {
            "posto_id":          f"posto_{i:03d}",
            "nome":              f"Posto {bandeira} {fake.last_name()}",
            "bandeira":          bandeira,
            "cnpj":              fake.cnpj(),
            "bairro":            bairro_nome,
            "cidade":            "São Paulo",
            "estado":            "SP",
            "endereco":          fake.street_address(),
            "lat":               lat,
            "lon":               lon,
            "telefone":          fake.phone_number(),
            "aberto_24h":        random.random() > 0.6,
            "servicos":          random.sample(
                                     ["lavagem", "troca_oleo", "calibragem", "loja", "borracharia"],
                                     k=random.randint(0, 3),
                                 ),
            "combustiveis":      comb_ofertados,
            "precos":            {c: _preco_variado(c) for c in comb_ofertados},
            "stars":             round(random.uniform(2.5, 5.0), 1),
            "total_avaliacoes":  random.randint(10, 900),
            "created_at":        datetime.utcnow(),
        }
        postos.append(posto)
    return postos


# ─── Geração de eventos ────────────────────────────────────────────────────────

def gerar_evento(postos: list) -> dict:
    posto   = random.choice(postos)
    tipo    = random.choices(TIPOS_EVENTO, weights=PESOS_EVENTO, k=1)[0]
    ts      = _ts_aleatorio()
    user_id = f"usr_{random.randint(1, 5000):04d}"

    base = {
        "type":       tipo,
        "ts":         ts,
        "ts_iso":     datetime.utcfromtimestamp(ts / 1000).isoformat(),
        "user_id":    user_id,
        "posto_id":   posto["posto_id"],
        "posto_nome": posto["nome"],
        "bandeira":   posto["bandeira"],
        "bairro":     posto["bairro"],
        "cidade":     posto["cidade"],
        "lat":        posto["lat"],
        "lon":        posto["lon"],
    }

    if tipo == "view":
        combustivel = random.choice(posto["combustiveis"])
        base.update({
            "combustivel": combustivel,
            "preco":       posto["precos"][combustivel],
            "origem":      random.choice(["busca", "mapa", "favoritos", "notificacao", "direto"]),
            "sessao_id":   f"sess_{random.randint(1000, 9999)}",
            "duracao_seg": random.randint(3, 120),
        })

    elif tipo == "search":
        base.update({
            "termo":       random.choice(TERMOS_BUSCA),
            "combustivel": random.choice(COMBUSTIVEIS),
            "raio_km":     random.choice([1, 2, 5, 10, 20]),
            "resultados":  random.randint(1, 30),
            "clicou":      random.random() > 0.4,
        })

    elif tipo == "price_update":
        combustivel = random.choice(posto["combustiveis"])
        preco_ant   = posto["precos"][combustivel]
        preco_novo  = _preco_variado(combustivel, desvio=0.30)
        delta_pct   = round(((preco_novo - preco_ant) / preco_ant) * 100, 2)

        base.update({
            "combustivel":    combustivel,
            "preco":          preco_novo,
            "preco_anterior": preco_ant,
            "delta_pct":      delta_pct,
            "fonte":          random.choice(["app_posto", "usuario", "parceiro_anp", "admin"]),
        })
        # atualiza preço em memória para próximos eventos do mesmo posto
        posto["precos"][combustivel] = preco_novo

    elif tipo == "rating":
        combustivel = random.choice(posto["combustiveis"])
        stars       = round(random.uniform(1.0, 5.0), 1)
        base.update({
            "stars":       stars,
            "comentario":  random.choice(COMENTARIOS),
            "combustivel": combustivel,
            "preco_pago":  posto["precos"][combustivel],
        })
        # atualiza média do posto em memória (aproximado)
        n = posto["total_avaliacoes"]
        posto["stars"] = round((posto["stars"] * n + stars) / (n + 1), 1)
        posto["total_avaliacoes"] += 1

    return base


# ─── Índices MongoDB ───────────────────────────────────────────────────────────

def criar_indices(db):
    print("[INDEX] Criando índices em 'postos'...")
    db.postos.create_index([("posto_id", ASCENDING)], unique=True, name="idx_posto_id")
    db.postos.create_index([("bairro", ASCENDING)],                name="idx_bairro")
    db.postos.create_index([("combustiveis", ASCENDING)],          name="idx_combustiveis")
    db.postos.create_index([("lat", ASCENDING), ("lon", ASCENDING)], name="idx_geo")

    print("[INDEX] Criando índices em 'eventos'...")
    db.eventos.create_index([("posto_id", ASCENDING), ("ts", DESCENDING)],    name="idx_posto_ts")
    db.eventos.create_index([("type", ASCENDING),     ("ts", DESCENDING)],    name="idx_type_ts")
    db.eventos.create_index([("bairro", ASCENDING),   ("type", ASCENDING)],   name="idx_bairro_type")
    db.eventos.create_index([("combustivel", ASCENDING), ("type", ASCENDING)],name="idx_comb_type")
    db.eventos.create_index([("ts", DESCENDING)],                             name="idx_ts")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Radar Combustível — seed MongoDB")
    parser.add_argument("--postos", type=int, default=30,   help="Número de postos (default: 30)")
    parser.add_argument("--events", type=int, default=5000, help="Número de eventos (default: 5000)")
    parser.add_argument("--stress", action="store_true",    help="Insere eventos extras SEM limpar a base")
    parser.add_argument("--reset",  action="store_true",    help="Limpa toda a base antes do seed")
    parser.add_argument("--batch",  type=int, default=500,  help="Tamanho do batch de inserção (default: 500)")
    args = parser.parse_args()

    sep = "─" * 56
    print(f"\n{sep}")
    print(f"  Radar Combustível — MongoDB Seed")
    print(f"  URI : {MONGO_URI}")
    print(f"  DB  : {MONGO_DB}")
    print(f"{sep}\n")

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        print("[OK] Conectado ao MongoDB\n")
    except ConnectionFailure as e:
        print(f"[ERRO] Não foi possível conectar: {e}")
        print("       Verifique: docker-compose up -d")
        raise SystemExit(1)

    db = client[MONGO_DB]

    if args.reset:
        db.postos.drop()
        db.eventos.drop()
        print("[RESET] Collections removidas.\n")

    # ── Postos ──────────────────────────────────────────────
    if not args.stress:
        print(f"[SEED] Gerando {args.postos} postos...")
        postos = gerar_postos(args.postos)
        db.postos.drop()
        db.postos.insert_many(postos)
        print(f"       {len(postos)} postos inseridos.\n")
    else:
        postos = list(db.postos.find({}, {"_id": 0}))
        if not postos:
            print("[AVISO] Sem postos na base — gerando agora...")
            postos = gerar_postos(args.postos)
            db.postos.insert_many(postos)
        print(f"[STRESS] Usando {len(postos)} postos existentes.\n")

    # ── Eventos ─────────────────────────────────────────────
    print(f"[SEED] Gerando {args.events} eventos (batch={args.batch})...")
    t0    = time.time()
    batch = []
    total = 0

    for _ in range(args.events):
        batch.append(gerar_evento(postos))
        if len(batch) >= args.batch:
            db.eventos.insert_many(batch)
            total += len(batch)
            batch = []
            elapsed = time.time() - t0
            pct = (total / args.events) * 100
            print(f"       {total:>5}/{args.events}  ({pct:5.1f}%)  {elapsed:.1f}s")

    if batch:
        db.eventos.insert_many(batch)
        total += len(batch)

    elapsed = time.time() - t0
    print(f"\n[OK] {total} eventos em {elapsed:.2f}s  ({total/elapsed:.0f} ev/s)\n")

    # ── Índices ─────────────────────────────────────────────
    criar_indices(db)
    print("[OK] Índices criados.\n")

    # ── Resumo ──────────────────────────────────────────────
    print(sep)
    print(f"  postos   : {db.postos.count_documents({})}")
    print(f"  eventos  : {db.eventos.count_documents({})}")
    print()
    for t in db.eventos.aggregate([
        {"$group": {"_id": "$type", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"    ├─ {t['_id']:<22} {t['n']:>5}")
    print(sep)
    print("\n[PRONTO] Execute o pipeline:\n")
    print("  python pipeline/mongodb_consumer.py\n")


if __name__ == "__main__":
    main()
