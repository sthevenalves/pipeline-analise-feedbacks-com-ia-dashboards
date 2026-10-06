"""Pipeline: CSV -> IA (sentimento + pontos de atenção) -> SQL -> CSVs/views para o Power BI.

Uso:
    python -m src.pipeline --input data/feedbacks_exemplo.csv --provider mock
    python -m src.pipeline --input data/feedbacks_exemplo.csv --provider gemini --batch-size 10
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from src import config, database
from src.analyzer import get_analyzer

COLUNAS_OBRIGATORIAS = {"id", "fonte", "data", "nota", "texto"}
log = logging.getLogger("pipeline")


def carregar_csv(caminho: Path) -> pd.DataFrame:
    df = pd.read_csv(caminho)
    faltando = COLUNAS_OBRIGATORIAS - set(df.columns)
    if faltando:
        raise ValueError(f"CSV sem as colunas: {sorted(faltando)}")
    df = df.dropna(subset=["id", "texto"]).copy()
    df["texto"] = df["texto"].astype(str).str.strip()
    df = df[df["texto"] != ""]
    df["data"] = pd.to_datetime(df["data"], errors="coerce").dt.date
    return df.drop_duplicates(subset="id")


def em_lotes(itens: list, tamanho: int):
    for i in range(0, len(itens), tamanho):
        yield itens[i : i + tamanho]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline de análise de feedbacks com IA")
    parser.add_argument("--input", required=True, type=Path, help="CSV com feedbacks")
    parser.add_argument("--provider", default=config.PROVIDER, choices=["openai", "gemini", "mock"])
    parser.add_argument("--batch-size", type=int, default=10, help="Feedbacks por chamada à IA")
    parser.add_argument("--limit", type=int, default=None, help="Máx. de feedbacks a analisar")
    parser.add_argument("--export-dir", default="output", help="Pasta dos CSVs para o Power BI")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    df = carregar_csv(args.input)
    log.info("CSV carregado: %d feedbacks válidos", len(df))

    engine = database.get_engine()
    database.init_db(engine)
    novos = database.insert_feedbacks(engine, df)
    log.info("Novos feedbacks inseridos no banco: %d", novos)

    pendentes = database.pending_feedbacks(engine, args.limit)
    log.info("Feedbacks pendentes de análise: %d", len(pendentes))

    if pendentes:
        analyzer = get_analyzer(args.provider)
        log.info("Analisando com: %s (%s)", args.provider, analyzer.nome_modelo)
        total = 0
        for n, lote in enumerate(em_lotes(pendentes, args.batch_size), start=1):
            try:
                resultados = analyzer.analyze(lote)
            except Exception:
                log.exception("Lote %d falhou; será reprocessado na próxima execução", n)
                continue
            total += database.save_analyses(engine, resultados, analyzer.nome_modelo)
            log.info("Lote %d: %d/%d analisados", n, len(resultados), len(lote))
        log.info("Total analisado nesta execução: %d", total)

    database.refresh_views(engine)
    arquivos = database.export_csv(engine, args.export_dir)
    for a in arquivos:
        log.info("Exportado: %s", a)
    log.info("Pronto! Conecte o Power BI ao banco ou importe os CSVs de '%s'.", args.export_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
