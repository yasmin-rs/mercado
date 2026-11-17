"""Testes da base pública da demo e da análise de hábitos (modelo FALSO, nenhuma chamada real)."""
import json
from types import SimpleNamespace

import pandas as pd

from mercado import habitos as hb
from mercado.demo import anonimizar


def itens_exemplo() -> pd.DataFrame:
    return pd.DataFrame({
        "chave": ["35260312345678000190650010000000011000000010"] * 3 + [None],
        "arquivo": ["n1", "n1", "n1", "kaggle_x"],
        "fonte": ["pessoal", "pessoal", "pessoal", "kaggle"],
        "emissao": ["2026-03-01 10:15:00", "2026-03-01 10:15:00", "2026-03-01 10:15:00", "2024-01-01 09:00:00"],
        "loja": ["loja_A"] * 3 + ["loja_?"],
        "produto": ["SACOLA SAMS", "T GRAV CLEARBLUE", "CHOC LACTA 90G", "PAO"],
        "produto_padrao": ["sacola", "teste de gravidez", "chocolate", "pão"],
        "categoria_padrao": ["utilidades", "farmacia", "doces_e_snacks", "padaria"],
        "marca_padrao": [None, "Clearblue", "Lacta", None],
        "quantidade": [1, 1, 2, 1], "unidade": ["UN"] * 4, "valor_total": [0.5, 30.0, 12.0, 1.0],
    })


def test_base_publica_nao_vaza_loja_saude_nem_horario():
    publico = anonimizar(itens_exemplo())
    texto = publico.to_csv(index=False)
    for proibido in ("SAMS", "GRAV", "gravidez", "Clearblue", "Lacta", "3526", "10:15", "n1"):
        assert proibido not in texto
    assert "item de farmácia" in texto and set(publico["compra"]) == {"C001", "C002"}
    assert "pão" in texto and "pão" not in anonimizar(itens_exemplo(), com_kaggle=False).to_csv(index=False)


def test_resumo_mascara_farmacia_e_estima_mensal():
    resumo = hb.resumir(itens_exemplo()[lambda d: d["fonte"] == "pessoal"], hoje=pd.Timestamp("2026-03-10"))
    produtos = {p["produto"] for p in resumo["produtos_que_mais_pesam"]}
    assert "teste de gravidez" not in produtos and "item de farmácia" in produtos
    assert resumo["gasto_total"] == 42.5 and resumo["meses_com_notas"] == 1


def analise(*sugestoes) -> hb.Analise:
    return hb.Analise(diagnostico="Gasto concentrado em doces e utilidades.",
                      sugestoes=[hb.Sugestao(**s) for s in sugestoes])


def test_conferir_descarta_sugestao_sem_base_ou_exagerada():
    resumo = hb.resumir(itens_exemplo()[lambda d: d["fonte"] == "pessoal"])
    filtrada, descartes = hb.conferir(analise(
        {"titulo": "Menos chocolate", "acao": "Comprar chocolate só no fim de semana.", "evidencia": "Chocolate",
         "economia_mensal_estimada": 6},
        {"titulo": "Cortar vinho", "acao": "Trocar o vinho por suco natural.", "evidencia": "vinho"},
        {"titulo": "Zerar doces", "acao": "Não comprar mais doces de jeito nenhum.", "evidencia": "doces_e_snacks",
         "economia_mensal_estimada": 500},
    ), resumo)
    assert [s.evidencia for s in filtrada.sugestoes] == ["chocolate"]
    assert len(descartes) == 2


def test_analisar_usa_cache_e_nao_chama_de_novo(tmp_path, monkeypatch):
    chamadas = []
    resposta = json.dumps({"diagnostico": "Você gasta bastante com doces e snacks.", "pontos_positivos": [],
                           "sugestoes": [{"titulo": "Menos chocolate", "acao": "Levar lista e evitar o corredor de doces.",
                                          "evidencia": "chocolate", "economia_mensal_estimada": None}],
                           "alerta": None})

    def falso(**kw):
        chamadas.append(1)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=resposta))])

    monkeypatch.setattr(hb, "_completar", lambda conversa, parametros: falso().choices[0].message.content)
    resumo = hb.resumir(itens_exemplo()[lambda d: d["fonte"] == "pessoal"])
    primeiro = hb.analisar(resumo, "economizar", "gemini/falso", pasta_cache=tmp_path)
    segundo = hb.analisar(resumo, "economizar", "gemini/falso", pasta_cache=tmp_path)
    assert primeiro == segundo and len(chamadas) == 1
    assert primeiro["analise"]["sugestoes"][0]["evidencia"] == "chocolate"


def test_conferir_aceita_nome_com_acento_ou_espaco():
    resumo = {"categorias": [{"categoria": "doces_e_snacks", "gasto_mensal_estimado": 30.0}],
              "produtos_que_mais_pesam": [{"produto": "pão francês", "gasto_mensal_estimado": 10.0}], "recorrentes": []}
    filtrada, descartes = hb.conferir(analise(
        {"titulo": "Menos doce", "acao": "Comprar doces só no fim de semana.", "evidencia": "Doces e Snacks"},
        {"titulo": "Pão", "acao": "Comprar pão só para o dia seguinte.", "evidencia": "pao frances"},
    ), resumo)
    assert [s.evidencia for s in filtrada.sugestoes] == ["doces_e_snacks", "pão francês"] and not descartes
