"""
realtime_event_generator.py — Radar Combustível

Insere novos eventos randômicos no MongoDB em intervalo contínuo.
O consumer MongoDB Change Stream captura esses inserts e atualiza o Redis em tempo quase real.

Uso:
    python pipeline/realtime_event_generator.py
    python pipeline/realtime_event_generator.py --interval 120
    python pipeline/realtime_event_generator.py --interval 10 --batch-size 3
    python pipeline/realtime_event_generator.py --once --batch-size 5
"""

import argparse
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(".env.local")
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
MONGO_DB = os.getenv("RADAR_MONGO_DB", "radar_combustivel")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "events")

COMBUSTIVEIS = [
    "gasolina_comum",
    "gasolina_aditivada",
    "etanol",
    "diesel",
    "diesel_s10",
    "gnv",
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
PESOS_EVENTO = [0.35, 0.25, 0.18, 0.10, 0.12]

TERMOS_BUSCA = [
    "gasolina barata",
    "etanol perto de mim",
    "diesel S10",
    "menor preço combustível",
    "posto 24h",
    "GNV",
    "gasolina aditivada",
    "posto shell",
    "posto petrobras",
    "posto bandeira branca",
    "posto aberto agora",
]

COMENTARIOS = [
    "Ótimo atendimento!",
    "Preço justo.",
    "Fila grande.",
    "Boa localização.",
    "Poderia ser mais barato.",
    "Combustível de qualidade.",
    "Rápido e eficiente.",
    "Bom custo-benefício.",
    None,
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_ts() -> Tuple[int, datetime, str]:
    dt = utc_now()
    return int(dt.timestamp() * 1000), dt, dt.isoformat()


def preco_variado(combustivel: str, preco_atual: float | None = None, desvio: float = 0.18) -> float:
    base = float(preco_atual or PRECOS_BASE.get(combustivel, 5.0))
    return round(max(0.01, base + random.uniform(-desvio, desvio)), 2)


def load_postos(db) -> List[Dict]:
    postos = list(db.postos.find({}, {"_id": 0}))
    if not postos:
        raise RuntimeError(
            "Nenhum posto encontrado em radar_combustivel.postos. Rode primeiro: python mongo_seed_v2.py --reset"
        )
    return postos


def gerar_evento(postos: List[Dict], db) -> Dict:
    posto = random.choice(postos)
    tipo = random.choices(TIPOS_EVENTO, weights=PESOS_EVENTO, k=1)[0]
    ts, ts_dt, ts_iso = current_ts()

    base = {
        "type": tipo,
        "ts": ts,
        "ts_dt": ts_dt,
        "ts_iso": ts_iso,
        "user_id": f"usr_live_{random.randint(1, 9999):04d}",
        "posto_id": posto["posto_id"],
        "posto_nome": posto["nome"],
        "bandeira": posto.get("bandeira", ""),
        "bairro": posto.get("bairro", ""),
        "bairro_key": posto.get("bairro_key", ""),
        "cidade": posto.get("cidade", ""),
        "cidade_key": posto.get("cidade_key", ""),
        "estado": posto.get("estado", ""),
        "lat": posto.get("lat", 0),
        "lon": posto.get("lon", 0),
        "location": posto.get("location"),
        "source": "realtime_generator",
    }

    combustiveis_do_posto = posto.get("combustiveis") or COMBUSTIVEIS
    precos = posto.get("precos") or {}

    if tipo == "view":
        combustivel = random.choice(combustiveis_do_posto)
        base.update({
            "combustivel": combustivel,
            "preco": float(precos.get(combustivel, PRECOS_BASE.get(combustivel, 0))),
            "origem": random.choice(["busca", "mapa", "favoritos", "notificacao", "direto"]),
            "sessao_id": f"sess_live_{random.randint(1000, 9999)}",
            "duracao_seg": random.randint(3, 150),
        })

    elif tipo == "search":
        base.update({
            "termo": random.choice(TERMOS_BUSCA),
            "combustivel": random.choice(COMBUSTIVEIS),
            "raio_km": random.choice([1, 2, 5, 10, 20]),
            "resultados": random.randint(1, 30),
            "clicou": random.random() > 0.45,
        })

    elif tipo == "price_update":
        combustivel = random.choice(combustiveis_do_posto)
        preco_anterior = float(precos.get(combustivel, PRECOS_BASE.get(combustivel, 5.0)))
        preco_novo = preco_variado(combustivel, preco_anterior)
        delta_abs = round(preco_novo - preco_anterior, 2)
        delta_pct = round((delta_abs / preco_anterior) * 100, 2) if preco_anterior else 0.0

        base.update({
            "combustivel": combustivel,
            "preco": preco_novo,
            "preco_anterior": preco_anterior,
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
            "fonte": random.choice(["app_posto", "usuario", "parceiro_anp", "admin"]),
        })

        # Mantém o cadastro do posto atualizado também no Mongo.
        db.postos.update_one(
            {"posto_id": posto["posto_id"]},
            {"$set": {f"precos.{combustivel}": preco_novo}},
        )
        posto.setdefault("precos", {})[combustivel] = preco_novo

    elif tipo == "rating":
        combustivel = random.choice(combustiveis_do_posto)
        stars = round(random.uniform(1.0, 5.0), 1)
        base.update({
            "stars": stars,
            "comentario": random.choice(COMENTARIOS),
            "combustivel": combustivel,
            "preco_pago": float(precos.get(combustivel, PRECOS_BASE.get(combustivel, 0))),
        })

    elif tipo == "abastecimento":
        combustivel = random.choice(combustiveis_do_posto)
        litros = round(random.uniform(15, 70), 2)
        preco_unitario = float(precos.get(combustivel, PRECOS_BASE.get(combustivel, 0)))
        base.update({
            "combustivel": combustivel,
            "litros": litros,
            "preco_unitario": preco_unitario,
            "valor_total": round(litros * preco_unitario, 2),
            "forma_pagamento": random.choice(["credito", "debito", "pix", "dinheiro"]),
            "cupom_aplicado": random.random() > 0.75,
        })

    return base


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerador contínuo de eventos Radar Combustível.")
    parser.add_argument("--interval", type=int, default=120, help="Intervalo entre lotes em segundos. Default: 120.")
    parser.add_argument("--batch-size", type=int, default=1, help="Número de eventos por lote.")
    parser.add_argument("--once", action="store_true", help="Insere um lote e encerra.")
    args = parser.parse_args()

    mongo = MongoClient(MONGO_URI)
    mongo.admin.command("ping")
    db = mongo[MONGO_DB]
    col = db[MONGO_COLLECTION]
    postos = load_postos(db)

    print("[GENERATOR] Radar Combustível — gerador em tempo real")
    print(f"[GENERATOR] MongoDB: {MONGO_DB}.{MONGO_COLLECTION}")
    print(f"[GENERATOR] Intervalo: {args.interval}s | batch-size: {args.batch_size}")
    print("[GENERATOR] Ctrl+C para encerrar.\n")

    while True:
        eventos = [gerar_evento(postos, db) for _ in range(args.batch_size)]
        col.insert_many(eventos)

        for event in eventos:
            if event["type"] == "price_update":
                detail = f"{event['combustivel']} {event['preco_anterior']} -> {event['preco']}"
            elif event["type"] == "abastecimento":
                detail = f"{event['combustivel']} R$ {event['valor_total']}"
            elif event["type"] == "search":
                detail = f"{event['combustivel']} | {event['termo']}"
            else:
                detail = event.get("combustivel", "")

            print(
                f"[GENERATOR] {event['ts_iso']} | {event['type']:<14} | "
                f"{event['posto_id']} | {event['bairro']} | {detail}"
            )

        if args.once:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
