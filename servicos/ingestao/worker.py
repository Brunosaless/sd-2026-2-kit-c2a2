"""
Worker de mensageria (TAREFA 4 - Aula 8).

Consome tarefas das filas Redis (`fila_ingestao` e `fila_rag`) de forma
desacoplada usando BLPOP:
  - `fila_ingestao`: recebe lotes de documentos, calcula os embeddings (TF-IDF)
    e persiste o indice vetorial em disco.
  - `fila_rag`: intermedia buscas de contexto no servico de recuperacao.

Rodar: python -m servicos.ingestao.worker
"""
import json
import os
import time

import requests

from servicos.comum.fila import (
    FILA_INGESTAO,
    FILA_RAG,
    obter_cliente_redis,
    registrar_heartbeat_worker,
    responder_tarefa,
)
from servicos.ingestao.app import executar_indexacao_local

URL_RECUPERACAO = os.getenv("URL_RECUPERACAO", "http://localhost:8002")


def processar_mensagem_ingestao(cliente, mensagem: dict) -> dict:
    """Processa uma tarefa de indexacao consumida da fila_ingestao."""
    tarefa_id = mensagem.get("id", "")
    documentos = mensagem.get("documentos", [])
    total = executar_indexacao_local(documentos)
    resultado = {
        "id": tarefa_id,
        "status": "concluida",
        "indexados": total,
        "novos_recebidos": len(documentos),
    }
    responder_tarefa(cliente, tarefa_id, resultado)
    return resultado


def processar_mensagem_rag(cliente, mensagem: dict) -> dict:
    """Processa uma tarefa de recuperacao consumida da fila_rag."""
    tarefa_id = mensagem.get("id", "")
    pergunta = mensagem.get("pergunta", "")
    top_k = mensagem.get("top_k", 3)
    url_recuperacao = os.getenv("URL_RECUPERACAO", URL_RECUPERACAO)

    try:
        r = requests.post(
            f"{url_recuperacao}/buscar",
            json={"pergunta": pergunta, "top_k": top_k},
            timeout=10,
        )
        r.raise_for_status()
        dados = r.json()
        resultado = {
            "id": tarefa_id,
            "status": "concluida",
            "resultados": dados.get("resultados", []),
        }
    except Exception as exc:
        resultado = {
            "id": tarefa_id,
            "status": "erro",
            "erro": str(exc),
        }

    responder_tarefa(cliente, tarefa_id, resultado)
    return resultado


def processar_uma_iteracao(cliente, timeout: int = 2) -> dict | None:
    """Executa um ciclo de consumo nas filas Redis."""
    registrar_heartbeat_worker(cliente)
    item = cliente.blpop([FILA_INGESTAO, FILA_RAG], timeout=timeout)
    if not item:
        return None

    fila, conteudo = item
    mensagem = json.loads(conteudo)
    if fila == FILA_INGESTAO:
        return processar_mensagem_ingestao(cliente, mensagem)
    if fila == FILA_RAG:
        return processar_mensagem_rag(cliente, mensagem)
    return None


def iniciar_worker():
    """Loop principal do worker de mensageria."""
    print("[worker] Iniciando worker de mensageria Redis...")
    while True:
        cliente = obter_cliente_redis()
        if cliente is None:
            print("[worker] Redis indisponivel. Nova tentativa em 3s...")
            time.sleep(3)
            continue

        try:
            resultado = processar_uma_iteracao(cliente, timeout=2)
            if resultado:
                print(f"[worker] Tarefa processada: {resultado.get('id')} -> {resultado.get('status')}")
        except KeyboardInterrupt:
            print("[worker] Encerrando worker...")
            break
        except Exception as exc:
            print(f"[worker] Erro ao processar fila: {exc}")
            time.sleep(1)


if __name__ == "__main__":
    iniciar_worker()

