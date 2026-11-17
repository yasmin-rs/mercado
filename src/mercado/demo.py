"""Gera a base PÚBLICA da demonstração (demo/itens.csv), a única parte do histórico que vai para o git.

O app publicado não tem acesso a data/ nem outputs/ (ficam fora do git). Este comando cria uma
cópia mínima e anonimizada do histórico para quem abrir a demo:
- só produto PADRONIZADO (nome bruto pode entregar o mercado: "SACOLA SAMS", marca própria "CRF");
- sem marca, sem chave de acesso (tem CNPJ), sem nome de arquivo; a loja já é apelido (loja_A...);
- data sem horário (o horário revela rotina);
- itens de farmácia viram "item de farmácia": nome de remédio é dado de saúde;
- notas do Kaggle entram marcadas (fonte=kaggle): dataset CC0 (domínio público), só itens de alimentos;
  delas também só vai o nome padronizado (as imagens não são publicadas).

    uv run python -m mercado.demo
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from mercado.padronizar import ARQUIVO_SAIDA

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_DEMO = RAIZ / "demo" / "itens.csv"
CATEGORIAS_SENSIVEIS = {"farmacia": "item de farmácia"}
COLUNAS_PUBLICAS = ["compra", "fonte", "emissao", "loja", "produto_padrao", "categoria_padrao", "quantidade",
                    "unidade", "valor_total"]


def mascarar_sensiveis(itens: pd.DataFrame) -> pd.DataFrame:
    """Troca o nome de produtos de categorias sensíveis por um rótulo genérico (o valor continua)."""
    itens = itens.copy()
    for categoria, rotulo in CATEGORIAS_SENSIVEIS.items():
        itens.loc[itens["categoria_padrao"] == categoria, "produto_padrao"] = rotulo
    return itens


def anonimizar(itens: pd.DataFrame, com_kaggle: bool = True) -> pd.DataFrame:
    if not com_kaggle and "fonte" in itens:
        itens = itens[itens["fonte"] != "kaggle"]
    itens = mascarar_sensiveis(itens[itens["produto_padrao"].notna()])
    emissao = pd.to_datetime(itens["emissao"])
    # id da compra = ordem cronológica (C001, C002...), no lugar da chave/arquivo
    ordem = (itens.assign(_e=emissao).groupby("arquivo")["_e"].min().sort_values(na_position="last").index)
    ids = {arq: f"C{i:03d}" for i, arq in enumerate(ordem, start=1)}
    publico = itens.assign(compra=itens["arquivo"].map(ids), emissao=emissao.dt.date)
    return publico[COLUNAS_PUBLICAS].sort_values(["compra", "categoria_padrao"], ignore_index=True)


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sem-kaggle", action="store_true", help="só as notas da autora")
    args = parser.parse_args()
    publico = anonimizar(pd.read_csv(ARQUIVO_SAIDA), not args.sem_kaggle)
    ARQUIVO_DEMO.parent.mkdir(exist_ok=True)
    publico.to_csv(ARQUIVO_DEMO, index=False)
    print(f"{publico['compra'].nunique()} compras, {len(publico)} itens -> {ARQUIVO_DEMO}")


if __name__ == "__main__":
    principal()
