"""Confere e decodifica chaves de acesso de NFC-e (44 dígitos) — sem internet, sem IA.

Estrutura da chave (padrão nacional da NF-e/NFC-e):
    cUF(2) AAMM(4) CNPJ(14) modelo(2) série(3) número(9) tipo_emissão(1) código(8) DV(1)

O DV é módulo 11 com pesos 2..9 da direita para a esquerda; se der 10 ou 11, vira 0.

Uso:
    uv run python -m mercado.chaves                  # lê data/chaves.txt
    uv run python -m mercado.chaves --arquivo outro.txt

Saída: outputs/chaves.csv (fora do git). A loja aparece como pseudônimo (loja_A, loja_B...),
nunca o CNPJ, para os dados poderem ir para o dashboard sem dizer onde a compra foi feita.
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from mercado.lojas import apelidar

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_CHAVES = RAIZ / "data" / "chaves.txt"
ARQUIVO_SAIDA = RAIZ / "outputs" / "chaves.csv"

UFS = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA",
       "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
       "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
       "51": "MT", "52": "GO", "53": "DF"}
MODELOS = {"55": "NF-e", "65": "NFC-e", "59": "SAT (CF-e)"}


def limpar_chave(texto: str) -> str:
    """Tira espaços, pontos, traços etc. Só sobram os dígitos."""
    return re.sub(r"\D", "", texto)


def digito_verificador(primeiros_43: str) -> int:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(d) * pesos[i % 8] for i, d in enumerate(reversed(primeiros_43)))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def decodificar_chave(texto: str) -> dict:
    chave = limpar_chave(texto)
    if len(chave) != 44:
        return {"chave": chave, "valida": False, "problema": f"tem {len(chave)} dígitos (esperado 44)"}
    dv_ok = digito_verificador(chave[:43]) == int(chave[43])
    info = {
        "chave": chave,
        "uf": UFS.get(chave[0:2], f"?{chave[0:2]}"),
        "ano": 2000 + int(chave[2:4]),
        "mes": int(chave[4:6]),
        "cnpj": chave[6:20],
        "modelo": MODELOS.get(chave[20:22], f"?{chave[20:22]}"),
        "serie": int(chave[22:25]),
        "numero": int(chave[25:34]),
        "valida": dv_ok and 1 <= int(chave[4:6]) <= 12,
        "problema": None if dv_ok else "dígito verificador não confere (erro de digitação?)",
    }
    if dv_ok and not info["valida"]:
        info["problema"] = "mês inválido"
    return info


def ler_chaves(caminho: Path) -> list[str]:
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in linhas if l.strip() and not l.strip().startswith("#")]


def pseudonimizar_lojas(df: pd.DataFrame, arquivo: Path | None = None) -> pd.DataFrame:
    """CNPJ -> apelido estável (loja_A, loja_B... na ordem em que a loja apareceu). O CNPJ sai da tabela."""
    ordem = df.sort_values(["ano", "mes"])["cnpj"].tolist() if {"ano", "mes"} <= set(df.columns) else df["cnpj"].tolist()
    apelidos = apelidar(ordem, arquivo=arquivo) if arquivo else apelidar(ordem)
    return df.assign(loja=df["cnpj"].map(apelidos)).drop(columns=["cnpj"])


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # acentos certos no terminal do Windows
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arquivo", type=Path, default=ARQUIVO_CHAVES)
    args = parser.parse_args()

    brutas = ler_chaves(args.arquivo)
    if not brutas:
        print(f"Nenhuma chave em {args.arquivo}. Cole uma por linha e rode de novo.")
        return
    df = pd.DataFrame([decodificar_chave(c) for c in brutas])

    invalidas = df[~df["valida"]]
    validas = df[df["valida"]].drop_duplicates("chave")
    duplicadas = int(df["valida"].sum()) - len(validas)

    print(f"Chaves lidas: {len(df)} | válidas: {len(validas)} | inválidas: {len(invalidas)} | repetidas: {duplicadas}")
    for _, linha in invalidas.iterrows():
        print(f"  ! {linha['chave'][:6]}…{linha['chave'][-4:]}: {linha['problema']}")
    if validas.empty:
        return

    validas = pseudonimizar_lojas(validas)
    print("\nPor tipo de documento / estado:")
    print(validas.groupby(["modelo", "uf"]).size().to_string())
    print("\nCompras por mês:")
    print(validas.groupby(["ano", "mes"]).size().to_string())
    print(f"\nLojas diferentes: {validas['loja'].nunique()}")
    print(validas["loja"].value_counts().to_string())

    ARQUIVO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    validas.to_csv(ARQUIVO_SAIDA, index=False)
    print(f"\nSalvo em {ARQUIVO_SAIDA} (fora do git).")


if __name__ == "__main__":
    principal()
