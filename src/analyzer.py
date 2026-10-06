"""Analisadores de feedback: OpenAI, Gemini e um modo mock (sem API) para testes."""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from src import config

log = logging.getLogger(__name__)

CATEGORIAS = [
    "atendimento",
    "preco",
    "estrutura",
    "limpeza",
    "app_usabilidade",
    "desempenho_treino",
    "outros",
]

SYSTEM_PROMPT = f"""Você é um analista de experiência do cliente. Receberá uma lista de feedbacks \
em português (avaliações de academias, apps de corrida e apps de bem-estar).

Para CADA feedback, retorne um objeto com:
- "id": o mesmo id recebido (inteiro)
- "sentimento": "positivo", "negativo" ou "neutro"
- "score": número de -1.0 (muito negativo) a 1.0 (muito positivo)
- "categoria": o tema principal, UMA entre {CATEGORIAS}
- "pontos_atencao": lista de 0 a 3 problemas ou pontos de melhoria concretos, em frases curtas \
(máx. 8 palavras). Lista vazia se não houver.
- "resumo": resumo do feedback em uma frase.

Responda SOMENTE com JSON válido no formato {{"resultados": [ ... ]}}, sem texto extra."""


class Analise(BaseModel):
    id: int
    sentimento: Literal["positivo", "negativo", "neutro"]
    score: float = Field(ge=-1.0, le=1.0)
    categoria: str = "outros"
    pontos_atencao: list[str] = Field(default_factory=list)
    resumo: str = ""

    @field_validator("sentimento", mode="before")
    @classmethod
    def _norm_sentimento(cls, v: str) -> str:
        return str(v).strip().lower()

    @field_validator("categoria", mode="before")
    @classmethod
    def _norm_categoria(cls, v: str) -> str:
        v = str(v).strip().lower()
        return v if v in CATEGORIAS else "outros"

    @field_validator("pontos_atencao", mode="before")
    @classmethod
    def _norm_pontos(cls, v):
        if v is None:
            return []
        return [str(p).strip()[:200] for p in v if str(p).strip()]


def build_prompt(itens: list[tuple[int, str]]) -> str:
    payload = [{"id": i, "texto": t} for i, t in itens]
    return "Analise os feedbacks abaixo:\n\n" + json.dumps(payload, ensure_ascii=False)


def parse_response(raw: str) -> dict[int, Analise]:
    """Converte a resposta bruta da IA em objetos validados. Itens inválidos são ignorados."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(raw)
    itens = data["resultados"] if isinstance(data, dict) else data

    resultado: dict[int, Analise] = {}
    for item in itens:
        try:
            a = Analise.model_validate(item)
            resultado[a.id] = a
        except (ValidationError, TypeError) as exc:
            log.warning("Resposta inválida ignorada (%s): %s", exc.__class__.__name__, item)
    return resultado


class BaseAnalyzer(ABC):
    nome_modelo: str = "desconhecido"
    max_tentativas = 4

    @abstractmethod
    def _call(self, prompt: str) -> str:
        """Envia o prompt ao provedor e devolve o texto JSON bruto."""

    def analyze(self, itens: list[tuple[int, str]]) -> dict[int, Analise]:
        prompt = build_prompt(itens)
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                return parse_response(self._call(prompt))
            except Exception as exc:  # rede, rate limit, JSON quebrado...
                espera = 2**tentativa
                log.warning(
                    "Falha na tentativa %d/%d (%s). Novo try em %ds.",
                    tentativa, self.max_tentativas, exc, espera,
                )
                if tentativa == self.max_tentativas:
                    raise
                time.sleep(espera)
        return {}


class OpenAIAnalyzer(BaseAnalyzer):
    def __init__(self) -> None:
        from openai import OpenAI

        if not config.OPENAI_API_KEY:
            raise RuntimeError("Defina OPENAI_API_KEY no arquivo .env")
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.nome_modelo = config.OPENAI_MODEL

    def _call(self, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.nome_modelo,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""


class GeminiAnalyzer(BaseAnalyzer):
    def __init__(self) -> None:
        from google import genai

        if not config.GEMINI_API_KEY:
            raise RuntimeError("Defina GEMINI_API_KEY no arquivo .env")
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.nome_modelo = config.GEMINI_MODEL

    def _call(self, prompt: str) -> str:
        resp = self.client.models.generate_content(
            model=self.nome_modelo,
            contents=prompt,
            config={
                "system_instruction": SYSTEM_PROMPT,
                "response_mime_type": "application/json",
                "temperature": 0,
            },
        )
        return resp.text or ""


class MockAnalyzer(BaseAnalyzer):
    """Heurística simples por palavras-chave. Só para testar o pipeline sem gastar API."""

    nome_modelo = "mock-keywords"

    POS = ["adorei", "ótimo", "otimo", "excelente", "recomendo", "melhor", "incríveis",
           "motivam", "ajudaram", "mudou", "lindo", "simpática", "gosto", "bonita"]
    NEG = ["quebrado", "péssimo", "pessimo", "sujo", "trava", "não funciona", "nao funciona",
           "impreciso", "lento", "ignorado", "burocrático", "duplicada", "não consigo",
           "irritar", "caro", "cara ", "ninguém", "confusa", "demais"]
    TEMAS = {
        "limpeza": ["sujo", "higiênico", "limpo"],
        "atendimento": ["atendimento", "recepção", "suporte", "atenciosos", "chamado", "cancel"],
        "preco": ["mensalidade", "caro", "cara", "assinatura", "custo", "valor", "cobraram"],
        "app_usabilidade": ["app", "gps", "trava", "interface", "navegação", "notificações",
                            "sincroniza", "atualização", "anúncios"],
        "estrutura": ["aparelhos", "equipamentos", "ar condicionado", "estrutura", "pico"],
        "desempenho_treino": ["treino", "treinos", "aulas", "exercícios", "meditações", "desafios"],
    }
    PROBLEMAS = {
        "aparelhos quebrados": "Equipamentos quebrados sem manutenção",
        "sujo": "Vestiário sem limpeza adequada",
        "trava": "App trava durante o uso",
        "gps": "Precisão do GPS",
        "sincroniza": "Falha na sincronização com relógio",
        "cancel": "Dificuldade para cancelar",
        "mensalidade": "Preço da mensalidade",
        "assinatura": "Preço/gestão da assinatura",
        "recepção": "Atendimento da recepção",
        "notificações": "Excesso de notificações",
        "anúncios": "Anúncios na versão gratuita",
        "navegação": "Navegação confusa",
        "ar condicionado": "Falta de ar condicionado",
    }

    def _call(self, prompt: str) -> str:  # pragma: no cover - não usado
        raise NotImplementedError

    def analyze(self, itens: list[tuple[int, str]]) -> dict[int, Analise]:
        saida: dict[int, Analise] = {}
        for id_, texto in itens:
            t = texto.lower()
            pos = sum(k in t for k in self.POS)
            neg = sum(k in t for k in self.NEG)
            score = max(-1.0, min(1.0, (pos - neg) / 3))
            sent = "positivo" if score > 0.15 else "negativo" if score < -0.15 else "neutro"
            categoria = next(
                (c for c, kws in self.TEMAS.items() if any(k in t for k in kws)), "outros"
            )
            pontos = [v for k, v in self.PROBLEMAS.items() if k in t] if sent != "positivo" else []
            saida[id_] = Analise(
                id=id_, sentimento=sent, score=round(score, 2), categoria=categoria,
                pontos_atencao=pontos[:3], resumo=texto[:100],
            )
        return saida


def get_analyzer(provider: str) -> BaseAnalyzer:
    provider = provider.lower()
    if provider == "openai":
        return OpenAIAnalyzer()
    if provider == "gemini":
        return GeminiAnalyzer()
    if provider == "mock":
        return MockAnalyzer()
    raise ValueError(f"Provedor desconhecido: {provider} (use openai, gemini ou mock)")
