import os
import re
import time
from datetime import datetime
from typing import Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from pymongo import MongoClient
from redis import Redis

load_dotenv(".env.local")
load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
MONGO_DB = os.getenv("RADAR_MONGO_DB", "radar_combustivel")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "events")


def get_redis() -> Redis:
    return Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


def get_mongo_collection():
    client = MongoClient(MONGO_URI)
    return client[MONGO_DB][MONGO_COLLECTION]


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


def scan_keys(redis: Redis, pattern: str) -> List[str]:
    return sorted([k for k in redis.scan_iter(match=pattern)])


def zset_to_df(redis: Redis, key: str, n: int = 10, reverse: bool = True, member_col: str = "member", score_col: str = "score") -> pd.DataFrame:
    rows = redis.zrevrange(key, 0, n - 1, withscores=True) if reverse else redis.zrange(key, 0, n - 1, withscores=True)
    df = pd.DataFrame(rows, columns=[member_col, score_col])
    if not df.empty:
        df[score_col] = pd.to_numeric(df[score_col], errors="coerce")
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


def available_fuels(redis: Redis) -> List[str]:
    keys = scan_keys(redis, "ranking:postos:preco:*")
    fuels = set()
    for key in keys:
        parts = key.split(":")
        if len(parts) == 4:
            fuels.add(parts[-1])
        elif len(parts) >= 7:
            fuels.add(parts[-1])

    default_order = ["gasolina_comum", "gasolina_aditivada", "etanol", "diesel", "diesel_s10", "gnv"]
    ordered = [f for f in default_order if f in fuels]
    ordered.extend(sorted(fuels - set(ordered)))
    return ordered or default_order


def latest_events_df(limit: int = 5) -> pd.DataFrame:
    col = get_mongo_collection()
    docs = list(
        col.find({}, {"_id": 0})
        .sort("ts", -1)
        .limit(limit)
    )

    rows = []
    for doc in docs:
        ts = int(doc.get("ts") or 0)
        event_type = doc.get("type", "-")

        if event_type == "price_update":
            summary = f"{doc.get('combustivel')} | R$ {doc.get('preco_anterior')} → R$ {doc.get('preco')} | Δ {doc.get('delta_pct')}%"
        elif event_type == "abastecimento":
            summary = f"{doc.get('combustivel')} | {doc.get('litros')} L | R$ {doc.get('valor_total')}"
        elif event_type == "search":
            summary = f"{doc.get('combustivel')} | termo: {doc.get('termo')}"
        elif event_type == "rating":
            summary = f"{doc.get('combustivel')} | nota {doc.get('stars')}"
        else:
            summary = f"{doc.get('combustivel', '')} | origem: {doc.get('origem', '-')}"

        rows.append({
            "datetime": datetime.fromtimestamp(ts / 1000.0) if ts else None,
            "type": event_type,
            "posto_id": doc.get("posto_id", "-"),
            "posto_nome": doc.get("posto_nome", "-"),
            "cidade": doc.get("cidade", "-"),
            "bairro": doc.get("bairro", "-"),
            "summary": summary,
            "source": doc.get("source", "seed/backfill"),
        })

    return pd.DataFrame(rows)


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


st.set_page_config(
    page_title="Radar Combustível — Realtime Redis",
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
    .event-card {
        padding: 12px 14px;
        border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.035);
        margin-bottom: 8px;
    }
    .event-type {
        font-weight: 700;
        font-size: 0.92rem;
        color: #ff4b4b;
    }
    .muted {
        color: #8a8f98;
        font-size: 0.82rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⛽ Radar Combustível — Realtime MongoDB → Redis")
st.caption("Use o gerador em tempo real para inserir novos eventos no MongoDB. O consumer captura via Change Stream e atualiza o Redis.")

with st.sidebar:
    st.header("Controles")
    auto_refresh = st.toggle("Auto-refresh", value=True)
    refresh_seconds = st.number_input("Intervalo de refresh", min_value=2, max_value=60, value=10, step=1)
    top_n = st.slider("Top N", min_value=5, max_value=30, value=10, step=5)
    st.divider()
    st.caption("Para simular updates:")
    st.code("python pipeline/realtime_event_generator.py --interval 120", language="powershell")

redis = get_redis()
try:
    redis.ping()
except Exception as exc:
    st.error(f"Falha ao conectar ao Redis: {exc}")
    st.stop()

postos_df = get_postos_df(redis)
fuels = available_fuels(redis)
fuel = st.sidebar.selectbox("Combustível", fuels)

latest_df = latest_events_df(5)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Postos no Redis", f"{len(postos_df)}")
k2.metric("Views processadas", f"{int(postos_df['views'].sum()) if not postos_df.empty else 0}")
k3.metric("Eventos no Mongo", f"{get_mongo_collection().count_documents({})}")
k4.metric("Abastecimentos", f"{int(postos_df['abastecimentos'].sum()) if not postos_df.empty else 0}")
value = postos_df["valor_total_abastecido"].sum() if not postos_df.empty else 0
k5.metric("Valor abastecido", f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

st.subheader("🧾 Top 5 eventos mais recentes no MongoDB")
if latest_df.empty:
    st.info("Nenhum evento encontrado.")
else:
    for _, row in latest_df.iterrows():
        st.markdown(
            f"""
            <div class="event-card">
                <div class="event-type">{row['type']} · {row['posto_id']} · {row['bairro']}</div>
                <div><strong>{row['posto_nome']}</strong></div>
                <div>{row['summary']}</div>
                <div class="muted">{row['datetime']} · source={row['source']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

tab1, tab2, tab3, tab4 = st.tabs(["Rankings Redis", "Preço", "Mapa", "TimeSeries"])

with tab1:
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🏆 Top postos por views")
        df = zset_to_df(redis, "ranking:postos:views", top_n, True, "posto_id", "views")
        if df.empty:
            st.info("Sem dados.")
        else:
            df["posto_nome"] = df["posto_id"].map(resolve_posto_names(redis, df["posto_id"].tolist()))
            df["views"] = df["views"].astype(int)
            fig = px.bar(df.sort_values("views"), x="views", y="posto_nome", orientation="h", text="views")
            fig.update_layout(height=420, yaxis_title="", xaxis_title="views")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("🔎 Combustíveis mais buscados")
        df = zset_to_df(redis, "ranking:combustiveis:buscas", top_n, True, "combustivel", "buscas")
        if df.empty:
            st.info("Sem dados.")
        else:
            df["buscas"] = df["buscas"].astype(int)
            fig = px.pie(df, names="combustivel", values="buscas", hole=0.45)
            fig.update_layout(height=420)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("📍 Bairros mais buscados")
    df_bairros = zset_to_df(redis, "ranking:bairros:buscas", top_n, True, "bairro", "buscas")
    if not df_bairros.empty:
        df_bairros["buscas"] = df_bairros["buscas"].astype(int)
        st.dataframe(df_bairros, use_container_width=True, hide_index=True)

with tab2:
    c1, c2 = st.columns(2)

    with c1:
        st.subheader(f"💸 Menores preços — {fuel}")
        df_price = zset_to_df(redis, f"ranking:postos:preco:{fuel}", top_n, False, "posto_id", "preco")
        if df_price.empty:
            st.info("Sem dados.")
        else:
            df_price["posto_nome"] = df_price["posto_id"].map(resolve_posto_names(redis, df_price["posto_id"].tolist()))
            df_price["preco"] = df_price["preco"].round(2)
            fig = px.bar(df_price.sort_values("preco", ascending=False), x="preco", y="posto_nome", orientation="h", text="preco")
            fig.update_layout(height=430, yaxis_title="", xaxis_title="R$/L")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_price, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("📈 Maiores variações")
        rows = []
        for key in scan_keys(redis, f"variacao:preco:*:{fuel}"):
            info = redis.hgetall(key)
            if info:
                rows.append({
                    "posto_id": info.get("posto_id"),
                    "posto_nome": info.get("posto_nome"),
                    "bairro": info.get("bairro"),
                    "combustivel": info.get("combustivel"),
                    "preco_anterior": float(info.get("preco_anterior") or 0),
                    "preco_atual": float(info.get("preco_atual") or 0),
                    "delta_pct": float(info.get("delta_pct") or 0),
                })
        df_var = pd.DataFrame(rows)
        if df_var.empty:
            st.info("Sem variações para o combustível selecionado.")
        else:
            df_var["abs_delta_pct"] = df_var["delta_pct"].abs()
            df_var = df_var.sort_values("abs_delta_pct", ascending=False).head(top_n)
            fig = px.bar(df_var.sort_values("delta_pct"), x="delta_pct", y="posto_nome", orientation="h", text="delta_pct")
            fig.update_layout(height=430, yaxis_title="", xaxis_title="Δ%")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_var.drop(columns=["abs_delta_pct"]), use_container_width=True, hide_index=True)

with tab3:
    st.subheader("🗺️ Postos no mapa")
    if postos_df.empty:
        st.info("Sem postos.")
    else:
        map_df = postos_df[(postos_df["lat"] != 0) & (postos_df["lon"] != 0)].copy()
        st.map(map_df.rename(columns={"lat": "latitude", "lon": "longitude"}), latitude="latitude", longitude="longitude")
        st.dataframe(map_df[["posto_id", "posto_nome", "bandeira", "cidade", "bairro", "lat", "lon"]], use_container_width=True, hide_index=True)

with tab4:
    st.subheader("📈 RedisTimeSeries")
    if postos_df.empty:
        st.info("Sem postos.")
    else:
        posto_options = postos_df.sort_values("views", ascending=False)["posto_id"].tolist()
        c1, c2, c3 = st.columns(3)
        selected_posto = c1.selectbox("Posto", posto_options)
        metric = c2.selectbox("Métrica", ["views", "preco", "abastecimentos", "valor_abastecido"])
        ts_fuel = c3.selectbox("Combustível", fuels)

        numeric = extract_numeric_id(selected_posto)
        if metric == "preco":
            key = f"ts:posto:{numeric}:preco:{ts_fuel}"
            aggregation = "avg"
        else:
            key = f"ts:posto:{numeric}:{metric}"
            aggregation = "sum"

        st.code(key, language="text")
        df_ts = ts_range(redis, key, aggregation=aggregation)
        if df_ts.empty:
            st.info("Sem série temporal para essa seleção.")
        else:
            fig = px.line(df_ts, x="datetime", y="value", markers=True)
            fig.update_layout(height=430)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_ts.tail(20), use_container_width=True, hide_index=True)

if auto_refresh:
    time.sleep(int(refresh_seconds))
    st.rerun()
