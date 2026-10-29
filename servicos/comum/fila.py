"""
Modulo de mensageria com Redis (Aula 8 - TAREFA 4).

Permite publicar tarefas em filas Redis (RPUSH) e consumi-las em workers
independentes (BLPOP), com suporte a resposta assincrona por tarefa_id.
"""
import json
import os
import uuid

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FILA_INGESTAO = "fila_ingestao"
FILA_RAG = "fila_rag"
CHAVE_WORKER_ATIVO = "worker:ativo"


def obter_cliente_redis():
    """Retorna cliente Redis conectado ou None se indisponivel."""
    url = os.getenv("REDIS_URL", REDIS_URL)
    try:
        cliente = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=5.0,
        )
        cliente.ping()
        return cliente
    except Exception:
        return None


def registrar_heartbeat_worker(cliente, ttl: int = 15):
    """Registra no Redis que ha um worker ativo consumindo as filas."""
    if cliente is not None:
        try:
            cliente.set(CHAVE_WORKER_ATIVO, "1", ex=ttl)
        except Exception:
            pass


def worker_esta_ativo(cliente=None) -> bool:
    """Verifica se ha um worker ativo registrado no Redis."""
    c = cliente or obter_cliente_redis()
    if c is None:
        return False
    try:
        return c.get(CHAVE_WORKER_ATIVO) == "1"
    except Exception:
        return False


def publicar_tarefa(fila: str, payload: dict, cliente=None) -> str | None:
    """Enfileira uma mensagem na fila Redis e retorna o ID da tarefa."""
    c = cliente or obter_cliente_redis()
    if c is None:
        return None
    tarefa_id = payload.get("id") or str(uuid.uuid4())
    mensagem = {**payload, "id": tarefa_id}
    try:
        c.set(
            f"tarefa:{tarefa_id}",
            json.dumps({"id": tarefa_id, "status": "enfileirada"}),
            ex=300,
        )
        c.rpush(fila, json.dumps(mensagem))
        return tarefa_id
    except Exception:
        return None


def responder_tarefa(cliente, tarefa_id: str, resultado: dict, ttl: int = 120):
    """Publica o resultado processado pelo worker para a tarefa correspondente."""
    if cliente is None or not tarefa_id:
        return
    dados = json.dumps(resultado)
    try:
        cliente.set(f"tarefa:{tarefa_id}", dados, ex=ttl)
        cliente.rpush(f"resposta:{tarefa_id}", dados)
        cliente.expire(f"resposta:{tarefa_id}", ttl)
    except Exception:
        pass


def aguardar_resposta_fila(tarefa_id: str, timeout: int = 3, cliente=None) -> dict | None:
    """Aguarda (via BLPOP) a conclusao de uma tarefa pelo worker."""
    c = cliente or obter_cliente_redis()
    if c is None or not tarefa_id:
        return None
    try:
        item = c.blpop(f"resposta:{tarefa_id}", timeout=timeout)
        if not item:
            return None
        _, conteudo = item
        return json.loads(conteudo)
    except Exception:
        return None


def obter_status_tarefa(tarefa_id: str, cliente=None) -> dict | None:
    """Consulta o estado atual de uma tarefa no Redis."""
    c = cliente or obter_cliente_redis()
    if c is None:
        return None
    try:
        conteudo = c.get(f"tarefa:{tarefa_id}")
        return json.loads(conteudo) if conteudo else None
    except Exception:
        return None

