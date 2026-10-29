"""
Circuit breaker de referencia - Aula 13.

Este arquivo esta COMPLETO e serve de modelo. Voce vai adapta-lo para
proteger a chamada ao LLM no servico de geracao.

Estados:
  fechado  -> deixa passar (normal)
  aberto   -> recusa na hora, sem tentar (servico esta quebrado)
  meio     -> deixa passar UMA chamada de teste para ver se voltou
"""
import time


class CircuitoAberto(Exception):
    """Levantada quando o disjuntor esta aberto e recusa a chamada."""


class CircuitBreaker:
    """Implementacao do padrao Circuit Breaker para evitar chamadas seguidas

    a servicos externos instaveis ou fora do ar.
    """
    def __init__(self, limite_falhas=5, espera_segundos=30):
        # Quantidade de falhas seguidas permitidas antes de abrir o circuito
        self.limite_falhas = limite_falhas
        
        # Tempo (em segundos) que o circuito permanece aberto apos atingir o limite
        self.espera_segundos = espera_segundos
        
        # Contador atual de falhas consecutivas
        self.falhas = 0
        
        # Estado inicial do circuito: "fechado" (operacao normal)
        self.estado = "fechado"
        
        # Timestamp de quando o circuito foi aberto pela ultima vez
        self.aberto_em = 0.0

    def chamar(self, funcao, *args, **kwargs):
        """Executa a funcao fornecida aplicando a regra de protecao do Circuit Breaker.

        
        :param funcao: A funcao/metodo que sera executado (ex: chamada de API/LLM).
        :param args: Argumentos posicionais para a funcao.
        :param kwargs: Argumentos nomeados para a funcao.
        :return: O resultado da execucao da funcao.
        :raises CircuitoAberto: Se o circuito estiver aberto e no periodo de espera.
        """

        if self.estado == "aberto":
            if time.time() - self.aberto_em >= self.espera_segundos:
                self.estado = "meio"
            else:
                raise CircuitoAberto("disjuntor aberto: chamada recusada")
        try:
            resultado = funcao(*args, **kwargs)
        except Exception:
            self._registrar_falha()
            raise
        self._registrar_sucesso()
        return resultado

    def _registrar_falha(self):
        """Atualiza os contadores internos quando ocorre uma falha na chamada."""
        self.falhas += 1
        if self.falhas >= self.limite_falhas or self.estado == "meio":
            self.estado = "aberto"
            self.aberto_em = time.time()

    def _registrar_sucesso(self):
        """Reseta o estado e os contadores apos uma chamada executada com sucesso."""
        self.falhas = 0
        self.estado = "fechado"
