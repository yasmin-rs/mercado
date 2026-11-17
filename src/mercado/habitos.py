"""Análise de hábitos com IA: o histórico de compras comparado com uma meta da pessoa.

Divisão de trabalho:
- o CÓDIGO calcula os números (gasto por categoria, por mês, produtos que mais pesam, recorrência);
- a IA recebe SÓ esse resumo agregado (nada de loja, nota ou nome bruto; farmácia mascarada)
  e escreve o diagnóstico e as sugestões;
- o CÓDIGO confere a resposta: cada sugestão precisa apontar uma categoria/produto que existe no
  resumo, e a economia prometida não pode passar do que a pessoa gasta com aquilo. O que falhar
  é descartado (e contado), em vez de mostrado.

1 chamada por análise; o resultado fica em cache (mesmo histórico + mesma meta = sem nova chamada).

    uv run python -m mercado.habitos --meta economizar
"""
import argparse
import hashlib
import json
import os
import sys
import unicodedata
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from mercado.demo import mascarar_sensiveis
from mercado.padronizar import ARQUIVO_SAIDA, _completar
from mercado.recorrencia import calcular_recorrencia

RAIZ = Path(__file__).resolve().parents[2]
PASTA_CACHE = RAIZ / "outputs" / "analises"
load_dotenv(RAIZ / ".env")

METAS = {
    "economizar": "gastar menos no mercado sem passar vontade",
    "comer_melhor": "comer de forma mais saudável (mais comida de verdade, menos ultraprocessado)",
    "ganhar_massa": "ganhar massa muscular (mais proteína, refeições completas)",
    "emagrecer": "emagrecer (menos doces, snacks, bebidas açucaradas e refeições fora)",
}
ROTULOS_METAS = {"economizar": "💰 Economizar", "comer_melhor": "🥦 Comer melhor",
                 "ganhar_massa": "💪 Ganhar massa", "emagrecer": "⚖️ Emagrecer"}


class Sugestao(BaseModel):
    titulo: str = Field(min_length=3, max_length=80)
    acao: str = Field(min_length=10, max_length=300, description="o que fazer, concreto")
    evidencia: str = Field(description="categoria ou produto do resumo em que a sugestão se baseia")
    economia_mensal_estimada: float | None = Field(default=None, ge=0)


class Analise(BaseModel):
    diagnostico: str = Field(min_length=20, max_length=600)
    pontos_positivos: list[str] = Field(default_factory=list, max_length=3)
    sugestoes: list[Sugestao] = Field(min_length=1, max_length=4)
    alerta: str | None = None


PROMPT = """Você é um assistente de compras de supermercado no Brasil. Recebe um RESUMO do histórico de
compras de uma pessoa (já calculado, em reais) e a META dela. Escreva em português, tom direto e gentil.

Regras:
- Use SOMENTE os números do resumo. Não invente preços, produtos ou categorias que não estão lá.
- Cada sugestão deve ser concreta (o que trocar, reduzir ou manter) e ter em "evidencia" o nome EXATO
  de uma categoria (campo "categoria") ou produto (campo "produto") do resumo.
- Priorize o que mais pesa para a META: comece pelas categorias/produtos de maior gasto ou mais ligados
  à meta. Compra única e pontual (ex.: 1 sapato) importa menos do que gasto que se repete.
- "economia_mensal_estimada": estime quando possível (ex.: reduzir 30% do "gasto_mensal_estimado"
  daquela categoria/produto), em R$/mês; nunca maior do que esse gasto mensal. Se não fizer sentido, null.
- Diagnóstico e pontos positivos precisam citar números do resumo (R$ ou %); não elogie algo que o
  resumo não mostra (ex.: não diga "consome bastante hortifrúti" se a categoria tem pouco gasto).
- O histórico pode estar incompleto (nem toda compra vira nota). Não trate ausência como "não compra".
- Não dê orientação médica nem dieta; para saúde, sugira procurar um profissional no "alerta".
- "pontos_positivos": até 3 hábitos bons que o resumo mostra.

Responda SOMENTE com JSON:
{"diagnostico": "...", "pontos_positivos": ["..."],
 "sugestoes": [{"titulo": "...", "acao": "...", "evidencia": "...", "economia_mensal_estimada": null}],
 "alerta": null}"""


# ---------------------------------------------------------------- números (sem IA)
def resumir(itens: pd.DataFrame, hoje: pd.Timestamp | None = None) -> dict:
    """Resumo agregado do histórico — é tudo o que a IA vê."""
    itens = mascarar_sensiveis(itens[itens["produto_padrao"].notna()])
    total = float(itens["valor_total"].sum())
    datadas = itens[itens["emissao"].notna()]
    emissao = pd.to_datetime(datadas["emissao"])
    meses = max(1, emissao.dt.to_period("M").nunique())
    gasto_datado = float(datadas["valor_total"].sum())

    def mensal(valor: float, base: float) -> float:
        # gasto mensal estimado: proporção daquele item no total datado / nº de meses com notas
        return round(valor / base * gasto_datado / meses, 2) if base else 0.0

    por_categoria = itens.groupby("categoria_padrao")["valor_total"].sum().sort_values(ascending=False)
    por_produto = itens.groupby(["produto_padrao", "categoria_padrao"])["valor_total"].agg(["sum", "count"])
    top = por_produto.sort_values("sum", ascending=False).head(15).reset_index()
    rec = calcular_recorrencia(itens, hoje)
    return {
        "periodo": f"{emissao.min():%m/%Y} a {emissao.max():%m/%Y}" if len(emissao) else "sem datas",
        "compras_registradas": int(itens["arquivo"].nunique()),
        "meses_com_notas": int(meses),
        "gasto_total": round(total, 2),
        "gasto_medio_por_mes_com_notas": round(gasto_datado / meses, 2),
        "categorias": [{"categoria": c, "gasto": round(v, 2), "pct": round(100 * v / total, 1),
                        "gasto_mensal_estimado": mensal(v, total)} for c, v in por_categoria.items()],
        "produtos_que_mais_pesam": [{"produto": r.produto_padrao, "categoria": r.categoria_padrao,
                                     "gasto": round(r.sum, 2), "vezes": int(r.count),
                                     "gasto_mensal_estimado": mensal(r.sum, total)} for r in top.itertuples()],
        # só padrões confiáveis (3+ compras, intervalo plausível): 2 compras não dizem "a cada X dias"
        "recorrentes": [{"produto": r.produto, "a_cada_dias": r.intervalo_medio_dias, "gasto_medio": r.gasto_medio}
                        for r in rec.itertuples() if r.status not in ("poucos_dados", "esporadico")],
    }


# ---------------------------------------------------------------- conferência da resposta
def normalizar_nome(texto: str) -> str:
    """'Hortifrúti' / 'doces e snacks' -> 'hortifruti' / 'doces_e_snacks' (a IA nem sempre usa o nome exato)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "_".join(sem_acento.lower().replace("-", " ").split())


def conferir(analise: Analise, resumo: dict) -> tuple[Analise, list[str]]:
    """Descarta sugestões sem base no resumo. -> (análise filtrada, motivos dos descartes)."""
    limites = {c["categoria"]: c["gasto_mensal_estimado"] for c in resumo["categorias"]}
    limites.update({p["produto"]: p["gasto_mensal_estimado"] for p in resumo["produtos_que_mais_pesam"]})
    limites.update({r["produto"]: None for r in resumo["recorrentes"] if r["produto"] not in limites})
    nomes = {normalizar_nome(nome): nome for nome in limites}
    validas, descartes = [], []
    for s in analise.sugestoes:
        nome = nomes.get(normalizar_nome(s.evidencia))
        if nome is None:
            descartes.append(f"'{s.titulo}': evidência '{s.evidencia}' não está no histórico")
        elif s.economia_mensal_estimada and limites[nome] is not None and s.economia_mensal_estimada > limites[nome] + 0.01:
            descartes.append(f"'{s.titulo}': promete economizar R$ {s.economia_mensal_estimada:.2f}/mês, "
                             f"mais do que o gasto ({limites[nome]:.2f})")
        else:
            validas.append(s.model_copy(update={"evidencia": nome}))
    return analise.model_copy(update={"sugestoes": validas}), descartes


# ---------------------------------------------------------------- modelo
def chave_cache(resumo: dict, meta: str, modelo: str) -> str:
    texto = json.dumps([resumo, meta, modelo, PROMPT], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(texto.encode()).hexdigest()[:16]


def em_cache(resumo: dict, meta: str, modelo: str, pasta_cache: Path = PASTA_CACHE) -> dict | None:
    """Análise já feita para este histórico + meta (sem chamar o modelo), ou None."""
    arquivo = pasta_cache / f"{meta}_{chave_cache(resumo, meta, modelo)}.json"
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None


def analisar(resumo: dict, meta: str, modelo: str, tentativas: int = 3, pasta_cache: Path | None = PASTA_CACHE) -> dict:
    """Resumo + meta -> {"analise": Analise (dict), "descartes": [...], "modelo": ...}. Usa cache em disco."""
    arquivo = pasta_cache / f"{meta}_{chave_cache(resumo, meta, modelo)}.json" if pasta_cache else None
    if arquivo and arquivo.exists():
        return json.loads(arquivo.read_text(encoding="utf-8"))
    conversa = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps({"meta": METAS[meta], "resumo": resumo}, ensure_ascii=False)}]
    parametros = {"model": modelo, "temperature": 0.3, "timeout": 120, "response_format": {"type": "json_object"}}
    if modelo.startswith("ollama"):
        parametros["api_base"] = os.getenv("API_BASE", "http://localhost:11434")
    for _ in range(tentativas):
        texto = _completar(conversa, parametros)
        try:
            analise, descartes = conferir(Analise.model_validate_json(texto), resumo)
            break
        except ValidationError as e:
            conversa += [{"role": "assistant", "content": texto},
                         {"role": "user", "content": f"JSON inválido: {str(e)[:300]}. Responda só o JSON corrigido."}]
    else:
        raise ValueError("o modelo não devolveu uma análise válida")
    resultado = {"meta": meta, "modelo": modelo, "analise": analise.model_dump(), "descartes": descartes}
    if arquivo:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    return resultado


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--meta", choices=list(METAS), default="economizar")
    parser.add_argument("--modelo", default=os.getenv("MODEL_NAME", "ollama/qwen2.5:7b"))
    parser.add_argument("--entrada", type=Path, default=ARQUIVO_SAIDA)
    parser.add_argument("--sem-kaggle", action="store_true")
    parser.add_argument("--saida", type=Path, help="também grava o resultado neste JSON (ex.: demo/analises)")
    args = parser.parse_args()
    itens = pd.read_csv(args.entrada)
    if "arquivo" not in itens:
        itens["arquivo"] = itens["compra"]
    if args.sem_kaggle and "fonte" in itens:
        itens = itens[itens["fonte"] != "kaggle"]
    resumo = resumir(itens)
    print(f"1 chamada ao {args.modelo} (~{len(json.dumps(resumo, ensure_ascii=False)) // 3 + 700} tokens de entrada)")
    resultado = analisar(resumo, args.meta, args.modelo)
    print(json.dumps(resultado, ensure_ascii=False, indent=1))
    if args.saida:
        args.saida.mkdir(parents=True, exist_ok=True)
        (args.saida / f"{args.meta}.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    principal()
