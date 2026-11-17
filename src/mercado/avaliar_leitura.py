"""Mede o leitor de fotos SEM rotular nada à mão: a nota "confere" quando a matemática fecha
(cada item: qtd x preço = total; soma - descontos = valor a pagar impresso).

Uso:
    uv run python -m mercado.avaliar_leitura data/kaggle --limite 3 --modelo ollama_chat/qwen2.5vl:7b
    uv run python -m mercado.avaliar_leitura data/kaggle --modelo gemini/gemini-3.5-flash-lite

Resultados por foto em outputs/avaliacao_leitura/<modelo>.csv (retoma de onde parou:
fotos já avaliadas com o mesmo modelo não são lidas de novo) e as notas em data/fotos_lidas/.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from mercado import ler_foto as lf
from mercado.notas import PASTA_FOTOS_LIDAS

RAIZ = Path(__file__).resolve().parents[2]
PASTA_SAIDA = RAIZ / "outputs" / "avaliacao_leitura"


def nome_modelo(modelo: str) -> str:
    return modelo.replace("/", "-").replace(":", "-")


def avaliar(fotos: list[Path], modelo: str, fonte: str, salvar_notas: bool) -> pd.DataFrame:
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    arquivo = PASTA_SAIDA / f"{nome_modelo(modelo)}.csv"
    feitas = pd.read_csv(arquivo) if arquivo.exists() else pd.DataFrame(columns=["foto"])
    for i, foto in enumerate(fotos, 1):
        if foto.name in set(feitas["foto"]):
            continue
        inicio = time.perf_counter()
        linha = {"foto": foto.name, "modelo": modelo}
        try:
            nota = lf.ler_foto(foto.read_bytes(), foto.name, modelo)
            problemas = lf.conferir(nota)
            linha.update(ok=True, itens=len(nota.itens), confere=not problemas and nota.valor_a_pagar is not None,
                         tem_total=nota.valor_a_pagar is not None, tem_data=bool(nota.data_emissao),
                         problemas=" | ".join(problemas))
            if salvar_notas:
                PASTA_FOTOS_LIDAS.mkdir(parents=True, exist_ok=True)
                registro = {**lf.para_nota(nota, f"{fonte}_{foto.stem}"), "origem": "foto", "fonte": fonte}
                registro["emissao"] = registro["emissao"].isoformat() if registro["emissao"] is not None else None
                (PASTA_FOTOS_LIDAS / f"{fonte}_{foto.stem}.json").write_text(
                    json.dumps(registro, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        except Exception as e:
            linha.update(ok=False, itens=0, confere=False, tem_total=False, tem_data=False,
                         problemas=f"{type(e).__name__}: {str(e)[:200]}")
        linha["segundos"] = round(time.perf_counter() - inicio, 1)
        feitas = pd.concat([feitas, pd.DataFrame([linha])], ignore_index=True)
        feitas.to_csv(arquivo, index=False)  # grava a cada foto: dá para interromper e retomar
        print(f"  {i}/{len(fotos)} {foto.name}: {linha['itens']} itens, "
              f"{'CONFERE' if linha['confere'] else 'não confere'} ({linha['segundos']}s)", flush=True)
    return feitas[feitas["foto"].isin([f.name for f in fotos])]


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pasta", type=Path)
    parser.add_argument("--modelo", default=lf.MODELO_VISAO)
    parser.add_argument("--limite", type=int)
    parser.add_argument("--fonte", default="kaggle", help="marca a origem das notas (para filtrar depois)")
    parser.add_argument("--nao-salvar", action="store_true", help="só medir, sem gravar as notas no histórico")
    args = parser.parse_args()

    fotos = sorted(p for p in args.pasta.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    fotos = fotos[: args.limite] if args.limite else fotos
    print(f"{len(fotos)} foto(s) -> {len(fotos)} chamada(s) ao {args.modelo}"
          f" ({'local, US$ 0' if lf.eh_local(args.modelo) else 'API'})")
    r = avaliar(fotos, args.modelo, args.fonte, not args.nao_salvar)
    print(f"\nConfere: {int(r['confere'].sum())}/{len(r)} | falhas de leitura: {int((~r['ok'].astype(bool)).sum())}"
          f" | tempo mediano: {r['segundos'].median():.0f}s por foto")


if __name__ == "__main__":
    principal()
