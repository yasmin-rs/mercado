"""Despensa: "preciso comprar X?", última compra, lista de compras (checklist) e o retorno da pessoa.

Tudo em código, sem IA (custo zero e resposta explicável). Para cada produto estima quanto tempo ele
costuma durar, nesta ordem de confiança:
1. RETORNO da pessoa ("acabou" depois de N dias / "ainda tenho" depois de N dias), ajustado pela
   quantidade comprada (consumo por dia);
2. HÁBITO de compra: intervalo médio entre compras (só se for um padrão confiável, ver recorrencia.py),
   ajustado se a última compra foi maior que o normal (10 kg de farinha dura mais que 1 kg);
3. PALPITE pela categoria (hortifrúti ~7 dias, mercearia ~30...), deixado explícito na resposta.

O retorno vira dado: cada "acabou"/"ainda tenho" melhora a próxima estimativa daquele produto.
"""
import difflib
import json
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

from mercado.recorrencia import CATEGORIAS_RECORRENTES, INTERVALO_MAXIMO_DIAS, MIN_COMPRAS

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_RETORNO = RAIZ / "data" / "retorno_despensa.csv"
ARQUIVO_LISTA = RAIZ / "data" / "lista_compras.json"
DURACAO_PADRAO_DIAS = {"hortifruti": 7, "padaria": 3, "laticinios": 10, "proteina": 7, "bebidas": 10,
                       "doces_e_snacks": 14, "congelados": 30, "mercearia": 30, "higiene": 45, "limpeza": 45}
FOLGA_DIAS = 3
DIAS_A_MAIS_AINDA_TEM = 7  # "ainda tenho" hoje: supõe pelo menos mais uma semana
COLUNAS_RETORNO = ["registrado_em", "produto", "data_compra", "quantidade", "unidade", "resposta", "dias"]
PALAVRAS_IGNORADAS = {"preciso", "comprar", "compro", "tenho", "ainda", "acabou", "acabando", "de", "do", "da",
                      "o", "a", "os", "as", "um", "uma", "mais", "devo", "vou", "tem", "precisa", "quando", "eu"}


# ---------------------------------------------------------------- nomes
def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in sem_acento.lower()).split())


def _singular(palavra: str) -> str:
    return palavra[:-1] if len(palavra) > 3 and palavra.endswith("s") else palavra


def candidatos_produto(pergunta: str, produtos: list[str], limite: int = 5) -> list[str]:
    """'preciso comprar ovos?' -> ['ovo']; 'tem pão?' -> ['pão de queijo', 'pão francês'] (a pessoa escolhe).
    Nome inteiro contido na pergunta ganha; senão, produtos com uma palavra parecida (empates = ambíguo)."""
    texto = " ".join(_singular(p) for p in normalizar(pergunta).split())
    contidos = [p for p in produtos if f" {' '.join(_singular(x) for x in normalizar(p).split())} " in f" {texto} "]
    if contidos:
        return [max(contidos, key=len)]
    palavras = [p for p in texto.split() if p not in PALAVRAS_IGNORADAS and len(p) >= 3]
    notas = {}
    for produto in produtos:
        partes = [_singular(x) for x in normalizar(produto).split()]
        nota = max((difflib.SequenceMatcher(None, palavra, parte).ratio() for palavra in palavras for parte in partes),
                   default=0.0)
        if nota >= 0.8:
            notas[produto] = round(nota, 2)
    if not notas:
        return []
    melhor = max(notas.values())
    return sorted(p for p, n in notas.items() if n == melhor)[:limite]


def encontrar_produto(pergunta: str, produtos: list[str]) -> str | None:
    """Só quando a resposta é uma: o produto; ambíguo ou nada parecido: None."""
    candidatos = candidatos_produto(pergunta, produtos)
    return candidatos[0] if len(candidatos) == 1 else None


# ---------------------------------------------------------------- retorno (feedback)
def ler_retorno(arquivo: Path | None = ARQUIVO_RETORNO) -> pd.DataFrame:
    if arquivo and arquivo.exists():
        return pd.read_csv(arquivo, parse_dates=["registrado_em", "data_compra"])
    return pd.DataFrame(columns=COLUNAS_RETORNO)


def registrar_retorno(retorno: pd.DataFrame, produto: str, data_compra, quantidade: float, unidade: str,
                      resposta: str, hoje, arquivo: Path | None = ARQUIVO_RETORNO) -> pd.DataFrame:
    """resposta: 'acabou' ou 'ainda_tem'. Sem arquivo (demo), só devolve a tabela atualizada."""
    hoje, data_compra = pd.Timestamp(hoje).normalize(), pd.Timestamp(data_compra).normalize()
    linha = {"registrado_em": hoje, "produto": produto, "data_compra": data_compra, "quantidade": quantidade,
             "unidade": unidade, "resposta": resposta, "dias": int((hoje - data_compra).days)}
    nova = pd.DataFrame([linha])
    retorno = nova if retorno.empty else pd.concat([retorno, nova], ignore_index=True)
    if arquivo:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        retorno.to_csv(arquivo, index=False)
    return retorno


# ---------------------------------------------------------------- estimativa
def compras_do_produto(itens: pd.DataFrame, produto: str) -> pd.DataFrame:
    """Uma linha por dia de compra do produto (só notas da própria pessoa e com data)."""
    base = itens[(itens["produto_padrao"] == produto) & itens["emissao"].notna()]
    if "fonte" in base:
        base = base[base["fonte"] != "kaggle"]
    base = base.assign(dia=pd.to_datetime(base["emissao"]).dt.normalize())
    return (base.groupby("dia").agg(quantidade=("quantidade", "sum"), unidade=("unidade", "first"),
                                    valor=("valor_total", "sum"), loja=("loja", "first"),
                                    categoria=("categoria_padrao", "first"))
            .reset_index().sort_values("dia"))


def estimar_duracao(compras: pd.DataFrame, retorno: pd.DataFrame) -> tuple[float, str, str]:
    """-> (dias que a ÚLTIMA compra deve durar, como foi estimado, confiança alta/media/baixa)."""
    ultima = compras.iloc[-1]
    categoria = ultima["categoria"]
    acabou = retorno[retorno["resposta"] == "acabou"]
    ainda = retorno[retorno["resposta"] == "ainda_tem"]
    if len(acabou):
        mesma_unidade = acabou[(acabou["unidade"] == ultima["unidade"]) & (acabou["quantidade"] > 0)]
        if len(mesma_unidade):
            consumo_dia = (mesma_unidade["quantidade"] / mesma_unidade["dias"].clip(lower=1)).median()
            dias, como = ultima["quantidade"] / consumo_dia, "pelo seu retorno (quanto você usa por dia)"
        else:
            dias, como = float(acabou["dias"].median()), "pelo seu retorno"
        confianca = "alta"
    elif len(compras) >= MIN_COMPRAS and compras["dia"].diff().dt.days.mean() <= INTERVALO_MAXIMO_DIAS.get(categoria, 90):
        dias = float(compras["dia"].diff().dt.days.mean())
        tipica = compras["quantidade"].iloc[:-1].median()
        if tipica and ultima["quantidade"] > 0:  # compra maior que o normal dura mais (e vice-versa)
            dias *= min(4.0, max(0.5, ultima["quantidade"] / tipica))
        como, confianca = f"pelo seu hábito, {len(compras)} compras registradas", "media"
    else:
        dias = float(DURACAO_PADRAO_DIAS.get(categoria, 14))
        como, confianca = "palpite pelo tipo de produto, ainda sem histórico seu", "baixa"
    if len(ainda) and ainda["dias"].max() + DIAS_A_MAIS_AINDA_TEM > dias:  # "ainda tenho" após N dias: dura mais
        dias, como = float(ainda["dias"].max() + DIAS_A_MAIS_AINDA_TEM), como + ", ajustado pelo seu \"ainda tenho\""
    return round(dias, 1), como, confianca


def situacao(hoje: pd.Timestamp, ultima: pd.Timestamp, dias: float) -> str:
    restante = (ultima + pd.Timedelta(days=round(dias)) - hoje).days
    if restante > FOLGA_DIAS:
        return "tem"
    return "acabando" if restante >= -FOLGA_DIAS else "acabou"


def _qtd(quantidade: float, unidade: str) -> str:
    unidade = str(unidade).lower()
    if unidade in ("kg", "l"):
        return f"{quantidade:.3g} {unidade}"
    return f"{quantidade:.0f} {'un' if unidade in ('un', 'nan') else unidade}"


def consultar(itens: pd.DataFrame, produto: str, retorno: pd.DataFrame, hoje=None) -> dict | None:
    """Resposta para "preciso comprar <produto>?"."""
    hoje = pd.Timestamp(hoje or date.today()).normalize()
    compras = compras_do_produto(itens, produto)
    if compras.empty:
        return None
    ultima = compras.iloc[-1]
    dias, como, confianca = estimar_duracao(compras, retorno[retorno["produto"] == produto])
    status = situacao(hoje, ultima["dia"], dias)
    ha = (hoje - ultima["dia"]).days
    quando = "hoje" if ha == 0 else ("ontem" if ha == 1 else f"há {ha} dias")
    compra = f"Você comprou {_qtd(ultima['quantidade'], ultima['unidade'])} de **{produto}** em {ultima['dia']:%d/%m} ({quando})"
    previsao = f"costuma durar ~{dias:.0f} dias ({como})"
    antigo = ha > 2 * dias  # muito além do previsto: compra fora das notas ou deixou de comprar
    texto = {
        "tem": f"{compra} e {previsao}: **provavelmente ainda tem em casa**.",
        "acabando": f"{compra} e {previsao}: **deve estar acabando**. Vale conferir a despensa.",
        "acabou": f"{compra} e {previsao}: **provavelmente já acabou**. Coloque na lista.",
    }[status]
    if antigo:
        texto += " Faz tempo que ele não aparece nas suas notas: talvez tenha comprado sem registrar."
    return {"produto": produto, "categoria": ultima["categoria"], "ultima_compra": ultima["dia"],
            "quantidade": float(ultima["quantidade"]), "unidade": ultima["unidade"], "dias_desde": ha,
            "duracao_dias": dias, "como": como, "confianca": confianca, "status": status, "texto": texto,
            "compras": len(compras), "antigo": antigo}


# ---------------------------------------------------------------- última compra e lista
def produtos_proprios(itens: pd.DataFrame) -> list[str]:
    """Produtos que aparecem nas notas da própria pessoa (as de exemplo do Kaggle não contam)."""
    base = itens[itens["fonte"] != "kaggle"] if "fonte" in itens else itens
    return sorted(base["produto_padrao"].dropna().unique())


def ultima_compra(itens: pd.DataFrame) -> dict | None:
    proprias = itens[itens["emissao"].notna()]
    if "fonte" in proprias:
        proprias = proprias[proprias["fonte"] != "kaggle"]
    if proprias.empty:
        return None
    arquivo = proprias.loc[pd.to_datetime(proprias["emissao"]).idxmax(), "arquivo"]
    nota = proprias[proprias["arquivo"] == arquivo]
    return {"data": pd.Timestamp(nota["emissao"].iloc[0]), "loja": nota["loja"].iloc[0],
            "total": float(nota["valor_total"].sum()), "itens": nota}


def lista_sugerida(itens: pd.DataFrame, retorno: pd.DataFrame, hoje=None) -> pd.DataFrame:
    """Produtos que provavelmente acabaram ou estão acabando. Só entra compra recente (até 2x a duração
    estimada): batata comprada há 9 meses não é "acabou agora", é compra esporádica ou nota faltando."""
    hoje = pd.Timestamp(hoje or date.today()).normalize()
    base = itens[itens["categoria_padrao"].isin(CATEGORIAS_RECORRENTES)]
    linhas = []
    for produto in base["produto_padrao"].dropna().unique():
        r = consultar(itens, produto, retorno, hoje)
        if not r or r["status"] == "tem":
            continue
        if r["antigo"]:
            continue
        linhas.append(r)
    colunas = ["produto", "categoria", "status", "ultima_compra", "dias_desde", "duracao_dias", "confianca", "como"]
    lista = pd.DataFrame(linhas, columns=colunas + ["texto", "quantidade", "unidade", "compras"])[colunas]
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    return lista.assign(_o=lista["confianca"].map(ordem)).sort_values(["_o", "produto"]).drop(columns="_o") \
        .reset_index(drop=True)


def ler_lista(arquivo: Path | None = ARQUIVO_LISTA) -> dict:
    if arquivo and arquivo.exists():
        return json.loads(arquivo.read_text(encoding="utf-8"))
    return {"marcados": [], "manuais": []}


def salvar_lista(lista: dict, arquivo: Path | None = ARQUIVO_LISTA) -> None:
    if arquivo:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
