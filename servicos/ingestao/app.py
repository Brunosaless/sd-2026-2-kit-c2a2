"""
Servico 1 - INGESTAO: recebe documentos, gera embeddings e armazena.

JA PRONTO: indexacao em memoria e a rota de status.
TAREFA 1: persistir o indice em disco para nao perder ao reiniciar.
TAREFA 4: suporte a mensageria via fila Redis + worker.

Rodar: uvicorn servicos.ingestao.app:app --port 8001
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from servicos.comum.embeddings import Vetorizador
from servicos.comum.fila import (
    FILA_INGESTAO,
    aguardar_resposta_fila,
    obter_status_tarefa,
    publicar_tarefa,
    worker_esta_ativo,
)

app = FastAPI(title="Ingestao - C2.A2")

vetorizador = Vetorizador()
INDICE = []
_ULTIMA_MODIFICACAO = 0.0


def obter_caminho_indice() -> Path:
    return Path(os.getenv("CAMINHO_INDICE", "dados/indice.json"))


def salvar_indice(itens: list[dict] | None = None):
    """Persiste o indice vetorial em arquivo JSON no disco."""
    global _ULTIMA_MODIFICACAO
    dados = INDICE if itens is None else itens
    caminho = obter_caminho_indice()
    if caminho.parent and str(caminho.parent) != ".":
        caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho_tmp = caminho.with_suffix(".tmp")
    with open(caminho_tmp, "w", encoding="utf-8") as arq:
        json.dump(dados, arq, ensure_ascii=False, indent=2)
    os.replace(caminho_tmp, caminho)
    try:
        _ULTIMA_MODIFICACAO = caminho.stat().st_mtime
    except OSError:
        pass


def carregar_indice() -> list[dict]:
    """Carrega o indice persistido do disco para a memoria e ajusta o vetorizador."""
    global _ULTIMA_MODIFICACAO
    caminho = obter_caminho_indice()
    if not caminho.exists():
        return INDICE

    try:
        mtime = caminho.stat().st_mtime
        if INDICE and mtime == _ULTIMA_MODIFICACAO:
            return INDICE
        with open(caminho, "r", encoding="utf-8") as arq:
            dados = json.load(arq)
        if isinstance(dados, list):
            INDICE.clear()
            INDICE.extend(dados)
            if INDICE:
                vetorizador.ajustar([item["texto"] for item in INDICE])
                precisa_salvar = False
                for item in INDICE:
                    if "vetor" not in item or not item["vetor"]:
                        item["vetor"] = vetorizador.vetorizar(item["texto"])
                        precisa_salvar = True
                if precisa_salvar:
                    salvar_indice(INDICE)
            _ULTIMA_MODIFICACAO = mtime
    except Exception:
        pass
    return INDICE


def executar_indexacao_local(novos_docs: list[dict]) -> int:
    """Atualiza o indice com novos documentos, recalcula os vetores e persiste em disco."""
    carregar_indice()
    existentes = {(item["texto"], item.get("origem", "desconhecida")) for item in INDICE}
    for doc in novos_docs:
        texto = doc["texto"]
        origem = doc.get("origem", "desconhecida")
        if (texto, origem) not in existentes:
            INDICE.append({"texto": texto, "origem": origem})
            existentes.add((texto, origem))

    if INDICE:
        vetorizador.ajustar([item["texto"] for item in INDICE])
        for item in INDICE:
            item["vetor"] = vetorizador.vetorizar(item["texto"])

    salvar_indice(INDICE)
    return len(INDICE)


# Carrega o indice persistido ao iniciar o servico
carregar_indice()


class Documento(BaseModel):
    texto: str
    origem: str = "desconhecida"


@app.post("/indexar")
def indexar(docs: list[Documento]):
    payload_docs = [{"texto": d.texto, "origem": d.origem} for d in docs]

    # Se houver um worker ativo consumindo a fila Redis, processa via mensageria
    if worker_esta_ativo():
        tarefa_id = publicar_tarefa(
            FILA_INGESTAO,
            {"acao": "indexar", "documentos": payload_docs},
        )
        if tarefa_id:
            resposta_worker = aguardar_resposta_fila(tarefa_id, timeout=5)
            if resposta_worker and resposta_worker.get("status") == "concluida":
                carregar_indice()
                return {
                    "indexados": len(INDICE),
                    "via": "fila_redis",
                    "tarefa_id": tarefa_id,
                }

    total = executar_indexacao_local(payload_docs)
    return {"indexados": total}


@app.post("/enfileirar", status_code=202)
def enfileirar(docs: list[Documento]):
    """Enfileira documentos no Redis para indexacao assincrona pelo worker."""
    payload_docs = [{"texto": d.texto, "origem": d.origem} for d in docs]
    tarefa_id = publicar_tarefa(
        FILA_INGESTAO,
        {"acao": "indexar", "documentos": payload_docs},
    )
    if not tarefa_id:
        total = executar_indexacao_local(payload_docs)
        return {
            "status": "processado_localmente",
            "enfileirados": len(docs),
            "indexados": total,
        }
    return {
        "tarefa_id": tarefa_id,
        "status": "enfileirada",
        "enfileirados": len(docs),
    }


@app.get("/tarefa/{tarefa_id}")
def consultar_tarefa(tarefa_id: str):
    """Consulta o status de uma tarefa de ingestao enfileirada no Redis."""
    status = obter_status_tarefa(tarefa_id)
    if not status:
        raise HTTPException(status_code=404, detail="Tarefa nao encontrada")
    return status


@app.post("/indexar-diretorio")
def indexar_diretorio(diretorio: str = "documentos"):
    """Carrega e indexa todos os arquivos .txt do diretorio informado."""
    pasta = Path(diretorio)
    if not pasta.exists() or not pasta.is_dir():
        raise HTTPException(status_code=404, detail=f"Diretorio '{diretorio}' nao encontrado")
    docs = []
    for arquivo in sorted(pasta.glob("*.txt")):
        texto = arquivo.read_text(encoding="utf-8").strip()
        if texto:
            docs.append(Documento(texto=texto, origem=arquivo.stem))
    return indexar(docs)


@app.get("/indice")
def indice():
    """Consumido pelo servico de recuperacao."""
    carregar_indice()
    return {
        "total": len(INDICE),
        "itens": [
            {"texto": i["texto"], "origem": i["origem"], "vetor": i["vetor"]}
            for i in INDICE
        ],
    }


@app.delete("/indice")
def limpar_indice():
    """Remove todos os documentos do indice e atualiza o arquivo em disco."""
    INDICE.clear()
    salvar_indice(INDICE)
    return {"status": "limpo", "documentos": 0}


@app.get("/saude")
def saude():
    carregar_indice()
    return {
        "status": "ok",
        "documentos": len(INDICE),
        "worker_ativo": worker_esta_ativo(),
    }
