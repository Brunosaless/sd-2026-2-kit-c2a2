"""
Servico 3 - GERACAO: monta o prompt com o contexto e chama o LLM.

TAREFA 3: proteger a chamada ao LLM com o circuit breaker.

Rodar: uvicorn servicos.geracao.app:app --port 8003
"""
import os
import time

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from servicos.comum.circuit_breaker import CircuitBreaker, CircuitoAberto
from servicos.comum.fila import (
    FILA_RAG,
    aguardar_resposta_fila,
    publicar_tarefa,
    worker_esta_ativo,
)
from servicos.comum.llm import avaliar_resposta, gerar

app = FastAPI(title="Geracao - C2.A2")
URL_RECUPERACAO = os.getenv("URL_RECUPERACAO", "http://localhost:8002")

disjuntor = CircuitBreaker(limite_falhas=3, espera_segundos=20)


class Pergunta(BaseModel):
    pergunta: str
    top_k: int = 3


def _verificar_disjuntor_antes():
    """Verifica se o disjuntor esta aberto antes de iniciar chamadas externas."""
    if disjuntor.estado == "aberto":
        if time.time() - disjuntor.aberto_em >= disjuntor.espera_segundos:
            disjuntor.estado = "meio"
        else:
            raise CircuitoAberto("disjuntor aberto: chamada recusada")


def _buscar_resultados(pergunta: str, top_k: int = 3) -> list[dict]:
    """Busca trechos relevantes via fila Redis (se worker ativo) ou via REST com retentativa."""
    if worker_esta_ativo():
        tarefa_id = publicar_tarefa(
            FILA_RAG,
            {"acao": "buscar", "pergunta": pergunta, "top_k": top_k},
        )
        if tarefa_id:
            resp = aguardar_resposta_fila(tarefa_id, timeout=5)
            if resp and resp.get("status") == "concluida":
                return resp.get("resultados", [])
            if resp and resp.get("status") == "erro":
                raise requests.ConnectionError(resp.get("erro", "Erro na busca via fila"))

    url_recuperacao = os.getenv("URL_RECUPERACAO", URL_RECUPERACAO)
    tentativas = max(1, int(os.getenv("RECUPERACAO_RETENTATIVAS", "2")))
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            r = requests.post(
                f"{url_recuperacao}/buscar",
                json={"pergunta": pergunta, "top_k": top_k},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("resultados", [])
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < tentativas:
                time.sleep(0.2 * tentativa)
    raise ultimo_erro


@app.post("/perguntar")
def perguntar(p: Pergunta):
    try:
        _verificar_disjuntor_antes()
    except CircuitoAberto as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Servico indisponivel ({exc}). Tente novamente mais tarde.",
        ) from exc

    try:
        resultados = _buscar_resultados(p.pergunta, top_k=p.top_k)
    except Exception as exc:
        disjuntor._registrar_falha()
        raise HTTPException(
            status_code=503,
            detail=f"Servico de recuperacao indisponivel (disjuntor: {disjuntor.estado}): {exc}",
        ) from exc

    contexto = [t["texto"] for t in resultados]
    fontes = list(dict.fromkeys(t.get("origem", "desconhecida") for t in resultados))

    # TAREFA 3: envolva a chamada gerar(...) com disjuntor.chamar(...)
    # e trate CircuitoAberto devolvendo 503 com mensagem clara.
    try:
        resposta = disjuntor.chamar(gerar, p.pergunta, contexto)
    except CircuitoAberto as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Servico indisponivel ({exc}). Tente novamente mais tarde.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Falha ao comunicar com o LLM (disjuntor: {disjuntor.estado}): {exc}",
        ) from exc

    avaliacao = avaliar_resposta(p.pergunta, resposta, contexto)

    return {
        "pergunta": p.pergunta,
        "resposta": resposta,
        "contexto": contexto,
        "fontes": fontes,
        "avaliacao": avaliacao,
    }


@app.get("/saude")
def saude():
    return {"status": "ok", "disjuntor": disjuntor.estado}
