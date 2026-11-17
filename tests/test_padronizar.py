"""Testes da padronização com modelo FALSO (nenhuma chamada real)."""
import json
from types import SimpleNamespace

import pandas as pd

from mercado import padronizar as pz


def resposta(itens):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"itens": itens})))])


def test_limpar_nome_tira_codigos_fiscais():
    assert pz.limpar_nome("#1700900#18069000#TRUFA CHOC  SPEC 30") == "TRUFA CHOC SPEC 30"
    assert pz.limpar_nome("ovo bco ext") == "OVO BCO EXT"


def test_json_invalido_pede_de_novo(monkeypatch):
    respostas = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="não é json"))]),
                 resposta([{"original": "OVO BCO", "produto": "ovo", "categoria": "proteina", "marca": None}])]
    monkeypatch.setattr(pz.litellm, "completion", lambda **kw: respostas.pop(0))
    itens = pz.chamar_modelo([{"nome": "OVO BCO"}], "ollama/falso")
    assert [i.produto for i in itens] == ["ovo"]


def test_categoria_fora_da_lista_e_rejeitada(monkeypatch):
    ruim = resposta([{"original": "X", "produto": "xx", "categoria": "inventada", "marca": None}])
    monkeypatch.setattr(pz.litellm, "completion", lambda **kw: ruim)
    assert pz.chamar_modelo([{"nome": "X"}], "ollama/falso", tentativas=2) == []


def test_item_pulado_pelo_modelo_vira_falha(monkeypatch):
    monkeypatch.setattr(pz.litellm, "completion", lambda **kw: resposta(
        [{"original": "OVO BCO", "produto": "Ovo", "categoria": "proteina", "marca": ""}]))
    novos = pd.DataFrame({"nome_limpo": ["OVO BCO", "BANANA"], "unidade": ["UN", "KG"], "preco_unitario": [13.99, 9.9]})
    linhas, falhas = pz.padronizar_novos(novos, "ollama/falso")
    assert linhas == [{"original": "OVO BCO", "produto": "ovo", "categoria": "proteina", "marca": ""}]
    assert falhas == ["BANANA"]


def test_correcao_manual_ganha_do_modelo(tmp_path, monkeypatch):
    monkeypatch.setattr(pz, "ARQUIVO_DICIONARIO", tmp_path / "dic.csv")
    monkeypatch.setattr(pz, "ARQUIVO_CORRECOES", tmp_path / "cor.csv")
    pd.DataFrame([{"original": "QUILO CLIENTE", "produto": "queijo", "categoria": "laticinios", "marca": ""}]).to_csv(
        tmp_path / "dic.csv", index=False)
    pd.DataFrame([{"original": "QUILO CLIENTE", "produto": "comida por quilo", "categoria": "refeicao_fora",
                   "marca": ""}]).to_csv(tmp_path / "cor.csv", index=False)
    assert pz.dicionario_atual()["QUILO CLIENTE"]["categoria"] == "refeicao_fora"


def test_foto_farmacia_valor_liquido_vira_bruto():
    from mercado.ler_foto import ItemFoto, NotaFoto, conferir, normalizar
    nota = NotaFoto(itens=[ItemFoto(produto="CORISTINA D", quantidade=1, unidade="UN", preco_unitario=26.12,
                                    valor_total=20.11, desconto=6.01)], valor_total=26.12, valor_a_pagar=20.11)
    assert conferir(normalizar(nota)) == [] and nota.itens[0].valor_total == 26.12


def test_foto_com_item_faltando_nao_confere():
    from mercado.ler_foto import ItemFoto, NotaFoto, conferir, normalizar
    nota = NotaFoto(itens=[ItemFoto(produto="PAO", quantidade=1, unidade="UN", preco_unitario=5, valor_total=5)],
                    valor_total=20, valor_a_pagar=20)
    assert conferir(normalizar(nota))
