"""Perfil sintético: reproduzível, uma pessoa só, datas no período e todo item traduzido."""
import pandas as pd

from mercado import sintetico as si


def origem() -> pd.DataFrame:
    linhas = [(m, f"2015-01-{d:02d}", item) for m in range(1, 7) for d in (1, 2)
              for item in ("whole milk", "rolls/buns", "soda")]
    return pd.DataFrame(linhas, columns=["Member_number", "Date", "itemDescription"])


def traducao() -> pd.DataFrame:
    return pd.DataFrame({"item": ["whole milk", "rolls/buns", "soda"], "produto": ["leite", "pão francês", "refrigerante"],
                         "categoria": ["laticinios", "padaria", "bebidas"], "preco": [5.0, 6.5, 9.0]})


def test_gera_historico_de_uma_pessoa_reproduzivel():
    a = si.gerar(origem(), traducao(), n_compras=4, cestas_por_compra=3)
    b = si.gerar(origem(), traducao(), n_compras=4, cestas_por_compra=3)
    pd.testing.assert_frame_equal(a, b)
    assert a["compra"].nunique() == 4 and set(a["fonte"]) == {"sintetico"}
    assert a["emissao"].between(si.INICIO, si.FIM + pd.Timedelta(days=1)).all()
    assert a.groupby("compra")["produto_padrao"].apply(lambda s: s.is_unique).all()  # sem repetir na mesma ida
    assert a["valor_total"].between(5.0 * 0.92, 9.0 * 1.08).all()


def test_traducao_cobre_todas_as_categorias_validas():
    from mercado.padronizar import CATEGORIAS
    tabela = pd.read_csv(si.ARQUIVO_TRADUCAO)
    assert tabela["item"].is_unique and set(tabela["categoria"]) <= set(CATEGORIAS) and (tabela["preco"] > 0).all()
