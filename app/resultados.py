"""Lee la búsqueda y la lista exportadas.

El archivo es una lista JSON de cortes (``clip_id``, ``interview_id``,
``title``, ``speaker``, ``start``/``end`` en segundos, ``transcript_text``…).
Un mismo ``record_id`` (historia o tema) puede tener varios cortes no
contiguos; el orden de reproducción es ``selection_order`` y después
``source_number``.

El archivo exportado **no incluye el texto de la consulta ni la fecha**. Se
pueden indicar con ``MDF_DEMO_CONSULTA`` y ``MDF_DEMO_FECHA``; si no, se intenta
leer la fecha del nombre del archivo (``playlist_YYYYMMDDTHHMMSS…Z.json``).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .config import Ajustes, fecha_desde_nombre

log = logging.getLogger("mardefondo.resultados")

LONGITUD_EXTRACTO = 280


@dataclass(frozen=True)
class Corte:
    clip_id: str
    record_id: str
    tipo: str  # "historia" | "tema" | lo que traiga el archivo
    interview_id: str
    persona: str
    titulo: str
    resumen: str
    inicio: float
    fin: float
    texto: str
    orden: int
    n_fuente: int
    rank: int | None
    similitud: float | None
    banderas: tuple[str, ...] = ()
    parte: int = 1
    partes: int = 1

    @property
    def duracion(self) -> float:
        return self.fin - self.inicio

    @property
    def etiqueta_tipo(self) -> str:
        return {"historia": "Historia", "tema": "Tema"}.get(self.tipo, self.tipo.capitalize())


@dataclass(frozen=True)
class Busqueda:
    cortes: tuple[Corte, ...]
    consulta: str = ""
    fecha: str = ""
    archivo: str = ""
    avisos: tuple[str, ...] = ()
    es_demo: bool = True

    def por_id(self, clip_id: str) -> Corte | None:
        for c in self.cortes:
            if c.clip_id == clip_id:
                return c
        return None

    def de_entrevista(self, interview_id: str) -> list[Corte]:
        return [c for c in self.cortes if c.interview_id == interview_id]

    @property
    def n_entrevistas(self) -> int:
        return len({c.interview_id for c in self.cortes})


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------

def formatear_tiempo(segundos: float | None) -> str:
    """``mm:ss`` (o ``h:mm:ss`` a partir de una hora), truncando decimales."""
    if segundos is None or not math.isfinite(segundos) or segundos < 0:
        return "--:--"
    total = int(segundos)
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def formatear_duracion(segundos: float | None) -> str:
    """Duración en lenguaje natural: «2 min 41 s»."""
    if segundos is None or not math.isfinite(segundos) or segundos < 0:
        return "—"
    total = int(round(segundos))
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s" if s else f"{m} min"
    return f"{s} s"


def formatear_fecha(iso: str) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    sufijo = " UTC" if dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0 else ""
    return dt.strftime("%d/%m/%Y %H:%M") + sufijo


def extracto(texto: str, maximo: int = LONGITUD_EXTRACTO) -> str:
    """Primeras palabras del fragmento, cortadas en límite de palabra."""
    limpio = re.sub(r"\s+", " ", texto).strip()
    if len(limpio) <= maximo:
        return limpio
    corte = limpio[:maximo].rsplit(" ", 1)[0].rstrip(" ,;:.-—")
    return corte + "…"


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def _num(valor: Any) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor) if math.isfinite(valor) else None


def _cadena(valor: Any) -> str:
    return valor.strip() if isinstance(valor, str) else ""


def _entero(valor: Any, defecto: int) -> int:
    return int(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else defecto


def _interpretar(bruto: Any, posicion: int) -> tuple[Corte | None, str | None]:
    if not isinstance(bruto, dict):
        return None, f"El elemento {posicion} no es un objeto."
    clip_id = _cadena(bruto.get("clip_id"))
    interview_id = _cadena(bruto.get("interview_id"))
    inicio, fin = _num(bruto.get("start")), _num(bruto.get("end"))
    if not clip_id or not interview_id:
        return None, f"El elemento {posicion} no tiene clip_id o interview_id."
    if inicio is None or fin is None or inicio < 0 or fin <= inicio:
        return None, f"El corte {clip_id} tiene un intervalo no válido ({bruto.get('start')}–{bruto.get('end')})."
    banderas = bruto.get("quality_flags")
    corte = Corte(
        clip_id=clip_id,
        record_id=_cadena(bruto.get("record_id")) or clip_id,
        tipo=_cadena(bruto.get("record_type")) or "corte",
        interview_id=interview_id,
        persona=_cadena(bruto.get("speaker")) or interview_id,
        titulo=_cadena(bruto.get("title")) or clip_id,
        resumen=_cadena(bruto.get("summary")),
        inicio=inicio,
        fin=fin,
        texto=_cadena(bruto.get("transcript_text")),
        orden=_entero(bruto.get("selection_order"), posicion),
        n_fuente=_entero(bruto.get("source_number"), 1),
        rank=_entero(bruto.get("rank_in_index"), 0) or None,
        similitud=_num(bruto.get("cosine_similarity")),
        banderas=tuple(str(b) for b in banderas) if isinstance(banderas, list) else (),
    )
    return corte, None


def cargar_busqueda(ajustes: Ajustes) -> Busqueda:
    ruta = ajustes.datos_ruta / ajustes.playlist_archivo
    avisos: list[str] = []
    try:
        with ruta.open(encoding="utf-8") as f:
            brutos = json.load(f)
    except FileNotFoundError:
        log.error("No existe la búsqueda guardada: %s", ruta)
        return Busqueda((), avisos=(f"No se encuentra {ajustes.playlist_archivo}.",),)
    except (OSError, ValueError) as exc:
        log.error("No se pudo leer %s: %s", ruta, exc)
        return Busqueda((), avisos=(f"No se pudo leer {ajustes.playlist_archivo}.",),)

    if isinstance(brutos, dict):  # tolera {"clips": [...]} o {"cortes": [...]}
        brutos = brutos.get("clips") or brutos.get("cortes") or []
    if not isinstance(brutos, list):
        return Busqueda((), avisos=("La lista exportada no tiene el formato esperado.",))

    cortes: list[Corte] = []
    vistos: set[str] = set()
    for i, bruto in enumerate(brutos, start=1):
        corte, problema = _interpretar(bruto, i)
        if corte is None:
            avisos.append(problema or "Corte no válido.")
            continue
        if corte.clip_id in vistos:
            avisos.append(f"El corte {corte.clip_id} está repetido; se conserva el primero.")
            continue
        vistos.add(corte.clip_id)
        cortes.append(corte)

    cortes.sort(key=lambda c: (c.orden, c.n_fuente))  # estable: respeta el archivo

    totales: dict[str, int] = {}
    for c in cortes:
        totales[c.record_id] = totales.get(c.record_id, 0) + 1
    vistas: dict[str, int] = {}
    finales = []
    for c in cortes:
        vistas[c.record_id] = vistas.get(c.record_id, 0) + 1
        finales.append(replace(c, parte=vistas[c.record_id], partes=totales[c.record_id]))

    for aviso in avisos:
        log.warning(aviso)

    fecha = ajustes.demo_fecha.strip() or fecha_desde_nombre(ajustes.playlist_archivo)
    return Busqueda(
        cortes=tuple(finales),
        consulta=ajustes.demo_consulta.strip(),
        fecha=fecha,
        archivo=ajustes.playlist_archivo,
        avisos=tuple(avisos),
    )
