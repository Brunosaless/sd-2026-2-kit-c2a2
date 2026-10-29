"""
Ponto de entrada alternativo para o worker de mensageria.

Rodar: python -m servicos.worker
"""
from servicos.ingestao.worker import iniciar_worker

if __name__ == "__main__":
    iniciar_worker()

