"""Testes da despensa: pergunta -> produto, estimativa de duração, retorno da pessoa e lista."""
import pandas as pd

from mercado import despensa as dp


def compras(produto, datas, qtds, categoria="mercearia", unidade="KG", fonte="pessoal"):
    return pd.DataFrame({"produto_padrao": produto, "categoria_padrao": categoria, "fonte": fonte,
                         "arquivo": [f"{produto}{i}" for i in range(len(datas))], "loja": "loja_A",
                         "emissao": pd.to_datetime(datas), "quantidade": qtds, "unidade": unidade,
                         "valor_total": 10.0})


def test_encontra_produto_na_pergunta():
    produtos = ["farinha de trigo", "ovo", "pão francês", "leite condensado", "leite"]
    assert dp.encontrar_produto("Preciso comprar trigo?", produtos) == "farinha de trigo"
    assert dp.encontrar_produto("preciso de ovos?", produtos) == "ovo"
    assert dp.encontrar_produto("tem pao frances?", produtos) == "pão francês"
    assert dp.encontrar_produto("leite condensado acabou?", produtos) == "leite condensado"
    assert dp.encontrar_produto("preciso comprar abacaxi?", produtos) is None
    assert dp.candidatos_produto("tem pão?", produtos + ["pão de queijo"]) == ["pão de queijo", "pão francês"]


def test_trigo_comprado_semana_passada_ainda_tem():
    itens = compras("farinha de trigo", ["2026-11-16"], [10])
    r = dp.consultar(itens, "farinha de trigo", dp.ler_retorno(None), hoje="2026-11-23")
    assert r["status"] == "tem" and r["confianca"] == "baixa"
    assert "10 kg" in r["texto"] and "16/11" in r["texto"] and "ainda tem" in r["texto"]


def test_habito_e_compra_grande_dura_mais():
    itens = compras("arroz", ["2026-08-01", "2026-08-31", "2026-09-30"], [1, 1, 3])
    dias, como, confianca = dp.estimar_duracao(dp.compras_do_produto(itens, "arroz"), dp.ler_retorno(None))
    assert confianca == "media" and dias == 90.0  # 30 dias de hábito x 3 vezes a quantidade normal


def test_retorno_acabou_vira_consumo_por_dia(tmp_path):
    arquivo = tmp_path / "retorno.csv"
    itens = compras("farinha de trigo", ["2026-10-01", "2026-11-16"], [2, 10])
    retorno = dp.registrar_retorno(dp.ler_retorno(arquivo), "farinha de trigo", "2026-10-01", 2, "KG",
                                   "acabou", "2026-10-21", arquivo)
    assert arquivo.exists() and retorno.loc[0, "dias"] == 20
    r = dp.consultar(itens, "farinha de trigo", dp.ler_retorno(arquivo), hoje="2026-11-23")
    assert r["confianca"] == "alta" and r["duracao_dias"] == 100.0  # 2 kg em 20 dias -> 10 kg em 100


def test_ainda_tenho_aumenta_a_duracao():
    itens = compras("café", ["2026-11-01"], [1], unidade="UN")
    retorno = dp.registrar_retorno(dp.ler_retorno(None), "café", "2026-11-01", 1, "UN", "ainda_tem",
                                   "2026-12-15", arquivo=None)
    r = dp.consultar(itens, "café", retorno, hoje="2026-12-15")
    assert r["duracao_dias"] == 51.0 and r["status"] == "tem"  # ainda tinha após 44 dias: +1 semana


def test_lista_ignora_compra_antiga_sem_historico_e_notas_de_exemplo():
    itens = pd.concat([compras("batata", ["2026-02-05"], [1], "hortifruti"),
                       compras("banana", ["2026-11-10"], [1], "hortifruti"),
                       compras("pão", ["2026-11-01"], [1], "padaria", fonte="kaggle")])
    lista = dp.lista_sugerida(itens, dp.ler_retorno(None), hoje="2026-11-20")
    assert list(lista["produto"]) == ["banana"]  # batata: 9 meses atrás; pão: nota de outra pessoa


def test_ultima_compra_ignora_kaggle():
    itens = pd.concat([compras("ovo", ["2026-11-10"], [12], "proteina", "UN"),
                       compras("pão", ["2026-11-15"], [1], "padaria", fonte="kaggle")])
    assert dp.ultima_compra(itens)["data"] == pd.Timestamp("2026-11-10")


def test_ainda_tenho_logo_depois_muda_a_resposta():
    itens = compras("ovo", ["2026-11-10"], [1], "proteina", "UN")
    retorno = dp.registrar_retorno(dp.ler_retorno(None), "ovo", "2026-11-10", 1, "UN", "ainda_tem",
                                   "2026-11-16", arquivo=None)
    assert dp.consultar(itens, "ovo", dp.ler_retorno(None), hoje="2026-11-16")["status"] == "acabando"
    assert dp.consultar(itens, "ovo", retorno, hoje="2026-11-16")["status"] == "tem"  # 6 + 7 = 13 dias
