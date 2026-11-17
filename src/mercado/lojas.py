"""Apelidos ESTÁVEIS para as lojas (loja_A, loja_B...): o CNPJ nunca sai do seu computador.

O de-para fica em data/lojas.csv (fora do git). Uma loja, depois de ganhar a letra, fica
com ela para sempre; lojas novas recebem a próxima letra livre. Assim os gráficos e as
análises não "trocam" de loja quando chegam notas novas.
"""
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_LOJAS = RAIZ / "data" / "lojas.csv"


def nome_apelido(indice: int) -> str:
    """0 -> loja_A ... 25 -> loja_Z, 26 -> loja_AA ..."""
    letras = ""
    indice += 1
    while indice:
        indice, resto = divmod(indice - 1, 26)
        letras = chr(ord("A") + resto) + letras
    return f"loja_{letras}"


def apelidar(cnpjs: list[str], nomes: dict[str, str] | None = None, arquivo: Path = ARQUIVO_LOJAS) -> dict[str, str]:
    """Devolve {cnpj: apelido}, reaproveitando o de-para salvo e acrescentando as lojas novas."""
    depara = pd.read_csv(arquivo, dtype=str) if arquivo.exists() else pd.DataFrame(columns=["loja", "cnpj", "nome"])
    apelidos = dict(zip(depara["cnpj"], depara["loja"]))
    novas = []
    for cnpj in dict.fromkeys(c for c in cnpjs if c and c not in apelidos):  # ordem de chegada, sem repetir
        apelidos[cnpj] = nome_apelido(len(apelidos))
        novas.append({"loja": apelidos[cnpj], "cnpj": cnpj, "nome": (nomes or {}).get(cnpj, "")})
    # completa nomes que antes estavam vazios (ex.: loja vista primeiro só pela chave)
    if nomes:
        depara["nome"] = [n if isinstance(n, str) and n else nomes.get(c, "") for c, n in zip(depara["cnpj"], depara["nome"])]
    if novas or nomes:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        pd.concat([depara, pd.DataFrame(novas)], ignore_index=True).to_csv(arquivo, index=False)
    return apelidos
