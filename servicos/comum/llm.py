"""
Cliente de LLM - PRONTO, com modo simulado para funcionar offline.

Se a variavel de ambiente LLM_API_KEY existir, chama a API real.
Se NAO existir, entra em MODO SIMULADO e monta uma resposta a partir do
contexto recuperado. Assim o kit funciona no laboratorio sem internet.

IMPORTANTE: nunca escreva a chave no codigo. Use variavel de ambiente.
"""
import os
import re
import time

import requests

API_KEY = os.getenv("LLM_API_KEY", "")
API_URL = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
MODELO = os.getenv("LLM_MODELO", "gpt-4o-mini")
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))
MAX_RETENTATIVAS = int(os.getenv("LLM_RETENTATIVAS", "3"))
ESPERA_RETENTATIVA = float(os.getenv("LLM_ESPERA_RETENTATIVA", "0.5"))


def _simulado(pergunta, contexto):
    trechos = " ".join(contexto)[:600]
    return (
        "[MODO SIMULADO - sem LLM_API_KEY configurada]\n"
        f"Pergunta: {pergunta}\n"
        f"Com base nos documentos recuperados: {trechos}"
    )


def gerar(pergunta: str, contexto: list) -> str:
    """Monta o prompt com o contexto recuperado e chama o LLM com timeout e retentativa."""
    simular_falha = os.getenv("LLM_SIMULAR_FALHA", "").lower() in ("1", "true", "sim")
    api_key = API_KEY or os.getenv("LLM_API_KEY", "")
    api_url = os.getenv("LLM_API_URL", API_URL)
    modelo = os.getenv("LLM_MODELO", MODELO)
    timeout = float(os.getenv("LLM_TIMEOUT", str(TIMEOUT)))
    tentativas = max(1, int(os.getenv("LLM_RETENTATIVAS", str(MAX_RETENTATIVAS))))
    espera = float(os.getenv("LLM_ESPERA_RETENTATIVA", str(ESPERA_RETENTATIVA)))

    if not api_key and not simular_falha:
        return _simulado(pergunta, contexto)

    prompt = (
        "Responda a pergunta usando SOMENTE o contexto abaixo. "
        "Se a resposta nao estiver no contexto, diga que nao sabe.\n\n"
        f"Contexto:\n{chr(10).join(contexto)}\n\nPergunta: {pergunta}"
    )

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            if simular_falha:
                raise requests.ConnectionError("Falha simulada na comunicacao com o LLM")
            resposta = requests.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": modelo,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=timeout,
            )
            resposta.raise_for_status()
            return resposta.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < tentativas:
                time.sleep(espera * (2 ** (tentativa - 1)))

    raise ultimo_erro


def _tokenizar(texto: str) -> set[str]:
    stopwords = {
        "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
        "um", "uma", "uns", "umas", "para", "por", "com", "sem", "que",
        "como", "qual", "quais", "se", "ao", "aos", "as", "os", "ou", "e", "o", "a",
    }
    palavras = re.findall(r"\w+", texto.lower())
    return {p for p in palavras if len(p) > 2 and p not in stopwords}


def avaliar_resposta(pergunta: str, resposta: str, contexto: list[str]) -> dict:
    """Avalia automaticamente a qualidade e fundamentacao da resposta gerada."""
    if not resposta or not contexto:
        return {
            "nota": 0.0,
            "aderencia_contexto": 0.0,
            "cobertura_pergunta": 0.0,
            "qualidade": "insuficiente",
        }

    tokens_pergunta = _tokenizar(pergunta)
    tokens_contexto = _tokenizar(" ".join(contexto))
    corpo_resposta = re.sub(r"^\[MODO SIMULADO[^\]]*\]\s*", "", resposta)
    tokens_resposta = _tokenizar(corpo_resposta)

    if not tokens_resposta:
        aderencia = 0.0
    else:
        tokens_relevantes = tokens_resposta - tokens_pergunta
        if tokens_relevantes:
            aderencia = len(tokens_relevantes & tokens_contexto) / len(tokens_relevantes)
        else:
            aderencia = len(tokens_resposta & tokens_contexto) / len(tokens_resposta)

    if not tokens_pergunta:
        cobertura = 1.0
    else:
        cobertura = len(tokens_pergunta & (tokens_resposta | tokens_contexto)) / len(tokens_pergunta)

    nota = round(0.65 * aderencia + 0.35 * cobertura, 2)
    if nota >= 0.6:
        qualidade = "boa"
    elif nota >= 0.3:
        qualidade = "parcial"
    else:
        qualidade = "insuficiente"

    return {
        "nota": nota,
        "aderencia_contexto": round(aderencia, 2),
        "cobertura_pergunta": round(cobertura, 2),
        "qualidade": qualidade,
    }
