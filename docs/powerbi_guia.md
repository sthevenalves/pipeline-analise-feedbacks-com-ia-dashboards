# Guia do Dashboard no Power BI

## 1. Conectar aos dados

**Opção A – CSV (mais simples, funciona com o SQLite padrão)**
1. Rode o pipeline: ele gera `output/vw_feedbacks_analisados.csv` e `output/vw_pontos_atencao.csv`.
2. Power BI Desktop → *Obter dados* → *Texto/CSV* → importe os dois arquivos.

**Opção B – PostgreSQL (conexão direta, atualização automática)**
1. `docker compose up -d` e defina no `.env`:
   `DATABASE_URL=postgresql+psycopg2://feedback:feedback@localhost:5432/feedbacks`
2. Rode o pipeline.
3. Power BI Desktop → *Obter dados* → *Banco de dados PostgreSQL* → servidor `localhost`, banco `feedbacks`.
4. Selecione as views `vw_feedbacks_analisados` e `vw_pontos_atencao`.

## 2. Modelo de dados
Relacione `vw_pontos_atencao[feedback_id]` (muitos) → `vw_feedbacks_analisados[id]` (um).
Crie uma tabela de datas (`Calendario = CALENDARAUTO()`) e relacione com `vw_feedbacks_analisados[data]`.

## 3. Medidas DAX

```DAX
Total Feedbacks = COUNTROWS(vw_feedbacks_analisados)

% Positivo =
DIVIDE(
    CALCULATE([Total Feedbacks], vw_feedbacks_analisados[sentimento] = "positivo"),
    [Total Feedbacks]
)

% Negativo =
DIVIDE(
    CALCULATE([Total Feedbacks], vw_feedbacks_analisados[sentimento] = "negativo"),
    [Total Feedbacks]
)

Score Médio = AVERAGE(vw_feedbacks_analisados[score])

NPS Proxy = ([% Positivo] - [% Negativo]) * 100

Nota Média Original = AVERAGE(vw_feedbacks_analisados[nota_original])
```

## 4. Layout sugerido (1 página)

| Área | Visual | Campos |
|---|---|---|
| Topo | 4 cartões | Total Feedbacks, % Positivo, % Negativo, Score Médio |
| Esquerda | Rosca | `sentimento` × Total Feedbacks |
| Centro | Colunas empilhadas 100% | `categoria` × sentimento |
| Direita | Linha | Mês (Calendario) × Score Médio, legenda por `fonte` |
| Base esquerda | Barras horizontais (Top 10) | `vw_pontos_atencao[ponto]` × contagem, filtro sentimento ≠ positivo |
| Base direita | Tabela | `resumo`, `sentimento`, `fonte`, `data` |
| Lateral | Segmentações | `fonte`, período, `categoria` |

Dica: use verde/cinza/vermelho fixos para positivo/neutro/negativo em todos os visuais.

## 5. Atualização
Rode o pipeline sempre que houver feedbacks novos (`cron`, Agendador de Tarefas do Windows ou GitHub Actions) e clique em *Atualizar* no Power BI.
