import pandas as pd

from mercado.recorrencia import calcular_recorrencia


def itens(produto, datas, categoria="padaria", valor=5.0, fonte="pessoal"):
    return pd.DataFrame({"produto_padrao": produto, "categoria_padrao": categoria, "fonte": fonte,
                         "emissao": pd.to_datetime(datas), "valor_total": valor})


def test_intervalo_e_status():
    df = pd.concat([itens("pão francês", ["2026-11-01", "2026-11-08", "2026-11-15"]),
                    itens("café", ["2026-09-01", "2026-10-01", "2026-10-31"], "mercearia"),
                    itens("arroz", ["2025-11-02", "2025-12-02", "2026-01-01"], "mercearia"),
                    itens("comida por quilo", ["2026-11-01", "2026-11-02"], "refeicao_fora"),
                    itens("leite", ["2026-11-10"], "laticinios")])
    r = calcular_recorrencia(df, hoje=pd.Timestamp("2026-11-20")).set_index("produto")
    assert r.loc["pão francês", "intervalo_medio_dias"] == 7.0 and r.loc["pão francês", "status"] == "comprar"
    assert r.loc["café", "status"] == "ok"            # próxima: 30/11
    assert r.loc["arroz", "status"] == "sem_registro"  # previsto para janeiro, nada desde então
    assert "comida por quilo" not in r.index and "leite" not in r.index  # refeição fora / 1 compra só


def test_duas_compras_nao_viram_previsao():
    r = calcular_recorrencia(itens("chocolate", ["2026-02-09", "2026-05-21"], "doces_e_snacks"),
                             hoje=pd.Timestamp("2026-09-01"))
    assert r.loc[0, "status"] == "poucos_dados" and r.loc[0, "proxima_prevista"] is None


def test_perecivel_com_intervalo_enorme_e_esporadico():
    r = calcular_recorrencia(itens("batata", ["2025-09-01", "2026-02-05", "2026-07-20"], "hortifruti"),
                             hoje=pd.Timestamp("2026-08-01"))
    assert r.loc[0, "status"] == "esporadico"  # hortifrúti "a cada 160 dias" não é hábito de recompra


def test_notas_de_exemplo_nao_entram():
    df = pd.concat([itens("batata", ["2025-03-04"], "hortifruti", fonte="kaggle"),
                    itens("batata", ["2026-02-05", "2026-02-09"], "hortifruti")])
    r = calcular_recorrencia(df, hoje=pd.Timestamp("2026-02-10"))
    assert r.loc[0, "compras"] == 2 and r.loc[0, "intervalo_medio_dias"] == 4.0


def test_mesmo_dia_conta_uma_compra():
    r = calcular_recorrencia(itens("ovo", ["2026-11-01 10:00", "2026-11-01 18:00"]), hoje=pd.Timestamp("2026-11-20"))
    assert r.empty
