# ⛽ Radar Combustível — Pipeline MongoDB → Redis

> **MBA FIAP — Bancos de Dados In-Memory | Trabalho Final em Grupo**
> Arquitetura de dados em tempo quase real baseada em eventos, com MongoDB como event store, Redis como camada de serving e Streamlit para visualização interativa.

---

##  Integrantes

| Nome | RM |
|------|----|
| André da Silva Gomes Lima | RM 364124 |
| Evandro dos Santos Sales  | RM 362411 |
| Felipe de Almeida Pereira | RM 361006 |
| Helen Fernandes Borges    | RM 364154 |
| Matheus Pereira Condotta  | RM 361638 |
| Roberto Ferreira Paulo    | RM 362593 |

---
##  Visão geral

O **Radar Combustível** é uma plataforma orientada a dados que monitora, em tempo quase real, o comportamento de preços, demanda e interação de usuários em postos de combustível na Grande São Paulo.

A solução foi projetada para responder perguntas críticas de negócio com baixa latência:

* Onde estão os **menores preços** por combustível?
* Quais regiões apresentam **maior demanda**?
* Quais postos tiveram **maior variação de preço**?
* Quais opções estão **mais próximas do usuário**?
* Como os preços evoluem **ao longo do tempo**?

---

## Proposta da solução

A arquitetura implementa um pipeline moderno orientado a eventos:

```text
mongo_seed + realtime_generator
            ↓
        MongoDB (events)
            ↓
     Change Stream (real-time)
            ↓
        Consumer (Python)
            ↓
          Redis (serving layer)
            ↓
     Streamlit (analytics + realtime)
```

O sistema permite tanto análise histórica quanto monitoramento contínuo, simulando um ambiente próximo de produção.

---

## 🏗️ Arquitetura da solução

```
┌─────────────────────────────────────────────────────────────────────┐
│                         RADAR COMBUSTÍVEL                           │
│                                                                     │
│  ┌──────────────┐    Change      ┌─────────────────────────────┐    │
│  │   MongoDB    │────Stream────▶│   mongodb_consumer.py       │    │
│  │              │                │   (pipeline principal)      │    │
│  │  • postos    │                └────────────┬────────────────┘    │
│  │  • eventos   │                             │                     │
│  │    - view    │                    normalize_event()              │
│  │    - search  │                    event_transformer.py           │
│  │    - price_  │                             │                     │
│  │      update  │           ┌─────────────────▼───────────────────┐ │
│  │    - rating  │           │              REDIS                  │ │
│  │    - abaste- │           │                                     │ │
│  │      cimento │           │  Hash    posto:{id}                 │ │
│  └──────────────┘           │  SortedSet ranking:postos:preco:*   │ │
│                             │  SortedSet ranking:bairros:buscas   │ │
│  ┌──────────────┐           │  SortedSet ranking:postos:views     │ │
│  │ mongo_seed   │           │  SortedSet ranking:postos:avaliacoes│ │
│  │    .py       │           │  Hash    variacao:preco:{id}:{comb} │ │
│  │              │           │  Geo     geo:postos:{cidade}        │ │
│  │ (seed fake)  │           │  TimeSeries ts:posto:{id}:preco:*   │ │
│  └──────────────┘           │  TimeSeries ts:posto:{id}:views     │ │
│                             └──────────────┬──────────────────────┘ │
│                                            │                        │
│                             ┌──────────────▼──────────────────────┐ │
│                             │   Streamlit Dashboard               | |
|                             |    (Real-Time Analytics)            │ │
│                             │  • Visão executiva                  │ │
│                             │  • Preço & variação                 │ │
│                             │  • Demanda & rankings               │ │
│                             │  • Mapa GEO                         │ │
│                             │  • TimeSeries                       │ │
│                             │  • Debug Redis                      │ │
│                             └─────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```
---

## 🗄️ Modelo de dados — MongoDB

### Collection `postos`

Cadastro principal dos postos, desnormalizado para leitura direta:

```json
{
  "posto_id": "posto_001",
  "nome": "Posto Ipiranga Oliveira",
  "bandeira": "Ipiranga",
  "bairro": "Pinheiros",
  "cidade": "São Paulo",
  "lat": -23.563412,
  "lon": -46.682901,
  "aberto_24h": true,
  "combustiveis": ["gasolina_comum", "gasolina_aditivada", "etanol"],
  "precos": {
    "gasolina_comum": 5.79,
    "gasolina_aditivada": 6.09,
    "etanol": 3.89
  },
  "stars": 4.2,
  "total_avaliacoes": 312
}
```

### Collection `eventos`

Documento único desnormalizado — todos os tipos de evento compartilham a mesma collection. Campos presentes variam por tipo:

| Campo | `view` | `search` | `price_update` | `rating` | `abastecimento` |
|---|:---:|:---:|:---:|:---:|:---:|
| `posto_id`, `posto_nome`, `bairro`, `lat`, `lon` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `combustivel`, `preco` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `origem`, `sessao_id`, `duracao_seg` | ✓ | | | | |
| `termo`, `raio_km`, `resultados`, `clicou` | | ✓ | | | |
| `preco_anterior`, `delta_pct`, `delta_abs`, `fonte` | | | ✓ | | |
| `stars`, `comentario` | | | | ✓ | |
| `litros`, `valor_total`, `forma_pagamento` | | | | | ✓ |

Essa modelagem orientada a acesso evita joins e permite que o Change Stream entregue eventos já completos para o pipeline.

### Índices criados

```python
# postos
{ "posto_id": 1 }          # unique
{ "bairro": 1 }
{ "combustiveis": 1 }
{ "lat": 1, "lon": 1 }

# eventos
{ "posto_id": 1, "ts": -1 }
{ "type": 1, "ts": -1 }
{ "bairro": 1, "type": 1 }
{ "combustivel": 1, "type": 1 }
{ "ts": -1 }
```

---

## ⚡ Estruturas Redis e justificativas

### `Hash posto:{id}` — cadastro resumido

**Por que Hash?** O posto é uma entidade com múltiplos atributos de tipos distintos (string, float, int). O Hash permite `HGET` e `HSET` de campos individuais sem deserializar o objeto inteiro, e o `HMGET` retorna múltiplos campos em O(N) onde N é o número de campos — ideal para compor a tela de detalhe de um posto.

```
posto:001 → {
    posto_nome: "Posto Ipiranga Oliveira",
    bairro: "Pinheiros",
    lat: -23.563412,
    lon: -46.682901,
    stars: 4.2,
    views: 87,
    preco_gasolina_comum: 5.79,
    ts_preco_gasolina_comum: 1710010203000,
    ...
}
```

---

### `Sorted Set ranking:postos:preco:{combustivel}` — ranking de preços

**Por que Sorted Set?** O score é o preço atual em R$/L. `ZRANGE … WITHSCORES` retorna os postos ordenados do mais barato ao mais caro em O(log N + M). Atualização via `ZADD` com flag `GT` garante que o score só muda quando o preço novo é diferente — sem gravação desnecessária.

```
ranking:postos:preco:gasolina_comum →
    posto_015  5.49
    posto_003  5.59
    posto_022  5.67
    ...
```

---

### `Sorted Set ranking:bairros:buscas` — demanda por região

**Por que Sorted Set?** Cada evento `search` incrementa o score do bairro em 1 via `ZINCRBY`. `ZREVRANGE` retorna os bairros com maior volume de buscas em O(log N + M) — a consulta mais natural para o problema de demanda regionalizada.

```
ranking:bairros:buscas →
    Pinheiros         312
    Vila Madalena     289
    Itaim Bibi        241
    ...
```

---

### `Hash variacao:preco:{posto_id}:{combustivel}` — delta de preço

**Por que Hash dedicado?** A variação de preço é uma métrica composta (preço atual, anterior, delta absoluto, delta percentual, timestamp, fonte) que precisa ser lida inteira para a tela de "postos com maior oscilação". Um Hash dedicado por posto+combustível permite `HGETALL` O(N) e `SCAN variacao:preco:*` para varrer todos os pares.

```
variacao:preco:001:gasolina_comum → {
    preco_atual: 5.99,
    preco_anterior: 5.79,
    delta_abs: 0.20,
    delta_pct: 3.45,
    ts: 1710010203000,
    fonte: "app_posto"
}
```

---

### `Geo geo:postos:{cidade}` — consulta por proximidade

**Por que Geo?** O Redis Geo usa internamente um Sorted Set com Geohash codificado no score, permitindo `GEORADIUS` em O(N+log M) onde N é o número de resultados. Alternativas como filtrar coordenadas em memória teriam custo O(total de postos). A chave é particionada por cidade para evitar scans globais em deployments maiores.

```bash
GEORADIUS geo:postos:são_paulo -46.6830 -23.5634 5 km ASC COUNT 10
# → posto_007 (1.2 km), posto_012 (2.8 km), posto_003 (3.1 km) ...
```

---

### `TimeSeries ts:posto:{id}:preco:{combustivel}` — evolução temporal

**Por que TimeSeries?** O módulo RedisTimeSeries armazena dados de série temporal comprimidos (chunk-based) e suporta `TS.RANGE` com agregações nativas (`avg`, `min`, `max`, `sum`) por janela de tempo — sem precisar carregar todos os pontos em memória. O valor armazenado é o preço em R$/L, não uma contagem. Retenção configurada para 7 dias.

```bash
TS.RANGE ts:posto:001:preco:gasolina_comum - + AGGREGATION avg 3600000
# Retorna: [(ts_hora_1, 5.79), (ts_hora_2, 5.85), (ts_hora_3, 5.99) ...]
```

---

### `Sorted Set ranking:postos:avaliacoes` — ranking de avaliação

O score é a média ponderada calculada em tempo real a cada evento `rating`: `(rating_sum / rating_count)`. O Hash do posto mantém `rating_sum` e `rating_count` acumulados para recalcular a média sem precisar de histórico.

---
## Implementação técnica

* **MongoDB**

  * Armazena eventos operacionais (`events`)
  * Replica Set habilitado para Change Streams

* **Consumer (Python)**

  * Processa eventos em tempo real
  * Executa transformações e normalizações
  * Atualiza estruturas Redis otimizadas

* **Redis**

  * Camada de serving de alta performance
  * Estruturas utilizadas:

    * Sorted Sets → rankings
    * Hashes → estado dos postos
    * GEO → localização
    * TimeSeries → evolução temporal

* **Streamlit**

  * Dashboard analítico e operacional
  * Atualização automática (auto-refresh)

---

## ⚡ Pipeline em tempo real

Além do seed inicial, a solução inclui um **gerador contínuo de eventos**, permitindo simular atividade real da plataforma.

### 🔁 Gerador de eventos (`realtime_event_generator.py`)

* Gera eventos aleatórios continuamente
* Tipos simulados:

  * `view`
  * `search`
  * `price_update`
  * `rating`
  * `abastecimento`

### Execução

```bash
python realtime_queries/realtime_event_generator.py --interval 120
```

Para testes rápidos:

```bash
python realtime_queries/realtime_event_generator.py --interval 10
```

### Fluxo completo

```text
generator → MongoDB → Change Stream → Consumer → Redis → Dashboard
```

Essa abordagem permite observar o sistema operando continuamente, como em um cenário real.

---

## Dashboards

A solução possui duas camadas de visualização:

### 🧩 Dashboard analítico (Estático)

```bash
streamlit run queries/data-view.py
```

Focado em análise consolidada:

* Rankings de preços
* Variação de preços
* Demanda por bairro
* GEO (mapa)
* Séries temporais
* Debug Redis

---

### ⚡ Dashboard realtime

```bash
streamlit run realtime_queries/data-view-realtime.py
```

Focado em monitoramento ao vivo:

* 🔥 Top 5 eventos mais recentes (MongoDB)
* 📈 Rankings atualizando em tempo real
* 🔄 Auto-refresh configurável
* 📊 Integração completa com Redis

Exemplo de evento exibido:

```
price_update · posto_012 · Pinheiros
gasolina_comum | R$ 5.79 → R$ 5.99 | Δ 3.45%
```

## 🚀 Execução do projeto

> **Pré-requisito:** Docker instalado e rodando na máquina.

---

### Passo 1 — Subir a infraestrutura

Sobe o MongoDB Replica Set e o Redis Stack via Docker Compose:

```bash
docker compose up -d
```

Aguarde ~10 segundos para o MongoDB inicializar o Replica Set antes de prosseguir.

---

### Passo 2 — Configurar o ambiente virtual e instalar dependências

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

> **Linux/macOS:** usar `source .venv/bin/activate` no lugar de `.\.venv\Scripts\activate`

---

### Passo 3 — Resetar e popular o MongoDB com dados e eventos

Limpa o banco e popula com 30 postos e 5.000 eventos:

```bash
python init/mongo_seed.py --reset
```

---

### Passo 4 — Rodar o Event Transformer

Garante que as estruturas de normalização e mapeamento de chaves Redis estão prontas:

```bash
python pipeline/event_transformer.py
```

---

### Passo 5 — Iniciar o Consumer (escuta de eventos em tempo real)

Conecta ao MongoDB via Change Stream, faz backfill dos eventos existentes e fica **escutando novos eventos continuamente**. Manter este terminal aberto:

```bash
python pipeline/mongodb_consumer.py --flush-redis
```

O consumer ficará ativo aguardando novos eventos — será alimentado pelo gerador no passo seguinte.

---

### Passo 6 — Ativar o gerador de eventos em tempo real

Gera eventos aleatórios continuamente no MongoDB (`view`, `search`, `price_update`, `rating`, `abastecimento`), que são capturados pelo consumer via Change Stream e propagados para o Redis:

```bash
python realtime_queries/realtime_event_generator.py --interval 20
```

> `--interval 20` = novo evento a cada 20 segundos, ideal para ver o dashboard atualizando ao vivo. Reduza para `--interval 5` para testes mais rápidos.

---

### Passo 7 — Iniciar o Redis Reader

Lê as estruturas do Redis em tempo real e alimenta o dashboard com os dados processados pelo consumer:

```bash
python realtime_queries/redis_reader.py
```

Manter este terminal aberto junto com o consumer (Passo 5).

---

### Passo 8 — Rodar o dashboard em tempo real

Abre o dashboard principal com auto-refresh, rankings atualizados ao vivo e feed dos últimos eventos:

```bash
python -m streamlit run realtime_queries/data-view-realtime.py
```

O Streamlit abrirá automaticamente no navegador (geralmente `http://localhost:8501`) e solicitará permissão. Os logs e rankings serão atualizados em tempo quase real conforme o gerador envia eventos.

---

### (Opcional) Dashboard analítico estático

Versão consolidada para análise histórica — não depende do gerador estar rodando:

```bash
streamlit run queries/data-view.py
```

---

## 🧪 Validação

Exemplos de queries Redis:

```bash
ZREVRANGE ranking:postos:views 0 10 WITHSCORES
ZRANGE ranking:postos:preco:gasolina_comum 0 10 WITHSCORES
ZREVRANGE ranking:bairros:buscas 0 10 WITHSCORES
GEORADIUS geo:postos:sao_paulo -46.68 -23.56 5 km
TS.RANGE ts:posto:001:preco:gasolina_comum - +
```

---

## 📈 Insights obtidos

* Postos mais acessados não necessariamente são os mais baratos
* Bairros com maior volume de busca indicam concentração de demanda
* Variações de preço mostram volatilidade por combustível
* GEO permite recomendação baseada em proximidade
* Séries temporais evidenciam tendências e sazonalidade

---

## 🚀 Diferenciais da solução

* Arquitetura **event-driven com Change Stream**
* Simulação de ambiente real com dados contínuos
* Uso avançado de Redis como camada de serving
* Separação clara entre ingestão, transformação e consumo
* Dashboard com atualização em tempo quase real
* Estruturas otimizadas para baixa latência

---

## 🔗 Repositório

https://github.com/tigressdev/radar_app

---

## 🏁 Conclusão

O projeto demonstra, de forma prática, como construir uma arquitetura moderna de dados baseada em eventos, com foco em performance, escalabilidade e capacidade analítica em tempo quase real.
A solução implementa padrões amplamente utilizados em sistemas de produção, aproximando o ambiente acadêmico de cenários reais de engenharia de dados.
