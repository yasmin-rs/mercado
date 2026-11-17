"""Padroniza os nomes dos produtos das notas com um LLM: "OVO BCO EXT YAN" -> ovo (proteína).

Fluxo:
    outputs/itens.csv (nomes brutos)  ->  limpeza em código (códigos fiscais, espaços)
        -> dicionário salvo (data/produtos_padrao.csv): nome já visto = sem nova chamada
        -> nomes novos vão ao modelo em lotes de 10, com unidade e preço como pista
        -> correções manuais (data/correcoes_produtos.csv) sempre ganham do modelo
    -> outputs/itens_padronizados.csv

Por que o nome padronizado importa também para privacidade: nomes brutos às vezes entregam
o mercado ("SACOLA SAMS", marca própria "CRF"); o que for publicado usa só o nome padronizado.

Uso:
    uv run python -m mercado.padronizar          # modelo do .env (padrão: ollama/qwen2.5:7b, local, US$ 0)
    uv run python -m mercado.padronizar --sim    # sem pedir confirmação

Para corrigir um item: copie a linha de data/produtos_padrao.csv para data/correcoes_produtos.csv
e edite produto/categoria/marca. Rode de novo (não chama o modelo para isso).
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Literal

import litellm
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_ITENS = RAIZ / "outputs" / "itens.csv"
ARQUIVO_SAIDA = RAIZ / "outputs" / "itens_padronizados.csv"
ARQUIVO_DICIONARIO = RAIZ / "data" / "produtos_padrao.csv"
ARQUIVO_CORRECOES = RAIZ / "data" / "correcoes_produtos.csv"
TAMANHO_LOTE = 10

load_dotenv(RAIZ / ".env")

Categoria = Literal[
    "hortifruti", "proteina", "laticinios", "padaria", "mercearia", "doces_e_snacks", "bebidas",
    "congelados", "higiene", "limpeza", "farmacia", "refeicao_fora", "vestuario", "utilidades", "outro",
]
CATEGORIAS: tuple[str, ...] = Categoria.__args__


class ItemPadrao(BaseModel):
    original: str
    produto: str = Field(min_length=2, max_length=40)
    categoria: Categoria
    marca: str | None = None


class LotePadrao(BaseModel):
    itens: list[ItemPadrao]


PROMPT = f"""Você padroniza itens de notas fiscais de compras no Brasil (mercado, farmácia, restaurante, lojas).
Os nomes vêm abreviados e em maiúsculas. Para CADA item recebido, devolva:
- "original": exatamente o texto recebido no campo "nome";
- "produto": nome genérico, curto, em português, minúsculo, no singular, SEM marca, SEM tamanho/peso e
  SEM nome de loja. Itens iguais de marcas diferentes devem ter o MESMO produto
  (ex.: "OVO BCO EXT YAN" e "OVOS BRANCOS 12UN" -> "ovo"; "LTE COND SEMIDESN IT" -> "leite condensado");
- "categoria": uma de {list(CATEGORIAS)};
  proteina = carne, frango, peixe, ovo; refeicao_fora = prato pronto, comida por quilo, lanche de restaurante;
  utilidades = sacola, utensílios, objetos;
- "marca": a marca, se estiver clara no nome (ex.: "Lacta", "Piracanjuba"); senão null. Nunca a loja.
Pistas e regras (aprendidas com erros reais):
- Unidade KG = vendido a peso. "QUILO" / "QUILO CLIENTE" com preço alto por kg = "comida por quilo" (refeicao_fora).
- "OVO PASCOA" = "ovo de páscoa" (doces_e_snacks). Ovo de galinha ("OVO BCO", "OVOS VERM") = "ovo" (proteina).
- Itens de restaurante ("TABUA ...", "... ACOMP", porções, molhos de porção) = refeicao_fora.
- Abreviações comuns: SH = shampoo, SAB LIQ = sabonete líquido, LTE/LT = leite, COND = condensado,
  CR = creme, T GRAV / TESTE GRAV = teste de gravidez (farmacia), CABOTIA = abóbora cabotiá (hortifruti),
  MIST BOLO = mistura para bolo (mercearia), LT no fim = lata.
- Cosméticos e cuidados pessoais (óleo de amêndoas, shampoo, desodorante) = higiene. Remédios = farmacia.
- Enlatados, massas, molhos, temperos, farinhas, miojo/cup noodles = mercearia.
- Se não reconhecer o item, use o melhor palpite e categoria "outro"; nunca invente um objeto sem relação.

Responda SOMENTE com JSON: {{"itens": [{{"original": "...", "produto": "...", "categoria": "...", "marca": null}}]}}"""


# ---------------------------------------------------------------- limpeza em código
def limpar_nome(nome: str) -> str:
    """'#1700900#18069000#TRUFA CHOC 30' -> 'TRUFA CHOC 30' (códigos fiscais grudados no nome)."""
    nome = re.sub(r"^(#\d+)+#", "", str(nome))
    return " ".join(nome.split()).upper()


# ---------------------------------------------------------------- dicionário e correções
def ler_tabela(caminho: Path) -> pd.DataFrame:
    if caminho.exists():
        return pd.read_csv(caminho, dtype=str).fillna("")
    return pd.DataFrame(columns=["original", "produto", "categoria", "marca"])


def dicionario_atual() -> dict[str, dict]:
    """Modelo primeiro, correções por cima (correção manual sempre ganha)."""
    dic = {l["original"]: l for l in ler_tabela(ARQUIVO_DICIONARIO).to_dict("records")}
    dic.update({l["original"]: l for l in ler_tabela(ARQUIVO_CORRECOES).to_dict("records")})
    return dic


# ---------------------------------------------------------------- modelo
ESPERAS_LIMITE_MINUTO_S = (20, 40, 65)  # plano gratuito da API: poucos pedidos por minuto


def _completar(conversa: list[dict], parametros: dict) -> str:
    """Chamada ao modelo; no limite por minuto (429) ou sobrecarga (503), espera e tenta de novo.
    A cota diária esgotada não adianta esperar: falha na hora."""
    for espera in (*ESPERAS_LIMITE_MINUTO_S, None):
        try:
            return litellm.completion(messages=conversa, **parametros).choices[0].message.content or ""
        except Exception as e:
            texto = str(e).lower()
            temporario = ("429" in texto or "503" in texto) and "per day" not in texto and "perday" not in texto
            if espera is None or not temporario:
                raise
            print(f"  (modelo ocupado ou limite por minuto: aguardando {espera}s)", flush=True)
            time.sleep(espera)


def chamar_modelo(entradas: list[dict], modelo: str, tentativas: int = 3) -> list[ItemPadrao]:
    """Um lote de itens -> itens padronizados. Valida o JSON; se vier errado, pede de novo com o erro."""
    conversa = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(entradas, ensure_ascii=False)}]
    parametros = {"model": modelo, "temperature": 0, "timeout": 300, "response_format": {"type": "json_object"}}
    if modelo.startswith("ollama"):
        parametros["api_base"] = os.getenv("API_BASE", "http://localhost:11434")
    for _ in range(tentativas):
        texto = _completar(conversa, parametros)
        try:
            return LotePadrao.model_validate_json(texto).itens
        except ValidationError as e:
            conversa += [{"role": "assistant", "content": texto},
                         {"role": "user", "content": f"JSON inválido: {str(e)[:300]}. Responda só o JSON corrigido."}]
    return []


def padronizar_novos(novos: pd.DataFrame, modelo: str) -> tuple[list[dict], list[str]]:
    """Manda os nomes novos em lotes. Devolve (linhas para o dicionário, nomes que falharam)."""
    linhas, falhas = [], []
    for inicio in range(0, len(novos), TAMANHO_LOTE):
        lote = novos.iloc[inicio:inicio + TAMANHO_LOTE]
        entradas = [{"nome": r.nome_limpo, "unidade": r.unidade, "preco_unitario": r.preco_unitario}
                    for r in lote.itertuples()]
        t = time.perf_counter()
        resposta = {i.original.strip().upper(): i for i in chamar_modelo(entradas, modelo)}
        for nome in lote["nome_limpo"]:
            item = resposta.get(nome)
            if item:
                linhas.append({"original": nome, "produto": item.produto.strip().lower(),
                               "categoria": item.categoria, "marca": item.marca or ""})
            else:
                falhas.append(nome)  # modelo pulou ou alterou o nome: fica para a próxima rodada
        print(f"  lote {inicio // TAMANHO_LOTE + 1}: {len(lote)} itens em {time.perf_counter() - t:.0f}s", flush=True)
    return linhas, falhas


# ---------------------------------------------------------------- uso como função (comando e app)
def nomes_novos(itens: pd.DataFrame) -> pd.DataFrame:
    tabela = itens.assign(nome_limpo=itens["produto"].map(limpar_nome))
    return tabela[~tabela["nome_limpo"].isin(dicionario_atual())].drop_duplicates("nome_limpo")


def aplicar_padronizacao(itens: pd.DataFrame, modelo: str) -> tuple[pd.DataFrame, list[str]]:
    """Padroniza só os nomes novos (o resto vem do dicionário) e grava outputs/itens_padronizados.csv."""
    novos, falhas = nomes_novos(itens), []
    if len(novos):
        linhas, falhas = padronizar_novos(novos, modelo)
        dicionario = pd.concat([ler_tabela(ARQUIVO_DICIONARIO), pd.DataFrame(linhas)], ignore_index=True)
        dicionario.sort_values(["categoria", "produto"]).to_csv(ARQUIVO_DICIONARIO, index=False)
    dic = dicionario_atual()
    itens = itens.copy()
    limpos = itens["produto"].map(limpar_nome)
    for campo in ("produto", "categoria", "marca"):
        itens[f"{campo}_padrao"] = limpos.map(lambda n: dic.get(n, {}).get(campo) or None)
    itens.to_csv(ARQUIVO_SAIDA, index=False)
    return itens, falhas


# ---------------------------------------------------------------- principal
def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modelo", default=os.getenv("MODEL_NAME", "ollama/qwen2.5:7b"))
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação")
    args = parser.parse_args()

    itens = pd.read_csv(ARQUIVO_ITENS)
    novos = nomes_novos(itens)
    if len(novos):
        lotes = -(-len(novos) // TAMANHO_LOTE)
        local = args.modelo.startswith("ollama")
        print(f"Nomes novos: {len(novos)} -> {lotes} chamada(s) "
              f"ao {args.modelo} ({'local, US$ 0' if local else 'API PAGA: confira o preço do modelo'}).")
        if not local and not args.sim and input("Confirmar? (s/N) ").strip().lower() != "s":
            print("Cancelado.")
            return
    else:
        print("Nenhum nome novo: tudo já está no dicionário.")
    itens, falhas = aplicar_padronizacao(itens, args.modelo)
    if falhas:
        print(f"  ! {len(falhas)} sem resposta válida (rode de novo): {falhas}")

    ok = itens[itens["produto_padrao"].notna()]
    print(f"\nPadronizados: {len(ok)}/{len(itens)} itens -> {ARQUIVO_SAIDA}")
    gasto = ok.groupby("categoria_padrao")["valor_total"].sum().sort_values(ascending=False)
    print("\nGasto por categoria:\n" + gasto.map(lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")).to_string())
    repetidos = ok.groupby("produto_padrao")["chave"].nunique().loc[lambda s: s > 1]
    if len(repetidos):
        print("\nProdutos comprados em mais de uma nota:\n" + repetidos.sort_values(ascending=False).to_string())


if __name__ == "__main__":
    principal()
