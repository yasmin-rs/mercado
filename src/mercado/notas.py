"""Lê as páginas de NFC-e salvas da consulta da Sefaz-SP e gera tabelas de compras e itens.

Como salvar as notas (manual, 1 por vez — o captcha não é automatizado de propósito):
    https://www.nfce.fazenda.sp.gov.br/NFCeConsultaPublica/Paginas/ConsultaPublica.aspx
    cole a chave -> captcha -> Ctrl+S em data/notas/ (aceita "página única" .mhtml ou "somente HTML")

Uso:
    uv run python -m mercado.notas

Saídas (fora do git):
    outputs/compras.csv   uma linha por nota: data, loja pseudônima, total, pagamento, nº de itens
    outputs/itens.csv     uma linha por produto comprado
    data/lojas.csv        de-para PRIVADO loja_A -> nome real (só para você conferir)

Privacidade: nome, CNPJ e endereço da loja não saem do arquivo bruto; dados do consumidor
(CPF) são ignorados na leitura.
"""
import email
import json
import re
import sys
from email import policy
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from mercado.chaves import ARQUIVO_CHAVES, decodificar_chave, ler_chaves
from mercado.lojas import ARQUIVO_LOJAS, apelidar

RAIZ = Path(__file__).resolve().parents[2]
PASTA_NOTAS = RAIZ / "data" / "notas"
PASTA_FOTOS_LIDAS = RAIZ / "data" / "fotos_lidas"   # notas lidas de foto pelo modelo de visão (JSON)
PASTA_SAIDA = RAIZ / "outputs"


# ---------------------------------------------------------------- leitura do arquivo
def ler_html(caminho: Path) -> str:
    """Devolve o HTML da página, seja ela salva como MHTML (página única) ou HTML puro."""
    bruto = caminho.read_bytes()
    if bruto[:200].lstrip().startswith((b"From:", b"MIME-Version")) or b"Content-Type: multipart/related" in bruto[:2000]:
        msg = email.message_from_bytes(bruto, policy=policy.default)
        for parte in msg.walk():
            if parte.get_content_type() == "text/html":
                bruto = parte.get_payload(decode=True)
                break
    # a Sefaz serve UTF-8; o cabeçalho do MHTML às vezes diz outra coisa, então decodificamos nós mesmos
    try:
        return bruto.decode("utf-8")
    except UnicodeDecodeError:
        return bruto.decode("latin-1")


def numero_br(texto: str) -> float | None:
    """'1.234,56' -> 1234.56 ; '0,432' -> 0.432"""
    achado = re.search(r"-?[\d.]*\d(?:,\d+)?", texto or "")
    return float(achado.group().replace(".", "").replace(",", ".")) if achado else None


def _texto(no) -> str:
    return " ".join(no.get_text(" ", strip=True).split()) if no else ""


# ---------------------------------------------------------------- extração
def extrair_nota(html: str) -> dict:
    sopa = BeautifulSoup(html, "html.parser")
    if not sopa.select_one("#tabResult"):
        raise ValueError("página sem a tabela de itens (#tabResult): não é uma consulta de NFC-e?")

    itens = []
    for linha in sopa.select("#tabResult tr"):
        nome = linha.select_one(".txtTit")
        if not nome:
            continue
        itens.append({
            "produto": _texto(nome),
            "codigo": re.sub(r"\D", "", _texto(linha.select_one(".RCod"))) or None,
            "quantidade": numero_br(_texto(linha.select_one(".Rqtd")).replace("Qtde.:", "")),
            # alguns caixas gravam "un9"/"kg9": tira o número que sobra no fim
            "unidade": re.sub(r"\d+$", "", _texto(linha.select_one(".RUN")).replace("UN:", "").strip()).upper() or None,
            "preco_unitario": numero_br(_texto(linha.select_one(".RvlUnit")).replace("Vl. Unit.:", "")),
            "valor_total": numero_br(_texto(linha.select_one(".valor"))),
        })

    total, desconto, troco, pagamentos = None, 0.0, 0.0, []
    for bloco in sopa.select("#totalNota > div"):
        rotulo = _texto(bloco.select_one("label"))
        valor = numero_br(_texto(bloco.select_one(".totalNumb")))
        if rotulo.startswith("Valor a pagar"):
            total = valor
        elif rotulo.startswith("Descontos"):
            desconto = valor or 0.0
        elif rotulo.startswith("Troco"):
            troco = valor or 0.0
        elif bloco.select_one("label.tx") and valor is not None:
            pagamentos.append(rotulo)

    infos = _texto(sopa.select_one("#infos"))
    emissao = re.search(r"Emissão:\s*(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})", infos)
    cnpj = re.sub(r"\D", "", _texto(sopa.select_one(".txtCenter .text")))[:14]

    return {
        "chave": re.sub(r"\D", "", _texto(sopa.select_one(".chave"))) or None,
        "emissao": pd.to_datetime(emissao.group(1), dayfirst=True) if emissao else None,
        "loja_nome": _texto(sopa.select_one("#u20")),  # só para o de-para privado
        "cnpj": cnpj or None,                           # idem
        "total": total,
        "desconto": desconto,
        "troco": troco,
        "pagamento": " + ".join(pagamentos) or None,
        "itens": itens,
        "origem": "sefaz",
    }


# ---------------------------------------------------------------- lote
def ler_pasta(pasta: Path, pasta_fotos: Path | None = PASTA_FOTOS_LIDAS) -> tuple[list[dict], list[str]]:
    """Notas oficiais (HTML da Sefaz) + notas lidas de foto (JSON)."""
    notas, problemas = [], []
    for caminho in sorted(p for p in pasta.iterdir() if p.is_file()) if pasta.exists() else []:
        try:
            nota = extrair_nota(ler_html(caminho))
            nota["arquivo"] = caminho.name
            notas.append(nota)
        except Exception as e:
            problemas.append(f"{caminho.name}: {e}")
    for caminho in sorted(pasta_fotos.glob("*.json")) if pasta_fotos and pasta_fotos.exists() else []:
        try:
            nota = json.loads(caminho.read_text(encoding="utf-8"))
            nota["emissao"] = pd.to_datetime(nota["emissao"]) if nota.get("emissao") else None
            notas.append(nota)
        except Exception as e:
            problemas.append(f"{caminho.name}: {e}")
    return notas, problemas


def mesma_compra(foto: dict, oficial: dict) -> bool:
    """Foto e nota oficial da MESMA compra: mesmo dia e mesmo total (a foto não tem chave)."""
    return (foto["emissao"] is not None and oficial["emissao"] is not None
            and foto["emissao"].date() == oficial["emissao"].date()
            and foto["total"] is not None and oficial["total"] is not None
            and abs(foto["total"] - oficial["total"]) < 0.02)


def montar_tabelas(notas: list[dict], arquivo: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """-> (compras, itens, lojas). Apelidos estáveis (mercado.lojas); `arquivo` = de-para (testes usam temporário)."""
    unicas = list({n["chave"] or n["arquivo"]: n for n in notas}.values())  # mesma nota salva 2x conta 1
    oficiais = [n for n in unicas if n.get("origem", "sefaz") == "sefaz"]
    unicas = [n for n in unicas if n.get("origem", "sefaz") == "sefaz"
              or not any(mesma_compra(n, o) for o in oficiais)]  # foto repetida de nota oficial: fica a oficial
    unicas.sort(key=lambda n: (n["emissao"] is None, n["emissao"] or 0))
    nomes = {n["cnpj"]: n["loja_nome"] for n in unicas}
    apelido = apelidar([n["cnpj"] for n in unicas], nomes, arquivo) if arquivo else apelidar([n["cnpj"] for n in unicas], nomes)

    compras, itens = [], []
    for n in unicas:
        loja = apelido.get(n["cnpj"], "loja_?")
        soma_itens = round(sum(i["valor_total"] or 0 for i in n["itens"]), 2)
        compras.append({"chave": n["chave"], "arquivo": n.get("arquivo"), "origem": n.get("origem", "sefaz"),
                        "fonte": n.get("fonte", "pessoal"),
                        "emissao": n["emissao"], "loja": loja, "total": n["total"],
                        "desconto": n["desconto"], "pagamento": n["pagamento"], "n_itens": len(n["itens"]),
                        "confere": n["total"] is not None and abs(soma_itens - n["desconto"] - n["total"]) < 0.02})
        itens += [{"chave": n["chave"], "arquivo": n.get("arquivo"), "fonte": n.get("fonte", "pessoal"),
                   "emissao": n["emissao"], "loja": loja, **i} for i in n["itens"]]

    lojas = pd.DataFrame([{"loja": apelido[c], "nome": nomes[c], "cnpj": c} for c in dict.fromkeys(nomes) if c])
    compras = pd.DataFrame(compras).sort_values("emissao", ignore_index=True)
    return compras, pd.DataFrame(itens), lojas


def gerar_tabelas() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    """Lê tudo, monta e grava outputs/compras.csv e outputs/itens.csv. Usado pelo comando e pelo app."""
    notas, problemas = ler_pasta(PASTA_NOTAS)
    if not notas:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), problemas
    compras, itens, lojas = montar_tabelas(notas)
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    compras.to_csv(PASTA_SAIDA / "compras.csv", index=False)
    itens.to_csv(PASTA_SAIDA / "itens.csv", index=False)
    return compras, itens, lojas, problemas


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    compras, itens, lojas, problemas = gerar_tabelas()
    for p in problemas:
        print(f"  ! {p}")
    if compras.empty:
        print(f"Nenhuma nota lida em {PASTA_NOTAS}.")
        return

    print(f"Notas lidas: {len(compras)} | itens: {len(itens)} | lojas: {len(lojas)}")
    print(f"Total gasto: R$ {compras['total'].sum():,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    nao_conferem = compras[~compras["confere"]]
    if len(nao_conferem):
        print(f"  ! {len(nao_conferem)} nota(s) em que a soma dos itens não bate com o total — conferir.")
    print("\n" + compras[["emissao", "loja", "total", "n_itens", "pagamento"]].to_string(index=False))

    # quais chaves do Méliuz ainda não têm página salva?
    if ARQUIVO_CHAVES.exists():
        salvas = set(compras["chave"].dropna())
        faltam = [c for c in (decodificar_chave(x) for x in ler_chaves(ARQUIVO_CHAVES))
                  if c["valida"] and c["chave"] not in salvas]
        if faltam:
            print(f"\nFaltam salvar {len(faltam)} nota(s) de data/chaves.txt:")
            for c in faltam:
                print(f"  {' '.join(c['chave'][i:i + 4] for i in range(0, 44, 4))}")
    print(f"\nSalvo em {PASTA_SAIDA} (compras.csv, itens.csv) e de-para privado em {ARQUIVO_LOJAS}.")


if __name__ == "__main__":
    principal()
