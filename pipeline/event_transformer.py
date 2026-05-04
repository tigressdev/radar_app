import re
from typing import Any, Dict


def _extract_numeric_id(value: str) -> str:
    """
    Extrai o sufixo numérico de IDs como posto_001.
    Se não encontrar número, retorna o próprio valor.
    """
    match = re.search(r"(\d+)$", value or "")
    return match.group(1) if match else value


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza eventos do domínio Radar Combustível.

    Eventos esperados:
        - view
        - search
        - price_update
        - rating
        - abastecimento
    """
    event_type = str(raw.get("type", "")).strip().lower()

    posto_id = str(raw.get("posto_id", "")).strip()
    posto_num = _extract_numeric_id(posto_id)

    ts = _safe_int(raw.get("ts"), 0)
    if ts <= 0:
        raise ValueError("Evento sem timestamp válido em milissegundos.")

    combustivel = str(raw.get("combustivel", "")).strip().lower()

    event = {
        "type": event_type,
        "ts": ts,
        "user_id": str(raw.get("user_id", "")).strip(),

        # Identificação do posto
        "posto_id": posto_id,
        "posto_num": posto_num,
        "posto_nome": str(raw.get("posto_nome", "")).strip(),
        "bandeira": str(raw.get("bandeira", "")).strip(),

        # Localização
        "bairro": str(raw.get("bairro", "")).strip(),
        "bairro_key": str(raw.get("bairro_key", raw.get("bairro", ""))).strip().lower(),
        "cidade": str(raw.get("cidade", "")).strip(),
        "cidade_key": str(raw.get("cidade_key", raw.get("cidade", ""))).strip().lower(),
        "estado": str(raw.get("estado", "")).strip(),
        "lat": _safe_float(raw.get("lat")),
        "lon": _safe_float(raw.get("lon")),

        # Combustível e preço
        "combustivel": combustivel,
        "preco": _safe_float(raw.get("preco")),
        "preco_anterior": _safe_float(raw.get("preco_anterior")),
        "delta_abs": _safe_float(raw.get("delta_abs")),
        "delta_pct": _safe_float(raw.get("delta_pct")),

        # Busca
        "termo": str(raw.get("termo", "")).strip(),
        "raio_km": _safe_float(raw.get("raio_km")),
        "resultados": _safe_int(raw.get("resultados")),
        "clicou": bool(raw.get("clicou", False)),

        # Avaliação
        "stars": _safe_float(raw.get("stars")),
        "comentario": raw.get("comentario"),

        # Abastecimento
        "litros": _safe_float(raw.get("litros")),
        "preco_unitario": _safe_float(raw.get("preco_unitario")),
        "valor_total": _safe_float(raw.get("valor_total")),
        "forma_pagamento": str(raw.get("forma_pagamento", "")).strip(),
        "cupom_aplicado": bool(raw.get("cupom_aplicado", False)),

        # Campos auxiliares
        "origem": str(raw.get("origem", "")).strip(),
        "fonte": str(raw.get("fonte", "")).strip(),
    }

    return event


def hash_key(event: Dict[str, Any]) -> str:
    """
    Hash principal do posto.
    Exemplo: posto:001
    """
    return f"posto:{event['posto_num']}"


def variation_hash_key(event: Dict[str, Any]) -> str:
    """
    Hash de variação de preço por posto e combustível.
    Exemplo: variacao:preco:001:gasolina_comum
    """
    return f"variacao:preco:{event['posto_num']}:{event['combustivel']}"


def ts_key(event: Dict[str, Any], metric: str) -> str:
    """
    Chave de série temporal.

    Exemplos:
        ts:posto:001:views
        ts:posto:001:preco:gasolina_comum
        ts:posto:001:abastecimentos
    """
    if metric == "preco":
        return f"ts:posto:{event['posto_num']}:preco:{event['combustivel']}"
    return f"ts:posto:{event['posto_num']}:{metric}"


def ranking_key(event: Dict[str, Any]) -> str:
    """
    Retorna a principal chave de ranking para cada tipo de evento.
    Algumas features usam rankings adicionais no consumer.
    """
    event_type = event["type"]

    if event_type == "view":
        return "ranking:postos:views"

    if event_type == "search":
        return "ranking:bairros:buscas"

    if event_type == "price_update":
        combustivel = event.get("combustivel", "")
        return f"ranking:postos:preco:{combustivel}"

    if event_type == "rating":
        return "ranking:postos:avaliacoes"

    if event_type == "abastecimento":
        return "ranking:postos:abastecimentos"

    return ""


def price_ranking_key(event: Dict[str, Any]) -> str:
    """
    Ranking de preço por combustível.
    Score = preço atual.
    Quanto menor o score, mais barato o posto.
    """
    return f"ranking:postos:preco:{event['combustivel']}"


def price_ranking_key_by_region(event: Dict[str, Any]) -> str:
    """
    Ranking de preço por cidade, bairro e combustível.
    Score = preço atual.
    """
    return (
        f"ranking:postos:preco:"
        f"{event['cidade_key']}:{event['bairro_key']}:{event['combustivel']}"
    )


def search_neighborhood_key() -> str:
    """
    Ranking de bairros por volume de buscas.
    """
    return "ranking:bairros:buscas"


def search_fuel_key() -> str:
    """
    Ranking de combustíveis mais buscados.
    """
    return "ranking:combustiveis:buscas"


def abastecimento_value_key() -> str:
    """
    Ranking de postos por valor total abastecido.
    """
    return "ranking:postos:valor_abastecido"


def geo_key(event: Dict[str, Any]) -> str:
    """
    Chave GEO por cidade.
    Exemplo: geo:postos:sao_paulo
    """
    cidade_key = event.get("cidade_key") or "geral"
    return f"geo:postos:{cidade_key}"


def redis_hash_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Campos resumidos para HSET posto:{id}.
    Mantém apenas valores simples aceitos pelo Redis.
    """
    return {
        "posto_id": event["posto_id"],
        "posto_nome": event["posto_nome"],
        "bandeira": event["bandeira"],
        "bairro": event["bairro"],
        "bairro_key": event["bairro_key"],
        "cidade": event["cidade"],
        "cidade_key": event["cidade_key"],
        "estado": event["estado"],
        "lat": event["lat"],
        "lon": event["lon"],
        "stars": event["stars"],
        "ultimo_evento": event["type"],
        "ultimo_ts": event["ts"],
    }


def price_hash_fields(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Campos específicos de preço para atualizar no Hash do posto.
    Exemplo:
        preco_gasolina_comum = 5.79
        ts_preco_gasolina_comum = 1710010203000
    """
    combustivel = event["combustivel"]
    if not combustivel:
        return {}

    return {
        f"preco_{combustivel}": event["preco"],
        f"ts_preco_{combustivel}": event["ts"],
    }


def variation_hash_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Payload do Hash de variação de preço.
    """
    return {
        "posto_id": event["posto_id"],
        "posto_nome": event["posto_nome"],
        "bandeira": event["bandeira"],
        "bairro": event["bairro"],
        "cidade": event["cidade"],
        "combustivel": event["combustivel"],
        "preco_atual": event["preco"],
        "preco_anterior": event["preco_anterior"],
        "delta_abs": event["delta_abs"],
        "delta_pct": event["delta_pct"],
        "ts": event["ts"],
        "fonte": event["fonte"],
    }
