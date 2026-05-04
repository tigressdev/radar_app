import os
import re
import time
from datetime import datetime
from typing import Dict, List, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from redis import Redis

# Load .env.local first for local development, fallback to .env.
load_dotenv(".env.local")
load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None


# ──────────────────────────────────────────────────────────────────────────────
# Redis helpers
# ──────────────────────────────────────────────────────────────────────────────

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


def posto_hash_key(posto_id: str) -> str:
    return f"posto:{extract_numeric_id(posto_id)}"


def posto_info(redis: Redis, posto_id: str) -> Dict[str, str]:
    return redis.hgetall(posto_hash_key(posto_id)) or {}


def resolve_posto_names(redis: Redis, posto_ids: List[str]) -> Dict[str, str]:
    names = {}
    for posto_id in posto_ids:
        info = posto_info(redis, posto_id)
        names[posto_id] = info.get("posto_nome") or posto_id
    return names


def zset_to_df(
    redis: Redis,
    key: str,
    n: int = 10,
    reverse: bool = True,
    member_col: str = "member",
    score_col: str = "score",
) -> pd.DataFrame:
    if reverse:
        rows = redis.zrevrange(key, 0, n - 1, withscores=True)
    else:
        rows = redis.zrange(key, 0, n - 1, withscores=True)

    df = pd.DataFrame(rows, columns=[member_col, score_col])
    if not df.empty:
        df[score_col] = pd.to_numeric(df[score_col], errors="coerce")
    return df


def scan_keys(redis: Redis, pattern: str) -> List[str]:
    return sorted([k for k in redis.scan_iter(match=pattern)])


def ts_range(redis: Redis, key: str, aggregation: str = "sum", bucket_ms: int = 60000) -> pd.DataFrame:
    try:
        rows = redis.execute_command("TS.RANGE", key, "-", "+", "AGGREGATION", aggregation, bucket_ms)
    except Exception:
        rows = []

    df = pd.DataFrame(rows, columns=["ts", "value"])
    if not df.empty:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["datetime"] = df["ts"].apply(lambda v: datetime.fromtimestamp(int(v) / 1000.0))
    return df


def get_postos_df(redis: Redis) -> pd.DataFrame:
    rows = []
    for key in scan_keys(redis, "posto:*"):
        info = redis.hgetall(key)
        if not info:
            continue

        rows.append({
            "key": key,
            "posto_id": info.get("posto_id", key.replace("posto:", "posto_")),
            "posto_nome": info.get("posto_nome", "-"),
            "bandeira": info.get("bandeira", "-"),
            "cidade": info.get("cidade", "-"),
            "cidade_key": info.get("cidade_key", "-"),
            "bairro": info.get("bairro", "-"),
            "bairro_key": info.get("bairro_key", "-"),
            "estado": info.get("estado", "-"),
            "lat": float(info.get("lat") or 0),
            "lon": float(info.get("lon") or 0),
            "stars": float(info.get("stars") or 0),
            "views": int(float(info.get("views") or 0)),
            "searches": int(float(info.get("searches") or 0)),
            "abastecimentos": int(float(info.get("abastecimentos") or 0)),
            "litros_total": float(info.get("litros_total") or 0),
            "valor_total_abastecido": float(info.get("valor_total_abastecido") or 0),
        })

    return pd.DataFrame(rows)


def get_variacoes_df(redis: Redis) -> pd.DataFrame:
    rows = []
    for key in scan_keys(redis, "variacao:preco:*"):
        info = redis.hgetall(key)
        if not info:
            continue

        rows.append({
            "key": key,
            "posto_id": info.get("posto_id", "-"),
            "posto_nome": info.get("posto_nome", "-"),
            "bandeira": info.get("bandeira", "-"),
            "cidade": info.get("cidade", "-"),
            "bairro": info.get("bairro", "-"),
            "combustivel": info.get("combustivel", "-"),
            "preco_anterior": float(info.get("preco_anterior") or 0),
            "preco_atual": float(info.get("preco_atual") or 0),
            "delta_abs": float(info.get("delta_abs") or 0),
            "delta_pct": float(info.get("delta_pct") or 0),
            "ts": int(float(info.get("ts") or 0)),
            "fonte": info.get("fonte", "-"),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["datetime"] = df["ts"].apply(lambda v: datetime.fromtimestamp(v / 1000.0))
        df["abs_delta_pct"] = df["delta_pct"].abs()
    return df


def available_fuels(redis: Redis) -> List[str]:
    keys = scan_keys(redis, "ranking:postos:preco:*")
    fuels = set()

    for key in keys:
        parts = key.split(":")
        if len(parts) == 4:
            fuels.add(parts[-1])
        elif len(parts) >= 7:
            fuels.add(parts[-1])

    default_order = [
        "gasolina_comum",
        "gasolina_aditivada",
        "etanol",
        "diesel",
        "diesel_s10",
        "gnv",
    ]
    ordered = [f for f in default_order if f in fuels]
    ordered.extend(sorted(fuels - set(ordered)))
    return ordered


def metric_card(label: str, value: str, help_text: str = "") -> None:
    st.metric(label=label, value=value, help=help_text or None)


# ──────────────────────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Radar Combustível — Redis Serving",
    page_icon="⛽",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.08);
        padding: 14px 16px;
        border-radius: 14px;
    }
    .small-caption {color: #8a8f98; font-size: 0.9rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⛽ Radar Combustível — MongoDB → Redis")
st.caption(
    "Dashboard em tempo quase real sobre Redis: rankings, preço por combustível, buscas por bairro, GEO, TimeSeries e variação recente."
)

redis = get_redis()

try:
    redis.ping()
except Exception as exc:
    st.error(f"Falha ao conectar no Redis em {REDIS_HOST}:{REDIS_PORT}. Erro: {exc}")
    st.stop()

with st.sidebar:
    st.header("Controles")
    auto_refresh = st.toggle("Auto-refresh", value=False)
    refresh_seconds = st.number_input("Intervalo de refresh", min_value=2, max_value=60, value=10, step=1)
    top_n = st.slider("Top N", min_value=5, max_value=30, value=10, step=5)

    fuels = available_fuels(redis)
    fuel = st.selectbox("Combustível", fuels or ["gasolina_comum"])

    st.divider()
    st.caption("Conexão")
    st.code(f"Redis: {REDIS_HOST}:{REDIS_PORT}", language="text")

postos_df = get_postos_df(redis)
variacoes_df = get_variacoes_df(redis)

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    metric_card("Postos no Redis", f"{len(postos_df):,}".replace(",", "."))
with k2:
    metric_card("Views processadas", f"{int(postos_df['views'].sum()) if not postos_df.empty else 0:,}".replace(",", "."))
with k3:
    metric_card("Buscas registradas", str(int(redis.zcard("ranking:bairros:buscas") or 0)))
with k4:
    metric_card("Abastecimentos", f"{int(postos_df['abastecimentos'].sum()) if not postos_df.empty else 0:,}".replace(",", "."))
with k5:
    value = postos_df["valor_total_abastecido"].sum() if not postos_df.empty else 0
    metric_card("Valor abastecido", f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

tab_overview, tab_prices, tab_demand, tab_geo, tab_ts, tab_debug = st.tabs(
    [
        "Visão executiva",
        "Preço & variação",
        "Demanda & rankings",
        "Mapa GEO",
        "TimeSeries",
        "Debug Redis",
    ]
)

# ──────────────────────────────────────────────────────────────────────────────
# Overview
# ──────────────────────────────────────────────────────────────────────────────

with tab_overview:
    c1, c2 = st.columns([1.1, 1])

    with c1:
        st.subheader("🏆 Top postos por views")
        df_views = zset_to_df(redis, "ranking:postos:views", top_n, reverse=True, member_col="posto_id", score_col="views")
        if df_views.empty:
            st.info("Sem dados em ranking:postos:views.")
        else:
            names = resolve_posto_names(redis, df_views["posto_id"].tolist())
            df_views["posto_nome"] = df_views["posto_id"].map(names)
            df_views["views"] = df_views["views"].astype(int)

            fig = px.bar(
                df_views.sort_values("views", ascending=True),
                x="views",
                y="posto_nome",
                orientation="h",
                title="Postos mais acessados",
                text="views",
            )
            fig.update_layout(height=430, yaxis_title="", xaxis_title="views")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_views[["posto_id", "posto_nome", "views"]], use_container_width=True, hide_index=True)

    with c2:
        st.subheader("🧾 Perfil dos postos")
        if postos_df.empty:
            st.info("Sem hashes posto:* no Redis.")
        else:
            by_bandeira = postos_df.groupby("bandeira", as_index=False).agg(
                postos=("posto_id", "count"),
                views=("views", "sum"),
                valor_total=("valor_total_abastecido", "sum"),
            )
            fig = px.treemap(
                by_bandeira,
                path=["bandeira"],
                values="views",
                color="valor_total",
                title="Bandeiras por volume de views e valor abastecido",
            )
            fig.update_layout(height=430)
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("📋 Cadastro resumido no Redis")
    if postos_df.empty:
        st.info("Sem dados de postos.")
    else:
        cols = [
            "posto_id", "posto_nome", "bandeira", "cidade", "bairro",
            "stars", "views", "abastecimentos", "litros_total", "valor_total_abastecido",
        ]
        st.dataframe(postos_df[cols].sort_values("views", ascending=False), use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# Prices and variation
# ──────────────────────────────────────────────────────────────────────────────

with tab_prices:
    c1, c2 = st.columns([1, 1])

    with c1:
        st.subheader(f"💸 Menores preços — {fuel}")
        key = f"ranking:postos:preco:{fuel}"
        df_prices = zset_to_df(redis, key, top_n, reverse=False, member_col="posto_id", score_col="preco")
        if df_prices.empty:
            st.info(f"Sem dados em {key}.")
        else:
            names = resolve_posto_names(redis, df_prices["posto_id"].tolist())
            df_prices["posto_nome"] = df_prices["posto_id"].map(names)
            df_prices["preco"] = df_prices["preco"].round(2)

            enriched = []
            for _, row in df_prices.iterrows():
                info = posto_info(redis, row["posto_id"])
                enriched.append({
                    **row.to_dict(),
                    "bandeira": info.get("bandeira", "-"),
                    "cidade": info.get("cidade", "-"),
                    "bairro": info.get("bairro", "-"),
                    "stars": float(info.get("stars") or 0),
                })
            df_prices_full = pd.DataFrame(enriched)

            fig = px.bar(
                df_prices_full.sort_values("preco", ascending=False),
                x="preco",
                y="posto_nome",
                orientation="h",
                title="Ranking Redis Sorted Set — menor score = menor preço",
                text="preco",
                hover_data=["bandeira", "cidade", "bairro", "stars"],
            )
            fig.update_layout(height=450, yaxis_title="", xaxis_title="R$/L")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_prices_full, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("📈 Maiores variações recentes")
        if variacoes_df.empty:
            st.info("Sem dados em variacao:preco:*.")
        else:
            df_var = variacoes_df[variacoes_df["combustivel"] == fuel].copy()
            if df_var.empty:
                df_var = variacoes_df.copy()

            df_var = df_var.sort_values("abs_delta_pct", ascending=False).head(top_n)

            fig = px.bar(
                df_var.sort_values("delta_pct", ascending=True),
                x="delta_pct",
                y="posto_nome",
                orientation="h",
                title="Variação percentual de preço",
                text="delta_pct",
                hover_data=["combustivel", "preco_anterior", "preco_atual", "bairro", "fonte"],
            )
            fig.update_layout(height=450, yaxis_title="", xaxis_title="Delta %")
            st.plotly_chart(fig, use_container_width=True)

            show_cols = [
                "posto_id", "posto_nome", "bandeira", "bairro", "combustivel",
                "preco_anterior", "preco_atual", "delta_abs", "delta_pct", "fonte",
            ]
            st.dataframe(df_var[show_cols], use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# Demand
# ──────────────────────────────────────────────────────────────────────────────

with tab_demand:
    c1, c2, c3 = st.columns(3)

    with c1:
        st.subheader("🔎 Bairros mais buscados")
        df_bairros = zset_to_df(redis, "ranking:bairros:buscas", top_n, True, "bairro", "buscas")
        if df_bairros.empty:
            st.info("Sem dados em ranking:bairros:buscas.")
        else:
            df_bairros["buscas"] = df_bairros["buscas"].astype(int)
            fig = px.bar(
                df_bairros.sort_values("buscas", ascending=True),
                x="buscas",
                y="bairro",
                orientation="h",
                title="Busca por região",
                text="buscas",
            )
            fig.update_layout(height=420, yaxis_title="", xaxis_title="buscas")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_bairros, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("⛽ Combustíveis mais buscados")
        df_fuels = zset_to_df(redis, "ranking:combustiveis:buscas", top_n, True, "combustivel", "buscas")
        if df_fuels.empty:
            st.info("Sem dados em ranking:combustiveis:buscas.")
        else:
            df_fuels["buscas"] = df_fuels["buscas"].astype(int)
            fig = px.pie(
                df_fuels,
                names="combustivel",
                values="buscas",
                title="Participação das buscas",
                hole=0.45,
            )
            fig.update_layout(height=420)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_fuels, use_container_width=True, hide_index=True)

    with c3:
        st.subheader("🛢️ Abastecimentos")
        df_abast = zset_to_df(redis, "ranking:postos:abastecimentos", top_n, True, "posto_id", "abastecimentos")
        if df_abast.empty:
            st.info("Sem dados em ranking:postos:abastecimentos.")
        else:
            names = resolve_posto_names(redis, df_abast["posto_id"].tolist())
            df_abast["posto_nome"] = df_abast["posto_id"].map(names)
            df_abast["abastecimentos"] = df_abast["abastecimentos"].astype(int)
            fig = px.bar(
                df_abast.sort_values("abastecimentos", ascending=True),
                x="abastecimentos",
                y="posto_nome",
                orientation="h",
                title="Eventos de abastecimento por posto",
                text="abastecimentos",
            )
            fig.update_layout(height=420, yaxis_title="", xaxis_title="eventos")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_abast[["posto_id", "posto_nome", "abastecimentos"]], use_container_width=True, hide_index=True)

    st.subheader("💰 Ranking por valor abastecido")
    df_valor = zset_to_df(redis, "ranking:postos:valor_abastecido", top_n, True, "posto_id", "valor_total")
    if df_valor.empty:
        st.info("Sem dados em ranking:postos:valor_abastecido.")
    else:
        names = resolve_posto_names(redis, df_valor["posto_id"].tolist())
        df_valor["posto_nome"] = df_valor["posto_id"].map(names)
        df_valor["valor_total"] = df_valor["valor_total"].round(2)
        st.dataframe(df_valor[["posto_id", "posto_nome", "valor_total"]], use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# GEO
# ──────────────────────────────────────────────────────────────────────────────

with tab_geo:
    st.subheader("📍 Postos próximos — Redis GEO")

    if postos_df.empty:
        st.info("Sem postos para exibir no mapa.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        default_lat = float(postos_df["lat"].replace(0, pd.NA).dropna().mean())
        default_lon = float(postos_df["lon"].replace(0, pd.NA).dropna().mean())

        with c1:
            lat = st.number_input("Latitude", value=default_lat, format="%.6f")
        with c2:
            lon = st.number_input("Longitude", value=default_lon, format="%.6f")
        with c3:
            radius = st.number_input("Raio km", min_value=1.0, max_value=50.0, value=5.0, step=1.0)
        with c4:
            cidade_key = st.selectbox("Cidade GEO", sorted(postos_df["cidade_key"].dropna().unique().tolist()))

        geo_redis_key = f"geo:postos:{cidade_key}"

        try:
            nearby = redis.execute_command(
                "GEORADIUS",
                geo_redis_key,
                lon,
                lat,
                radius,
                "km",
                "WITHDIST",
                "ASC",
                "COUNT",
                top_n,
            )
        except Exception as exc:
            nearby = []
            st.warning(f"Falha na consulta GEO: {exc}")

        rows = []
        for item in nearby:
            posto_id, dist = item[0], float(item[1])
            info = posto_info(redis, posto_id)
            if info:
                rows.append({
                    "posto_id": posto_id,
                    "posto_nome": info.get("posto_nome", posto_id),
                    "bandeira": info.get("bandeira", "-"),
                    "cidade": info.get("cidade", "-"),
                    "bairro": info.get("bairro", "-"),
                    "dist_km": round(dist, 2),
                    "lat": float(info.get("lat") or 0),
                    "lon": float(info.get("lon") or 0),
                })

        df_near = pd.DataFrame(rows)

        map_df = postos_df[["posto_id", "posto_nome", "lat", "lon", "cidade", "bairro"]].copy()
        map_df = map_df[(map_df["lat"] != 0) & (map_df["lon"] != 0)]
        st.map(map_df.rename(columns={"lat": "latitude", "lon": "longitude"}), latitude="latitude", longitude="longitude")

        if df_near.empty:
            st.info(f"Nenhum posto encontrado em {geo_redis_key} no raio selecionado.")
        else:
            st.dataframe(df_near, use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# TimeSeries
# ──────────────────────────────────────────────────────────────────────────────

with tab_ts:
    st.subheader("📈 Séries temporais RedisTimeSeries")

    if postos_df.empty:
        st.info("Sem postos para consultar.")
    else:
        posto_options = postos_df.sort_values("views", ascending=False)["posto_id"].tolist()
        c1, c2, c3 = st.columns(3)

        with c1:
            selected_posto = st.selectbox("Posto", posto_options)
        with c2:
            metric = st.selectbox("Métrica", ["views", "preco", "abastecimentos", "valor_abastecido"])
        with c3:
            ts_fuel = st.selectbox("Combustível da série de preço", fuels or ["gasolina_comum"])

        numeric = extract_numeric_id(selected_posto)

        if metric == "preco":
            ts_key = f"ts:posto:{numeric}:preco:{ts_fuel}"
            aggregation = "avg"
            y_label = "R$/L"
        else:
            ts_key = f"ts:posto:{numeric}:{metric}"
            aggregation = "sum"
            y_label = metric

        df_ts = ts_range(redis, ts_key, aggregation=aggregation, bucket_ms=60000)

        st.code(ts_key, language="text")

        if df_ts.empty:
            st.info("Sem dados para a série selecionada.")
        else:
            fig = px.line(
                df_ts,
                x="datetime",
                y="value",
                markers=True,
                title=f"{metric} — {selected_posto}",
            )
            fig.update_layout(height=430, xaxis_title="tempo", yaxis_title=y_label)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_ts.tail(20), use_container_width=True, hide_index=True)


# ──────────────────────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────────────────────

with tab_debug:
    st.subheader("🧪 Debug das estruturas Redis")

    patterns = [
        "ranking:postos:*",
        "ranking:bairros:*",
        "ranking:combustiveis:*",
        "posto:*",
        "variacao:preco:*",
        "ts:posto:*",
        "geo:postos:*",
    ]

    selected_pattern = st.selectbox("Pattern", patterns)
    keys = scan_keys(redis, selected_pattern)
    st.caption(f"{len(keys)} chaves encontradas para `{selected_pattern}`.")

    st.dataframe(pd.DataFrame({"key": keys[:500]}), use_container_width=True, hide_index=True)

    st.subheader("Consulta rápida")
    sample_key = st.text_input("Chave Redis", value=keys[0] if keys else "ranking:postos:views")
    if sample_key:
        try:
            key_type = redis.type(sample_key)
            st.write(f"Tipo: `{key_type}`")

            if key_type == "zset":
                st.write(redis.zrevrange(sample_key, 0, 20, withscores=True))
            elif key_type == "hash":
                st.json(redis.hgetall(sample_key))
            elif key_type == "TSDB-TYPE":
                st.write(redis.execute_command("TS.RANGE", sample_key, "-", "+")[-20:])
            elif key_type == "string":
                st.write(redis.get(sample_key))
            else:
                st.write("Tipo não detalhado nesta tela.")
        except Exception as exc:
            st.error(f"Falha ao consultar chave: {exc}")

if auto_refresh:
    time.sleep(int(refresh_seconds))
    st.rerun()
