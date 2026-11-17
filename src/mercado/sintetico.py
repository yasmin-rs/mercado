"""Perfil SINTÉTICO de exemplo, a partir do dataset público "Groceries dataset" (Kaggle, GPL 2).

O dataset original tem ~15 mil cestas de 3.898 clientes (2014-2015), sem preço nem quantidade, e cada
cliente tem poucas compras (no máximo 11 em 2 anos): não dá para ver hábito de uma pessoa. Aqui as
cestas viram o histórico de UMA pessoa fictícia, para demonstrar a análise de longo prazo:
- sorteia N_COMPRAS idas ao mercado; cada ida junta CESTAS_POR_COMPRA cestas originais (~8 itens);
- cada ida ganha uma data aleatória entre INICIO e FIM (~2 idas por semana);
- produtos traduzidos para o português, com categoria e um preço típico em R$
  (demo/sintetico/traducao_groceries.csv), com variação de ±8% por compra; 1 unidade por item.

Os produtos vêm de compras reais, mas datas, preços e a "pessoa" são inventados: o app mostra o
perfil sempre como sintético e separado do histórico da autora. Semente fixa = resultado reproduzível.

    uv run python -m mercado.sintetico                  # lê data/groceries data.csv
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_ORIGEM = RAIZ / "data" / "groceries data.csv"
ARQUIVO_TRADUCAO = RAIZ / "demo" / "sintetico" / "traducao_groceries.csv"
ARQUIVO_SINTETICO = RAIZ / "demo" / "sintetico" / "itens.csv"
N_COMPRAS = 200
CESTAS_POR_COMPRA = 3
INICIO, FIM = pd.Timestamp("2025-01-01"), pd.Timestamp("2026-11-16")
VARIACAO_PRECO = 0.08
SEMENTE = 42


def gerar(origem: pd.DataFrame, traducao: pd.DataFrame, n_compras: int = N_COMPRAS,
          cestas_por_compra: int = CESTAS_POR_COMPRA, semente: int = SEMENTE) -> pd.DataFrame:
    rng = np.random.default_rng(semente)
    cestas = [g["itemDescription"].unique() for _, g in origem.groupby(["Member_number", "Date"])]
    escolhidas = rng.choice(len(cestas), size=n_compras * cestas_por_compra, replace=False)
    dias = (FIM - INICIO).days
    datas = sorted(INICIO + pd.to_timedelta(rng.integers(0, dias + 1, n_compras), unit="D")
                   + pd.to_timedelta(rng.integers(8 * 60, 21 * 60, n_compras), unit="min"))
    mapa = traducao.set_index("item")
    linhas = []
    for n, data in enumerate(datas):
        grupo = escolhidas[n * cestas_por_compra:(n + 1) * cestas_por_compra]
        produtos = dict.fromkeys(item for c in grupo for item in cestas[c])  # sem repetir na mesma ida
        for item in produtos:
            t = mapa.loc[item]
            preco = round(float(t["preco"]) * (1 + rng.uniform(-VARIACAO_PRECO, VARIACAO_PRECO)), 2)
            linhas.append({"compra": f"S{n + 1:03d}", "fonte": "sintetico", "emissao": data,
                           "loja": "loja_exemplo", "produto_padrao": t["produto"], "categoria_padrao": t["categoria"],
                           "quantidade": 1.0, "unidade": "UN", "valor_total": preco})
    return pd.DataFrame(linhas)


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compras", type=int, default=N_COMPRAS)
    parser.add_argument("--cestas-por-compra", type=int, default=CESTAS_POR_COMPRA)
    args = parser.parse_args()
    origem = pd.read_csv(ARQUIVO_ORIGEM)
    traducao = pd.read_csv(ARQUIVO_TRADUCAO)
    faltando = set(origem["itemDescription"]) - set(traducao["item"])
    if faltando:
        raise SystemExit(f"Itens sem tradução: {sorted(faltando)}")
    itens = gerar(origem, traducao, args.compras, args.cestas_por_compra)
    itens.to_csv(ARQUIVO_SINTETICO, index=False)
    por_compra = itens.groupby("compra")["valor_total"].agg(["size", "sum"])
    meses = itens["emissao"].dt.to_period("M").nunique()
    print(f"{itens['compra'].nunique()} compras, {len(itens)} itens, {itens['produto_padrao'].nunique()} produtos "
          f"-> {ARQUIVO_SINTETICO}")
    print(f"~{por_compra['size'].mean():.1f} itens e R$ {por_compra['sum'].mean():.2f} por compra; "
          f"R$ {itens['valor_total'].sum() / meses:.2f} por mês")


if __name__ == "__main__":
    principal()
