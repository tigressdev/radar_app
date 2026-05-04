# ⛽ Radar Combustível — Pipeline MongoDB → Redis

> **MBA FIAP — Bancos de Dados In-Memory | Trabalho Final em Grupo**
> Arquitetura de dados em tempo quase real baseada em eventos, com MongoDB como event store, Redis como camada de serving e Streamlit para visualização interativa.

---

## 🧑‍💻 Integrantes

| Nome | RM |
|------|----|
| André da Silva Gomes Lima | RM 364124 |
| Evandro dos Santos Sales  | RM 362411 |
| Felipe de Almeida Pereira | RM 361006 |
| Helen Fernandes Borges    | RM 364154 |
| Matheus Pereira Condotta  | RM 361638 |
| Roberto Ferreira Paulo    | RM 362593 |

---
## 🎯 Visão geral

O **Radar Combustível** é uma plataforma orientada a dados que monitora, em tempo quase real, o comportamento de preços, demanda e interação de usuários em postos de combustível na Grande São Paulo.

A solução foi projetada para responder perguntas críticas de negócio com baixa latência:

* Onde estão os **menores preços** por combustível?
* Quais regiões apresentam **maior demanda**?
* Quais postos tiveram **maior variação de preço**?
* Quais opções estão **mais próximas do usuário**?
* Como os preços evoluem **ao longo do tempo**?

---

## 🧠 Proposta da solução

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
## 🏗️ Arquitetura técnica

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
python pipeline/realtime_event_generator.py --interval 120
```

Para testes rápidos:

```bash
python pipeline/realtime_event_generator.py --interval 10
```

### Fluxo completo

```text
generator → MongoDB → Change Stream → Consumer → Redis → Dashboard
```

Essa abordagem permite observar o sistema operando continuamente, como em um cenário real.

---

## 📊 Dashboards

A solução possui duas camadas de visualização:

### 🧩 Dashboard analítico

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

---

## 🗄️ Modelo de dados

### MongoDB

#### Collection `postos`

Cadastro desnormalizado dos postos.

#### Collection `events`

Armazena todos os eventos da plataforma (event-driven).

---

## ⚡ Estruturas Redis

| Estrutura                          | Uso                   |
| ---------------------------------- | --------------------- |
| `Hash posto:{id}`                  | Estado atual do posto |
| `SortedSet ranking:postos:preco:*` | Ranking de preços     |
| `SortedSet ranking:bairros:buscas` | Demanda regional      |
| `SortedSet ranking:postos:views`   | Popularidade          |
| `Hash variacao:preco:*`            | Delta de preço        |
| `Geo geo:postos:*`                 | Proximidade           |
| `TimeSeries ts:posto:*`            | Evolução temporal     |

---

## 🚀 Execução do projeto

### 1. Subir infraestrutura

```bash
docker-compose up -d
```

---

### 2. Popular MongoDB

```bash
python init/mongo_seed.py --reset
```

---

### 3. Rodar pipeline

```bash
python pipeline/mongodb_consumer.py --flush-redis
```

---

### 4. (Opcional) Ativar realtime

```bash
python pipeline/realtime_event_generator.py
```

---

### 5. Rodar dashboards

```bash
streamlit run queries/data-view.py
streamlit run realtime_queries/data-view-realtime.py
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

> Caso privado, compartilhar com: https://github.com/commithouse

---

## 🧑‍💻 Integrantes

* André da Silva Gomes Lima
* Evandro dos Santos Sales
* Felipe de Almeida Pereira
* Helen Fernandes Borges
* Matheus Pereira Condotta
* Roberto Ferreira Paulo

---

## 🏁 Conclusão

O projeto demonstra, de forma prática, como construir uma arquitetura moderna de dados baseada em eventos, com foco em performance, escalabilidade e capacidade analítica em tempo quase real.

A solução implementa padrões amplamente utilizados em sistemas de produção, aproximando o ambiente acadêmico de cenários reais de engenharia de dados.

