"""Lectura tolerante de números y tiempos en los JSON de entrada.

Los datos del pipeline usan ``start``/``end``, pero la guía técnica también
habla de ``start_time``/``end_time``. Cualquier entrada que lleve tiempos
(capítulos, turnos, palabras, cortes) acepta ambos nombres.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

CLAVES_INICIO = ("start", "start_time")
CLAVES_FIN = ("end", "end_time")


def numero(valor: Any) -> float | None:
    """Número finito (int o float, no bool) o None."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor) if math.isfinite(valor) else None


def leer_numero(datos: Mapping[str, Any], *claves: str) -> float | None:
    """Primer valor numérico válido entre ``claves``."""
    for clave in claves:
        n = numero(datos.get(clave))
        if n is not None:
            return n
    return None


def leer_inicio(datos: Mapping[str, Any]) -> float | None:
    return leer_numero(datos, *CLAVES_INICIO)


def leer_fin(datos: Mapping[str, Any]) -> float | None:
    return leer_numero(datos, *CLAVES_FIN)


def leer_texto(datos: Mapping[str, Any], *claves: str) -> str | None:
    """Primer texto no vacío entre ``claves``, sin espacios sobrantes."""
    for clave in claves:
        valor = datos.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None
