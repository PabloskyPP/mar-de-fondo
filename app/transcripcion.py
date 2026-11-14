"""Transcripciones con marcas temporales → intervenciones clicables.

Formatos admitidos (el segundo, el de la guía técnica):

1. **Palabra a palabra** (el de ``*_transcripcion_corregida.json``): un objeto con
   ``words`` (``text``, ``start``, ``end``, ``type`` = ``word`` | ``spacing`` |
   ``audio_event``, y opcionalmente ``speaker_id``) y metadatos como
   ``transcription_id``, ``language_code``, ``language_probability`` y
   ``audio_duration_secs``. No trae hablantes ni frases: las intervenciones se
   forman aquí.
2. **Por turnos**: una lista (o un objeto con ``turns``/``turnos``/``segments``/
   ``transcripcion``…) de ``{start|start_time, end|end_time, speaker, text}``.

En ambos, ``start_time``/``end_time`` valen igual que ``start``/``end``.

Una intervención del formato palabra a palabra termina en el punto, el signo de
interrogación o de exclamación (no en «...», que en estos datos marca una duda),
en una pausa de ``PAUSA_NUEVA_INTERVENCION`` s o en un cambio de hablante. El
``inicio`` es siempre el ``start`` real de su primera palabra: pulsar la
intervención salta a ese instante del vídeo.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tiempos import leer_fin, leer_inicio, leer_numero, leer_texto

log = logging.getLogger("mardefondo.transcripcion")

PAUSA_NUEVA_INTERVENCION = 2.0  # s de silencio que separan dos intervenciones
PAUSA_DESTACADA = 3.0  # s a partir de los cuales la interfaz marca la pausa
MAX_PALABRAS = 60  # salvaguarda: una intervención sin puntuación se corta aquí

CLAVES_LISTA = ("turns", "turnos", "segments", "segmentos", "transcripcion", "transcript", "utterances")

_PUNTUACION_SUELTA = re.compile(r"[.,;:!?…)»”\]]+")
_FIN_DE_FRASE = re.compile(r"[.?!][\"»”)\]]*$")


@dataclass(frozen=True)
class Turno:
    inicio: float
    fin: float | None
    texto: str
    hablante: str | None = None
    tipo: str = "habla"  # "habla" | "evento" (p. ej. «[música de fondo]»)
    pausa: float = 0.0  # segundos de silencio desde el turno anterior

    @property
    def tras_pausa(self) -> bool:
        return self.pausa >= PAUSA_DESTACADA


@dataclass(frozen=True)
class Transcripcion:
    turnos: tuple[Turno, ...]
    formato: str  # "palabras" | "turnos"
    transcripcion_id: str | None = None
    idioma: str | None = None
    prob_idioma: float | None = None
    duracion_audio: float | None = None


def _unir(tokens: list[str]) -> str:
    texto = ""
    for t in tokens:
        if not texto:
            texto = t
        elif _PUNTUACION_SUELTA.fullmatch(t):
            texto += t
        else:
            texto += " " + t
    return texto


def _cierra_frase(texto: str) -> bool:
    return bool(_FIN_DE_FRASE.search(texto)) and not texto.endswith("...")


def _con_pausas(turnos: list[Turno]) -> tuple[Turno, ...]:
    turnos = sorted(turnos, key=lambda t: t.inicio)
    salida: list[Turno] = []
    fin_previo: float | None = None
    for t in turnos:
        pausa = max(0.0, t.inicio - fin_previo) if fin_previo is not None else 0.0
        salida.append(Turno(t.inicio, t.fin, t.texto, t.hablante, t.tipo, round(pausa, 2)))
        fin_previo = t.fin if t.fin is not None else t.inicio
    return tuple(salida)


def turnos_desde_palabras(palabras: list[Any]) -> tuple[Turno, ...]:
    unidades: list[dict[str, Any]] = []
    actual: dict[str, Any] | None = None

    def cerrar() -> None:
        nonlocal actual
        if actual is not None and actual["tokens"]:
            unidades.append(actual)
        actual = None

    for p in palabras:
        if not isinstance(p, dict):
            continue
        texto = p.get("text")
        tipo = p.get("type", "word")
        if tipo == "spacing" or not isinstance(texto, str) or not texto.strip():
            continue
        texto = texto.strip()
        inicio = leer_inicio(p)
        if inicio is None:
            continue
        fin = leer_fin(p)
        if fin is None or fin < inicio:
            fin = inicio
        hablante = p.get("speaker_id") if isinstance(p.get("speaker_id"), str) else None

        if tipo == "audio_event":
            if actual is not None:  # «[ríe]» dentro de una frase: se queda en ella
                actual["tokens"].append(texto)
                actual["fin"] = max(actual["fin"], fin)
            else:
                unidades.append({"tokens": [texto], "inicio": inicio, "fin": fin,
                                 "hablante": hablante, "tipo": "evento", "palabras": 0})
            continue

        if actual is not None and (
            inicio - actual["fin"] >= PAUSA_NUEVA_INTERVENCION or hablante != actual["hablante"]
        ):
            cerrar()
        if actual is None:
            actual = {"tokens": [], "inicio": inicio, "fin": fin, "hablante": hablante,
                      "tipo": "habla", "palabras": 0}
        actual["tokens"].append(texto)
        actual["fin"] = max(actual["fin"], fin)
        actual["palabras"] += 1

        n = actual["palabras"]
        if _cierra_frase(texto) or (n >= MAX_PALABRAS and texto.endswith((",", ";", ":"))) \
                or n >= 2 * MAX_PALABRAS:
            cerrar()
    cerrar()

    return _con_pausas([
        Turno(u["inicio"], u["fin"], _unir(u["tokens"]), u["hablante"], u["tipo"])
        for u in unidades
    ])


def turnos_desde_lista(lista: list[Any]) -> tuple[Turno, ...]:
    turnos: list[Turno] = []
    for item in lista:
        if not isinstance(item, dict):
            continue
        texto = leer_texto(item, "text", "texto")
        inicio = leer_inicio(item)
        if texto is None or inicio is None or inicio < 0:
            continue
        fin = leer_fin(item)
        turnos.append(Turno(inicio, fin if fin is not None and fin > inicio else None,
                            texto, leer_texto(item, "speaker", "hablante", "speaker_id")))
    return _con_pausas(turnos)


def interpretar_transcripcion(datos: Any) -> Transcripcion | None:
    """Reconoce el formato y devuelve la transcripción, o None si no hay turnos."""
    if isinstance(datos, dict) and isinstance(datos.get("words"), list):
        turnos, formato = turnos_desde_palabras(datos["words"]), "palabras"
    else:
        lista = datos if isinstance(datos, list) else next(
            (datos[c] for c in CLAVES_LISTA if isinstance(datos, dict) and isinstance(datos.get(c), list)),
            None,
        )
        if lista is None:
            return None
        turnos, formato = turnos_desde_lista(lista), "turnos"
    if not turnos:
        return None

    meta = datos if isinstance(datos, dict) else {}
    return Transcripcion(
        turnos=turnos,
        formato=formato,
        transcripcion_id=leer_texto(meta, "transcription_id", "transcript_id"),
        idioma=leer_texto(meta, "language_code", "language"),
        prob_idioma=leer_numero(meta, "language_probability"),
        duracion_audio=leer_numero(meta, "audio_duration_secs", "audio_duration", "duration"),
    )


def leer_transcripcion(ruta: Path) -> Transcripcion | None:
    try:
        with ruta.open(encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer la transcripción %s: %s", ruta, exc)
        return None
    resultado = interpretar_transcripcion(datos)
    if resultado is None:
        log.warning("La transcripción %s no tiene un formato reconocido; se ignora.", ruta)
    return resultado
