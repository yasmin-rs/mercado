"""Recorrência: de quanto em quanto tempo cada produto é comprado e quando deve ser comprado de novo.

Só cálculo (pandas), sem IA. O intervalo é entre COMPRAS REGISTRADAS (nem toda compra vira nota),
então só vira previsão quando o padrão é confiável:
- pelo menos MIN_COMPRAS dias de compra do produto (2 compras são só um intervalo, não um padrão);
- intervalo plausível para a categoria (hortifrúti "a cada 170 dias" não é um hábito: é compra
  esporádica ou notas faltando);
- só notas da própria pessoa (as notas de exemplo do Kaggle são de outras pessoas).
"""
import pandas as pd

# categorias que "acabam" e são recompradas; refeição fora, roupa e remédio pontual ficam de fora
CATEGORIAS_RECORRENTES = {"hortifruti", "proteina", "laticinios", "padaria", "mercearia", "bebidas",
                          "doces_e_snacks", "higiene", "limpeza", "congelados"}
# maior intervalo entre compras que ainda parece um hábito de recompra (perecível = curto)
INTERVALO_MAXIMO_DIAS = {"hortifruti": 21, "padaria": 21, "laticinios": 30, "proteina": 30, "bebidas": 45,
                         "doces_e_snacks": 45, "congelados": 60, "mercearia": 90, "higiene": 120, "limpeza": 120}
MIN_COMPRAS = 3
FOLGA_DIAS = 3  # avisa um pouco antes da data prevista
ORDEM_STATUS = ["comprar", "ok", "poucos_dados", "esporadico", "sem_registro"]


def situacao(hoje: pd.Timestamp, proxima: pd.Timestamp, intervalo: float) -> str:
    """ok = ainda não é hora; comprar = perto ou passou da data; sem_registro = passou tanto da
    data (mais de 1 intervalo além) que provavelmente houve compras não registradas."""
    if hoje < proxima - pd.Timedelta(days=FOLGA_DIAS):
        return "ok"
    if hoje <= proxima + pd.Timedelta(days=max(intervalo, 7)):
        return "comprar"
    return "sem_registro"


def calcular_recorrencia(itens: pd.DataFrame, hoje: pd.Timestamp | None = None) -> pd.DataFrame:
    hoje = (hoje or pd.Timestamp.now()).normalize()
    base = itens[itens["categoria_padrao"].isin(CATEGORIAS_RECORRENTES) & itens["emissao"].notna()]
    if "fonte" in base:
        base = base[base["fonte"] != "kaggle"]
    base = base.assign(dia=pd.to_datetime(base["emissao"]).dt.normalize())
    linhas = []
    for (produto, categoria), grupo in base.groupby(["produto_padrao", "categoria_padrao"]):
        dias = sorted(grupo["dia"].unique())
        if len(dias) < 2:
            continue
        intervalos = pd.Series(dias).diff().dropna().dt.days
        media = float(intervalos.mean())
        ultima = pd.Timestamp(dias[-1])
        proxima = ultima + pd.Timedelta(days=round(media))
        if len(dias) < MIN_COMPRAS:
            status = "poucos_dados"
        elif media > INTERVALO_MAXIMO_DIAS.get(categoria, 90):
            status = "esporadico"
        else:
            status = situacao(hoje, proxima, media)
        previsivel = status not in ("poucos_dados", "esporadico")
        linhas.append({
            "produto": produto, "categoria": categoria, "compras": len(dias),
            "intervalo_medio_dias": round(media, 1), "ultima_compra": ultima.date(),
            "proxima_prevista": proxima.date() if previsivel else None,
            "gasto_medio": round(grupo.groupby("dia")["valor_total"].sum().mean(), 2),
            "status": status,
        })
    colunas = ["produto", "categoria", "compras", "intervalo_medio_dias", "ultima_compra", "proxima_prevista",
               "gasto_medio", "status"]
    tabela = pd.DataFrame(linhas, columns=colunas)
    ordem = tabela["status"].map({s: i for i, s in enumerate(ORDEM_STATUS)})
    return (tabela.assign(_o=ordem).sort_values(["_o", "ultima_compra"], ascending=[True, False])
            .drop(columns="_o").reset_index(drop=True))
