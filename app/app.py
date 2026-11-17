"""Assistente de compras: adicione notas por foto e veja seu histórico.

    uv run streamlit run app/app.py

Modo demonstração (automático quando não há histórico local, ou com MODO_DEMO=1): usa a base pública
anonimizada em demo/itens.csv, não salva nada e limita as leituras por foto (cada uma é 1 chamada à API).
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
from mercado import entrada  # noqa: E402
from mercado import despensa as dp  # noqa: E402
from mercado import habitos as hb  # noqa: E402
from mercado.demo import ARQUIVO_DEMO  # noqa: E402
from mercado.padronizar import ARQUIVO_SAIDA  # noqa: E402
from mercado.recorrencia import MIN_COMPRAS, calcular_recorrencia  # noqa: E402
from mercado.sintetico import ARQUIVO_SINTETICO  # noqa: E402

st.set_page_config(page_title="Assistente de compras", page_icon="🛒", layout="wide")

# O Streamlit não tem tradução dos textos internos dos componentes ("Take Photo", "Upload"...):
# o CSS esconde o texto original e escreve o português no lugar.
st.markdown("""<style>
[data-testid="stCameraInputButton"] { font-size: 0 !important; }
[data-testid="stCameraInputButton"]::after { content: "Tirar foto"; font-size: 0.9rem; }
[data-testid="stCameraInputButton"]:has(svg)::after { content: none; }
[data-testid="stCameraInputButton"] > span {
    display: inline-flex !important; flex-direction: row !important; align-items: center; gap: 0.4rem; }
[data-testid="stCameraInputButton"] > span::after { content: "Limpar foto"; font-size: 0.9rem; }
[data-testid="stCameraInputButton"] svg { width: 0.6rem; height: 0.6rem; }
[data-testid="stCameraInputWebcamComponent"] > div:not([data-testid]) > p { display: none; }
[data-testid="stCameraInputWebcamComponent"] > div:not([data-testid])::after {
    content: "Permita o acesso à câmera no navegador para tirar a foto."; font-size: 0.9rem; }
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p { font-size: 0; }
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after {
    content: "Enviar"; font-size: 0.9rem; }
[data-testid="stFileUploaderDropzoneInstructions"] span { font-size: 0; }
[data-testid="stFileUploaderDropzoneInstructions"] span::after {
    content: "Arraste a imagem aqui · até 200 MB · PNG, JPG ou WEBP"; font-size: 0.8rem; }
</style>""", unsafe_allow_html=True)

MODO_DEMO = os.getenv("MODO_DEMO") == "1" or not ARQUIVO_SAIDA.exists()
PASTA_ANALISES_DEMO = RAIZ / "demo" / "analises"
LIMITE_LEITURAS_DEMO = 3  # por sessão: protege a cota gratuita da API na demo pública
TEM_CHAVE_API = bool(os.getenv("GEMINI_API_KEY"))
MODELO = os.getenv("MODEL_NAME", "gemini/gemini-3.5-flash-lite")

AZUL = "#2a78d6"
VERDE = "#1f9d6b"
ROTULOS_CATEGORIA = {
    "hortifruti": "Hortifrúti", "proteina": "Proteína", "laticinios": "Laticínios", "padaria": "Padaria",
    "mercearia": "Mercearia", "doces_e_snacks": "Doces e snacks", "bebidas": "Bebidas", "congelados": "Congelados",
    "higiene": "Higiene", "limpeza": "Limpeza", "farmacia": "Farmácia", "refeicao_fora": "Refeição fora",
    "vestuario": "Vestuário", "utilidades": "Utilidades", "outro": "Outro",
}
ROTULOS_STATUS = {
    "comprar": "🛒 Hora de comprar", "ok": "✅ Ainda não", "poucos_dados": "Poucas compras (sem previsão)",
    "esporadico": "Compra esporádica (sem previsão)", "sem_registro": "Sem nota recente",
}


def reais(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def barras(df: pd.DataFrame, categoria: str, valor: str, ordenar: bool = True) -> alt.Chart:
    base = alt.Chart(df).encode(
        y=alt.Y(f"{categoria}:N", sort="-x" if ordenar else None, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X(f"{valor}:Q", title=None, axis=alt.Axis(format=".0f", grid=True, tickCount=4)),
        tooltip=[alt.Tooltip(f"{categoria}:N", title=""), alt.Tooltip(f"{valor}:Q", title="R$", format=",.2f")],
    )
    return (base.mark_bar(cornerRadiusEnd=4, height=16, color=AZUL)
            + base.mark_text(align="left", dx=4, fontWeight=600).encode(text=alt.Text(f"{valor}:Q", format=".0f"))
            ).properties(height=max(120, 26 * len(df)))


@st.cache_data
def carregar_historico(_versao: float, demo: bool, perfil: str = "meu"):
    if perfil == "sintetico":
        itens = pd.read_csv(ARQUIVO_SINTETICO, parse_dates=["emissao"])
        itens["arquivo"] = itens["compra"]
    elif demo:
        itens = pd.read_csv(ARQUIVO_DEMO, parse_dates=["emissao"])
        itens["arquivo"] = itens["compra"]  # na base pública, o id da compra faz o papel do arquivo
    elif ARQUIVO_SAIDA.exists():
        itens = pd.read_csv(ARQUIVO_SAIDA, parse_dates=["emissao"])
    else:
        return pd.DataFrame()
    itens["categoria_rotulo"] = itens["categoria_padrao"].map(ROTULOS_CATEGORIA).fillna("Sem categoria")
    return itens


# ---------------------------------------------------------------- cabeçalho
st.title("🛒 Assistente de compras")
st.caption("Suas notas fiscais viram histórico de gastos, categorias, a previsão do que está acabando "
           "e uma análise com IA de acordo com a sua meta.")
if MODO_DEMO:
    st.info("**Modo demonstração.** O histórico é o da autora (+ notas públicas de exemplo), anonimizado: só "
            "produtos padronizados, lojas como apelidos e farmácia sem nome do item. Você pode testar a leitura de uma nota sua, mas nada é salvo.",
            icon="👀")

PERFIS = {"meu": "👤 Meu histórico", "sintetico": "🧪 Perfil sintético (2 anos)"}
perfil = st.segmented_control("Ver dados de", list(PERFIS), format_func=PERFIS.get, default="sintetico",
                              key="perfil") or "sintetico"
SINTETICO = perfil == "sintetico"
if SINTETICO:
    st.caption("**Perfil sintético**: uma pessoa fictícia com 200 idas ao mercado entre 01/2025 e 11/2026, montada a "
               "partir de cestas reais do dataset público *Groceries* (Kaggle, GPL 2). Datas e preços são simulados; "
               "serve para mostrar a análise de longo prazo. Nada aqui se mistura com o seu histórico.")

todos_itens = carregar_historico(st.session_state.get("versao", 0.0), MODO_DEMO, perfil)
itens = todos_itens
if not itens.empty and "fonte" in itens and (itens["fonte"] == "kaggle").any():
    incluir_exemplo = st.toggle("Incluir notas de exemplo (dataset do Kaggle)", value=True,
                                help="Notas fiscais públicas (dataset do Kaggle, CC0), de outras pessoas, só com alimentos. Desligue para ver "
                                     "apenas o seu histórico.")
    if not incluir_exemplo:
        itens = itens[itens["fonte"] != "kaggle"]
sem_data = int(itens.loc[itens["emissao"].isna(), "arquivo"].nunique()) if not itens.empty else 0
# na demo, "hoje" é a última nota: senão, com o tempo, todo produto viraria "sem registro"
hoje = itens["emissao"].max().normalize() if (MODO_DEMO or SINTETICO) and not itens.empty else None

aba_adicionar, aba_historico, aba_produtos, aba_ia = st.tabs(
    ["📷 Adicionar nota", "📊 Histórico", "🧺 Despensa e lista", "🎯 Análise com IA"])

# ---------------------------------------------------------------- adicionar
with aba_adicionar:
    st.markdown("Tire a foto da notinha **inteira** (o QR code aparecendo, se possível) ou envie uma imagem.")
    col_cam, col_arq = st.columns(2)
    foto_camera = col_cam.camera_input("Câmera")
    foto_arquivo = col_arq.file_uploader("Ou envie uma imagem", type=["png", "jpg", "jpeg", "webp"])
    foto = foto_camera or foto_arquivo
    st.caption("1º tentamos o QR code (lido no seu computador) e baixamos a nota oficial da Sefaz-SP. "
               "Se não der, a foto da lista de produtos é lida por um modelo de visão (Gemini): "
               "a imagem é enviada à API do Google, então evite fotografar o CPF.")

    leituras = st.session_state.get("leituras_foto", 0)
    usar_visao = TEM_CHAVE_API and not (MODO_DEMO and leituras >= LIMITE_LEITURAS_DEMO)
    if not TEM_CHAVE_API:
        st.caption("⚠️ Sem chave de API configurada: só a leitura pelo QR code está disponível.")
    elif MODO_DEMO:
        st.caption(f"Leituras por foto nesta sessão: {leituras} de {LIMITE_LEITURAS_DEMO} "
                   "(o QR code não conta: é lido sem IA).")

    if foto and st.button("Ler nota", type="primary"):
        with st.spinner("Lendo a nota..."):
            try:
                leitura = entrada.processar_imagem(foto.getvalue(), foto.name or "foto.jpg", usar_visao=usar_visao)
                if leitura["origem"] == "foto":
                    st.session_state["leituras_foto"] = leituras + 1
                st.session_state["leitura"] = leitura
            except Exception as e:
                st.session_state.pop("leitura", None)
                st.error(f"Não consegui ler a nota: {type(e).__name__}: {str(e)[:200]}")

    leitura = st.session_state.get("leitura")
    if leitura and not leitura["nota"] and leitura["problemas"]:
        st.warning("Não encontrei um QR code de NFC-e legível"
                   + (" e o limite de leituras por foto desta demonstração acabou." if MODO_DEMO else "."))
    if leitura and leitura["nota"]:
        nota = leitura["nota"]
        if leitura["origem"] == "qrcode":
            st.success("✅ QR code lido: esta é a nota **oficial** da Sefaz.")
        else:
            st.info("📷 Lida pela foto (modelo de visão).")
        if leitura["aviso_qr"]:
            st.caption(leitura["aviso_qr"])
        tabela = pd.DataFrame(nota["itens"])[["produto", "quantidade", "unidade", "preco_unitario", "valor_total"]]
        st.dataframe(tabela, hide_index=True, width="stretch",
                     column_config={"preco_unitario": st.column_config.NumberColumn("Preço unit.", format="R$ %.2f"),
                                    "valor_total": st.column_config.NumberColumn("Total", format="R$ %.2f")})
        c1, c2, c3 = st.columns(3)
        c1.metric("Itens", len(nota["itens"]))
        c2.metric("Desconto", reais(nota["desconto"] or 0))
        c3.metric("Total pago", reais(nota["total"]) if nota["total"] is not None else "não lido")
        if leitura["problemas"]:
            st.warning("A conta não fechou. Confira antes de salvar:\n- " + "\n- ".join(leitura["problemas"]))
        elif leitura["origem"] == "foto":
            st.caption("✔ Conferido: soma dos itens − descontos = total pago.")

        if MODO_DEMO:
            st.caption("Na demonstração a nota não é salva. Rodando o projeto no seu computador, ela entra no "
                       "histórico e os produtos novos são padronizados pela IA.")
        else:
            data = nota["emissao"]
            if data is None:
                dia = st.date_input("A data não aparece na foto. Quando foi a compra?", value=datetime.now().date(),
                                    format="DD/MM/YYYY")
                data = datetime.combine(dia, datetime.min.time())
            else:
                st.caption(f"Compra em {pd.Timestamp(data):%d/%m/%Y %H:%M}")

            if entrada.ja_registrada(nota):
                st.warning("Esta nota já está no seu histórico.")
            elif st.button("Salvar no histórico"):
                with st.spinner("Salvando e padronizando os produtos novos..."):
                    entrada.salvar(leitura, None if nota["emissao"] is not None else data)
                    _, _, falhas = entrada.atualizar_historico(MODELO)
                st.session_state.pop("leitura", None)
                st.session_state["versao"] = datetime.now().timestamp()
                st.success("Salvo! Veja nas abas de histórico."
                           + (f" ({len(falhas)} produto(s) sem padronizar)" if falhas else ""))

# ---------------------------------------------------------------- histórico
with aba_historico:
    if itens.empty:
        st.info("Ainda não há notas. Adicione a primeira na aba ao lado.")
    else:
        if sem_data:
            st.caption(f"{sem_data} nota(s) sem data legível na foto: entram nos totais e categorias, "
                       "mas não no gráfico por mês nem na recorrência.")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Gasto total", reais(itens["valor_total"].sum()))
        k2.metric("Compras", itens["arquivo"].nunique())
        k3.metric("Produtos diferentes", itens["produto_padrao"].nunique())
        k4.metric("Período", f"{itens['emissao'].min():%m/%Y} a {itens['emissao'].max():%m/%Y}")

        por_mes = (itens.assign(mes=itens["emissao"].dt.to_period("M").dt.to_timestamp())
                   .groupby("mes", as_index=False)["valor_total"].sum())
        st.subheader("Gasto por mês")
        meses = alt.Chart(por_mes).encode(
            x=alt.X("mes:T", title=None, axis=alt.Axis(format="%m/%y", tickCount="month")),
            y=alt.Y("valor_total:Q", title=None, axis=alt.Axis(format=".0f", tickCount=4)),
            tooltip=[alt.Tooltip("mes:T", title="Mês", format="%m/%Y"),
                     alt.Tooltip("valor_total:Q", title="R$", format=",.2f")])
        st.altair_chart((meses.mark_bar(cornerRadiusEnd=4, width=22, color=AZUL)
                         + meses.mark_text(dy=-8, fontWeight=600).encode(text=alt.Text("valor_total:Q", format=".0f"))
                         ).properties(height=240), width="stretch")
        st.caption("Meses sem barra = sem notas registradas, não necessariamente sem compras.")

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Por categoria")
            cat = itens.groupby("categoria_rotulo", as_index=False)["valor_total"].sum()
            st.altair_chart(barras(cat, "categoria_rotulo", "valor_total"), width="stretch")
        with col_b:
            st.subheader("Onde vai mais dinheiro")
            top = itens.groupby("produto_padrao", as_index=False)["valor_total"].sum().nlargest(12, "valor_total")
            st.altair_chart(barras(top, "produto_padrao", "valor_total"), width="stretch")

        with st.expander("Ver todos os itens"):
            st.dataframe(itens[["emissao", "loja", "produto_padrao", "categoria_rotulo", "quantidade", "unidade",
                                "valor_total"]].sort_values("emissao", ascending=False),
                         hide_index=True, width="stretch")
        if not SINTETICO:
            st.caption("Lojas aparecem como apelidos (loja_A, loja_B...): o nome real fica só no computador da autora.")

# ---------------------------------------------------------------- despensa e lista
COR_STATUS = {"tem": st.success, "acabando": st.warning, "acabou": st.error}
ROTULO_DESPENSA = {"acabando": "acabando", "acabou": "provavelmente acabou"}
# o retorno ("acabou"/"ainda tenho") e a lista ficam em data/ no computador; na demo, só nesta sessão
arquivo_retorno = None if MODO_DEMO or SINTETICO else dp.ARQUIVO_RETORNO
arquivo_lista = None if MODO_DEMO or SINTETICO else dp.ARQUIVO_LISTA
CHAVE_RETORNO, CHAVE_LISTA = f"retorno_{perfil}", f"lista_{perfil}"
if CHAVE_RETORNO not in st.session_state:
    st.session_state[CHAVE_RETORNO] = dp.ler_retorno(arquivo_retorno)
if CHAVE_LISTA not in st.session_state:
    st.session_state[CHAVE_LISTA] = dp.ler_lista(arquivo_lista)


def guardar_lista() -> None:
    dp.salvar_lista(st.session_state[CHAVE_LISTA], arquivo_lista)


def marcar(nome: str) -> None:
    marcados = st.session_state[CHAVE_LISTA]["marcados"]
    if st.session_state[f"chk_{perfil}_{nome}"]:
        marcados.append(nome)
    elif nome in marcados:
        marcados.remove(nome)
    guardar_lista()


def adicionar_manual() -> None:
    nome = st.session_state.get("novo_item", "").strip().lower()
    if nome and nome not in st.session_state[CHAVE_LISTA]["manuais"]:
        st.session_state[CHAVE_LISTA]["manuais"].append(nome)
        guardar_lista()
    st.session_state["novo_item"] = ""


def limpar_marcados() -> None:
    lista = st.session_state[CHAVE_LISTA]
    lista["manuais"] = [m for m in lista["manuais"] if m not in lista["marcados"]]
    for nome in lista["marcados"]:
        st.session_state.pop(f"chk_{perfil}_{nome}", None)
    lista["marcados"] = []
    guardar_lista()


def responder(resposta: str, consulta: dict) -> None:
    st.session_state[CHAVE_RETORNO] = dp.registrar_retorno(
        st.session_state[CHAVE_RETORNO], consulta["produto"], consulta["ultima_compra"], consulta["quantidade"],
        consulta["unidade"], resposta, hoje or pd.Timestamp.now(), arquivo_retorno)
    st.session_state["retorno_ok"] = consulta["produto"]


with aba_produtos:
    proprias = todos_itens[todos_itens["fonte"] != "kaggle"] if "fonte" in todos_itens else todos_itens
    if proprias.empty:
        st.info("Adicione notas suas para usar a despensa e a lista de compras.")
    else:
        retorno = st.session_state[CHAVE_RETORNO]
        col_ultima, col_pergunta = st.columns([1, 1.4], gap="large")

        # ---- última compra
        with col_ultima:
            ultima = dp.ultima_compra(proprias)
            referencia = hoje if hoje is not None else pd.Timestamp.now().normalize()
            ha = (referencia - ultima["data"].normalize()).days
            quando = "hoje" if ha == 0 else ("ontem" if ha == 1 else f"há {ha} dias")
            with st.container(border=True):
                st.markdown("**🧾 Sua última compra**")
                st.markdown(f"### {ultima['data']:%d/%m/%Y} · {quando}")
                st.caption(f"{ultima['loja']} · {len(ultima['itens'])} item(ns) · {reais(ultima['total'])}")
                st.dataframe(ultima["itens"][["produto_padrao", "quantidade", "unidade", "valor_total"]],
                             hide_index=True, width="stretch",
                             column_config={"produto_padrao": "Produto", "quantidade": "Qtd", "unidade": "Un",
                                            "valor_total": st.column_config.NumberColumn("Total", format="R$ %.2f")})
            with st.expander("Compras anteriores"):
                anteriores = (proprias[proprias["emissao"].notna()]
                              .groupby("arquivo").agg(data=("emissao", "min"), loja=("loja", "first"),
                                                      itens=("produto_padrao", "size"), total=("valor_total", "sum"))
                              .sort_values("data", ascending=False).head(15))
                st.dataframe(anteriores, hide_index=True, width="stretch",
                             column_config={"data": st.column_config.DatetimeColumn("Data", format="DD/MM/YYYY"),
                                            "total": st.column_config.NumberColumn("Total", format="R$ %.2f")})

        # ---- "preciso comprar X?"
        with col_pergunta:
            st.markdown("**🤔 Preciso comprar...?**")
            pergunta = st.text_input("Pergunte sobre um produto", key="pergunta_despensa",
                                     placeholder="Ex.: preciso comprar ovo?", label_visibility="collapsed")
            produto = None
            if pergunta:
                candidatos = dp.candidatos_produto(pergunta, dp.produtos_proprios(proprias))
                if not candidatos:
                    st.info("Não achei esse produto nas suas notas. Ou você ainda não comprou, ou ele aparece com "
                            "outro nome (veja em Meu histórico).")
                elif len(candidatos) == 1:
                    produto = candidatos[0]
                else:
                    produto = st.pills("Qual destes?", candidatos, key=f"escolha_{pergunta}")
            if produto:
                consulta = dp.consultar(proprias, produto, retorno, hoje)
                COR_STATUS[consulta["status"]](consulta["texto"])
                if st.session_state.get("retorno_ok") == produto:
                    st.caption("✔ Obrigado! Seu retorno foi guardado e já entrou na estimativa acima.")
                st.markdown("Para o assistente aprender o seu ritmo: **esse item já acabou?**")
                b1, b2, _ = st.columns([1, 1, 1.5])
                b1.button("Acabou", key="ret_acabou", on_click=responder, args=("acabou", consulta), width="stretch")
                b2.button("Ainda tenho", key="ret_ainda", on_click=responder, args=("ainda_tem", consulta),
                          width="stretch")
                anteriores_produto = retorno[retorno["produto"] == produto]
                if len(anteriores_produto):
                    st.caption(f"Você já respondeu sobre {produto} {len(anteriores_produto)} vez(es).")
            elif not pergunta:
                st.caption("A resposta usa a sua última compra, a quantidade e o seu ritmo de consumo. Quando você "
                           "responde se o item acabou, a próxima estimativa fica mais certeira.")
            if MODO_DEMO or SINTETICO:
                st.caption("Neste perfil, as respostas valem só nesta sessão.")

        # ---- checklist
        st.divider()
        sugestao = dp.lista_sugerida(proprias, retorno, hoje)
        lista = st.session_state[CHAVE_LISTA]
        col_lista, col_add = st.columns([1.4, 1], gap="large")
        with col_lista:
            st.subheader("🛒 Lista de compras")
            if sugestao.empty and not lista["manuais"]:
                st.caption("Nada para comprar agora pelas suas notas. Adicione itens à mão ao lado.")
            for r in sugestao.itertuples():
                rotulo = (f"**{r.produto}** · {ROTULO_DESPENSA[r.status]} · última compra "
                          f"{pd.Timestamp(r.ultima_compra):%d/%m}")
                st.checkbox(rotulo, key=f"chk_{perfil}_{r.produto}", value=r.produto in lista["marcados"],
                            on_change=marcar, args=(r.produto,),
                            help=f"Dura ~{r.duracao_dias:.0f} dias ({r.como}).")
            for nome in lista["manuais"]:
                st.checkbox(f"**{nome}** · adicionado por você", key=f"chk_{perfil}_{nome}", value=nome in lista["marcados"],
                            on_change=marcar, args=(nome,))
        with col_add:
            st.text_input("Adicionar item", key="novo_item", on_change=adicionar_manual,
                          placeholder="Ex.: papel toalha")
            st.button("Limpar marcados", on_click=limpar_marcados, disabled=not lista["marcados"])
            st.caption("A lista se monta sozinha com o que provavelmente acabou. Itens comprados há muito tempo "
                       "ficam de fora: provavelmente foram comprados sem nota.")

        with st.expander("Frequência de compra por produto"):
            rec = calcular_recorrencia(proprias, hoje)
            if rec.empty:
                st.caption("Ainda não há produtos comprados em mais de um dia.")
            else:
                st.dataframe(rec.assign(categoria=rec["categoria"].map(ROTULOS_CATEGORIA),
                                        status=rec["status"].map(ROTULOS_STATUS)),
                             hide_index=True, width="stretch",
                             column_config={
                                 "compras": st.column_config.NumberColumn("Compras"),
                                 "intervalo_medio_dias": st.column_config.NumberColumn("Intervalo (dias)", format="%.0f"),
                                 "ultima_compra": st.column_config.DateColumn("Última compra", format="DD/MM/YYYY"),
                                 "proxima_prevista": st.column_config.DateColumn("Próxima prevista",
                                                                                 format="DD/MM/YYYY"),
                                 "gasto_medio": st.column_config.NumberColumn("Gasto médio", format="R$ %.2f"),
                                 "status": st.column_config.TextColumn("Situação"),
                             })
                st.caption(f"Intervalo entre compras registradas. Só vira previsão com {MIN_COMPRAS}+ compras e "
                           "intervalo plausível para o tipo de produto.")


# ---------------------------------------------------------------- análise com IA
def mostrar_analise(resultado: dict) -> None:
    analise = resultado["analise"]
    st.markdown(f"#### Diagnóstico\n{analise['diagnostico']}")
    if analise["pontos_positivos"]:
        st.markdown("**O que já está bom**\n" + "\n".join(f"- {p}" for p in analise["pontos_positivos"]))
    sugestoes = analise["sugestoes"]
    economia = sum(s["economia_mensal_estimada"] or 0 for s in sugestoes)
    if economia:
        st.metric("Economia possível (estimativa da IA, conferida em código)", f"{reais(economia)} / mês")
    for coluna, s in zip(st.columns(max(1, len(sugestoes))), sugestoes):
        with coluna.container(border=True):
            st.markdown(f"**{s['titulo']}**")
            st.write(s["acao"])
            rotulo = ROTULOS_CATEGORIA.get(s["evidencia"], s["evidencia"])
            extra = f" · ~{reais(s['economia_mensal_estimada'])}/mês" if s["economia_mensal_estimada"] else ""
            st.caption(f"Base: {rotulo}{extra}")
    if analise.get("alerta"):
        st.warning(analise["alerta"])
    if resultado["descartes"]:
        with st.expander(f"{len(resultado['descartes'])} sugestão(ões) descartada(s) pela conferência"):
            st.markdown("\n".join(f"- {d}" for d in resultado["descartes"]))
    st.caption(f"Gerado por {resultado['modelo']}. Sugestões de consumo, não orientação médica ou nutricional.")


with aba_ia:
    if itens.empty or (todos_itens.get("fonte", pd.Series(dtype=str)) != "kaggle").sum() == 0:
        st.info("Adicione notas suas para receber uma análise.")
    else:
        st.markdown("Escolha uma meta. O código resume o histórico; a IA comenta os hábitos e sugere ajustes; "
                    "o código confere cada sugestão (precisa apontar algo que existe no histórico, e a economia "
                    "prometida não pode passar do que se gasta com aquilo).")
        meta = st.segmented_control("Meta", list(hb.METAS), format_func=hb.ROTULOS_METAS.get,
                                    default="economizar", key="meta")
        meta = meta or "economizar"
        # hábitos são de UMA pessoa: as notas de exemplo (Kaggle) ficam fora da análise
        proprias = todos_itens[todos_itens["fonte"] != "kaggle"] if "fonte" in todos_itens else todos_itens
        dono = "do perfil sintético" if SINTETICO else ("da autora" if MODO_DEMO else "suas")
        st.caption(f"A análise usa só as notas {dono} ({proprias['arquivo'].nunique()} compras)"
                   + ("." if SINTETICO else ", sem as notas de exemplo do Kaggle."))
        resumo = hb.resumir(proprias, hoje)
        col_num, col_ia = st.columns([1, 2.3])
        with col_num:
            st.metric("Gasto médio por mês com notas", reais(resumo["gasto_medio_por_mes_com_notas"]))
            principais = pd.DataFrame(resumo["categorias"]).head(6)
            principais["categoria"] = principais["categoria"].map(ROTULOS_CATEGORIA)
            st.altair_chart(barras(principais, "categoria", "gasto_mensal_estimado")
                            .properties(title="R$ por mês, por categoria"), width="stretch")
            with st.expander("O que a IA recebe"):
                st.caption("Só este resumo agregado: nenhuma loja, nota ou nome bruto de produto.")
                st.json(resumo, expanded=False)
        with col_ia:
            if MODO_DEMO:
                pasta = ARQUIVO_SINTETICO.parent / "analises" if SINTETICO else PASTA_ANALISES_DEMO
                arquivo = pasta / f"{meta}.json"
                if arquivo.exists():
                    mostrar_analise(json.loads(arquivo.read_text(encoding="utf-8")))
                    st.caption("Na demonstração a análise foi gerada antes (abrir a demo não gasta chamadas de IA).")
                else:
                    st.info("A análise desta meta ainda não foi gerada para a demonstração.")
            else:
                pronta = hb.em_cache(resumo, meta, MODELO)
                if pronta:
                    mostrar_analise(pronta)
                elif not TEM_CHAVE_API and not MODELO.startswith("ollama"):
                    st.info("Configure a chave de API no .env para gerar a análise.")
                elif st.button("Gerar análise (1 chamada à IA)", type="primary"):
                    with st.spinner("Analisando..."):
                        try:
                            mostrar_analise(hb.analisar(resumo, meta, MODELO))
                        except Exception as e:
                            st.error(f"Não consegui gerar a análise: {type(e).__name__}: {str(e)[:200]}")
                else:
                    st.caption("Nada gerado ainda para este histórico e esta meta. O resultado fica salvo: "
                               "só chama a IA de novo quando entram notas novas.")
