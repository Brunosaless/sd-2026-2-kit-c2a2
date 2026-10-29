"""
Servico 2 - RECUPERACAO: dada uma pergunta, acha os trechos mais relevantes.

TAREFA 2: implementar a busca por similaridade (com reranking opcional).

Rodar: uvicorn servicos.recuperacao.app:app --port 8002
"""
import os
import re

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from servicos.comum.embeddings import Vetorizador, similaridade

app = FastAPI(title="Recuperacao - C2.A2")
URL_INGESTAO = os.getenv("URL_INGESTAO", "http://localhost:8001")


class Consulta(BaseModel):
    """Modelo Pydantic para validar o corpo da requisicao do endpoint de busca."""
    pergunta: str
    top_k: int = 3


def _extrair_termos(texto: str) -> set[str]:
    """Extrai palavras relevantes de um texto, removendo pontuacoes e stopwords.

    :param texto: String de entrada para ser tokenizada.
    :return: Conjunto (set) de termos filtrados e em letras minusculas.
    """

    stopwords = {
        "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
        "um", "uma", "uns", "umas", "para", "por", "com", "sem", "que",
        "como", "qual", "quais", "se", "ao", "aos", "as", "os", "ou", "e", "o", "a",
    }
    tokens = re.findall(r"\w+", texto.lower())
    return {t for t in tokens if len(t) > 2 and t not in stopwords}


def _calcular_score_rerank(pergunta: str, texto_doc: str, score_vetorial: float) -> float:
    """Reordena candidatos combinando similaridade vetorial e sobreposicao lexical.

    :param pergunta: Texto da pergunta do usuario.
    :param texto_doc: Conteudo do documento avaliado.
    :param score_vetorial: Pontuacao de similaridade por vetores (0.0 a 1.0).
    :return: Score ponderado combinando busca vetorial (75%) e busca lexical (25%).
    """

    termos_pergunta = _extrair_termos(pergunta)
    if not termos_pergunta:
        return score_vetorial

    termos_doc = _extrair_termos(texto_doc)
    cobertura_lexical = len(termos_pergunta & termos_doc) / len(termos_pergunta)
    return 0.75 * score_vetorial + 0.25 * cobertura_lexical


@app.post("/buscar")
def buscar(consulta: Consulta):
    """Devolve os top_k trechos mais parecidos com a pergunta, reordenados por relevancia."""
    
    url_ingestao = os.getenv("URL_INGESTAO", URL_INGESTAO)
    try:
        r = requests.get(f"{url_ingestao}/indice", timeout=10)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Servico de ingestao indisponivel: {exc}",
        ) from exc

    itens = r.json().get("itens", [])
    if not itens:
        return {"resultados": []}

    textos = [item["texto"] for item in itens]
    vetorizador = Vetorizador()
    vetorizador.ajustar(textos)
    vetor_pergunta = vetorizador.vetorizar(consulta.pergunta)

    candidatos = []
    for item in itens:
        vetor_item = item.get("vetor")
        if not vetor_item or len(vetor_item) != len(vetor_pergunta):
            vetor_item = vetorizador.vetorizar(item["texto"])

        sim = similaridade(vetor_pergunta, vetor_item)
        score_rerank = _calcular_score_rerank(consulta.pergunta, item["texto"], sim)
        candidatos.append({
            "texto": item["texto"],
            "origem": item.get("origem", "desconhecida"),
            "similaridade": round(sim, 4),
            "score_rerank": round(score_rerank, 4),
        })

    # Reordenacao (reranking) dos candidatos mais relevantes
    candidatos.sort(key=lambda c: (c["score_rerank"], c["similaridade"]), reverse=True)
    limite = max(1, consulta.top_k)
    return {"resultados": candidatos[:limite]}


@app.get("/saude")
def saude():
    """Endpoint para verificacao da saude (healthcheck) do servico."""
    
    return {"status": "ok"}
