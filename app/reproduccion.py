"""Plan de «Reproducir fragmentos».

Convierte la lista ordenada de cortes en una lista de tramos reproducibles de
principio a fin, sin pasar dos veces por el mismo instante de un mismo vídeo.

Reglas (en este orden):

1. **Unión de cortes consecutivos solapados.** Si dos cortes seguidos de la
   lista son de la misma entrevista y se solapan (o se tocan, con una
   tolerancia de ``TOLERANCIA_UNION`` s), se reproducen como un único tramo
   continuo, de principio a fin cronológico. Así no se salta hacia atrás.
2. **Resta de lo ya reproducido.** Cada tramo se recorta con lo que ya se haya
   reproducido antes de esa misma entrevista, aunque venga de cortes no
   consecutivos. Un tramo puede quedar partido en dos si lo ya visto está en
   medio. Los restos menores de ``MINIMO_TRAMO`` s se descartan y, si no queda
   nada, el corte se anota como omitido.

El orden de la lista se respeta entre entrevistas y entre grupos; solo cambia
el orden interno de un grupo unido (cronológico).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

TOLERANCIA_UNION = 0.25
MINIMO_TRAMO = 0.5


class _CorteLike(Protocol):
    clip_id: str
    interview_id: str
    inicio: float
    fin: float


@dataclass(frozen=True)
class Tramo:
    interview_id: str
    inicio: float
    fin: float
    clip_ids: tuple[str, ...]

    @property
    def duracion(self) -> float:
        return self.fin - self.inicio


@dataclass(frozen=True)
class Plan:
    tramos: tuple[Tramo, ...]
    omitidos: tuple[str, ...]  # clip_id de cortes ya cubiertos por completo
    duracion_cortes: float  # suma de las duraciones de los cortes originales
    duracion_total: float  # suma de las duraciones de los tramos

    @property
    def repetido_evitado(self) -> float:
        return max(0.0, self.duracion_cortes - self.duracion_total)


def _unir_consecutivos(cortes: Sequence[_CorteLike]) -> list[tuple[str, float, float, list[str]]]:
    grupos: list[tuple[str, float, float, list[str]]] = []
    for c in cortes:
        if grupos:
            iid, ini, fin, ids = grupos[-1]
            if (
                iid == c.interview_id
                and c.inicio <= fin + TOLERANCIA_UNION
                and c.fin >= ini - TOLERANCIA_UNION
            ):
                grupos[-1] = (iid, min(ini, c.inicio), max(fin, c.fin), ids + [c.clip_id])
                continue
        grupos.append((c.interview_id, c.inicio, c.fin, [c.clip_id]))
    return grupos


def _restar(
    inicio: float, fin: float, reproducido: Iterable[tuple[float, float]]
) -> list[tuple[float, float]]:
    piezas = [(inicio, fin)]
    for r_ini, r_fin in reproducido:
        nuevas: list[tuple[float, float]] = []
        for p_ini, p_fin in piezas:
            if r_fin <= p_ini or r_ini >= p_fin:
                nuevas.append((p_ini, p_fin))
                continue
            if r_ini > p_ini:
                nuevas.append((p_ini, r_ini))
            if r_fin < p_fin:
                nuevas.append((r_fin, p_fin))
        piezas = nuevas
    return piezas


def construir_plan(cortes: Sequence[_CorteLike]) -> Plan:
    reproducido: dict[str, list[tuple[float, float]]] = {}
    tramos: list[Tramo] = []
    omitidos: list[str] = []

    for iid, ini, fin, ids in _unir_consecutivos(cortes):
        vistos = reproducido.setdefault(iid, [])
        piezas = [
            (a, b) for a, b in _restar(ini, fin, vistos) if b - a >= MINIMO_TRAMO
        ]
        if not piezas:
            omitidos.extend(ids)
            continue
        for a, b in piezas:
            tramos.append(Tramo(iid, a, b, tuple(ids)))
            vistos.append((a, b))

    return Plan(
        tramos=tuple(tramos),
        omitidos=tuple(omitidos),
        duracion_cortes=sum(c.fin - c.inicio for c in cortes),
        duracion_total=sum(t.duracion for t in tramos),
    )
