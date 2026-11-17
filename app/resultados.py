"""Lee la búsqueda y la lista exportadas.

El archivo es una lista JSON de cortes (``clip_id``, ``interview_id``,
``title``, ``speaker``, ``start``/``end`` en segundos, ``transcript_text``…).
Un mismo ``record_id`` (historia o tema) puede tener varios cortes no
contiguos; el orden de reproducción es ``selection_order`` y después
``source_number``.

Los tiempos pueden llamarse ``start``/``end`` o ``start_time``/``end_time``.

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
from .tiempos import leer_fin, leer_inicio

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
    tipo: str = "ambos"
    terminos: tuple[str, ...] = ()
    # Cómo se llegó a esta lista (solo si se conoce): parámetros, nº de candidatos
    # por índice y candidatos descartados por solapamiento.
    parametros: tuple[tuple[str, Any], ...] = ()
    n_candidatos: tuple[int, int] | None = None  # (temas, historias)
    descartados: tuple[tuple[str, float | None], ...] = ()  # (título, fracción ya cubierta)

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


def _bandera(valor: Any) -> str:
    """Texto de un aviso de calidad, que puede ser una cadena o un objeto."""
    if isinstance(valor, str):
        return valor.strip()
    if isinstance(valor, dict):
        return _cadena(valor.get("reason")) or _cadena(valor.get("kind"))
    return ""


def _interpretar(bruto: Any, posicion: int) -> tuple[Corte | None, str | None]:
    if not isinstance(bruto, dict):
        return None, f"El elemento {posicion} no es un objeto."
    clip_id = _cadena(bruto.get("clip_id"))
    interview_id = _cadena(bruto.get("interview_id"))
    inicio, fin = leer_inicio(bruto), leer_fin(bruto)
    if not clip_id or not interview_id:
        return None, f"El elemento {posicion} no tiene clip_id o interview_id."
    if inicio is None or fin is None or inicio < 0 or fin <= inicio:
        return None, f"El corte {clip_id} tiene un intervalo no válido ({inicio}–{fin})."
    banderas = [_bandera(b) for b in bruto.get("quality_flags") or [] if _bandera(b)]
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
        banderas=tuple(banderas),
    )
    return corte, None


def corte_desde_playlist(bruto: Any) -> Corte | None:
    """Un solo corte a partir de un elemento de lista; None si no es válido."""
    if bruto is None:
        return None
    return _interpretar(bruto, 1)[0]


def construir_busqueda(brutos: Any, **datos: Any) -> Busqueda:
    """Convierte una lista de cortes (``playlist``) en una ``Busqueda``.

    ``datos`` se pasa tal cual a ``Busqueda`` (consulta, fecha, es_demo…). Los
    elementos no válidos se omiten con un aviso; el orden es el de
    ``selection_order`` y después ``source_number``.
    """
    avisos: list[str] = []
    if isinstance(brutos, dict):  # tolera {"clips": [...]} o {"cortes": [...]}
        brutos = brutos.get("clips") or brutos.get("cortes") or []
    if not isinstance(brutos, list):
        return Busqueda((), avisos=("La lista exportada no tiene el formato esperado.",), **datos)

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
    return Busqueda(cortes=tuple(finales), avisos=tuple(avisos), **datos)


def busqueda_en_directo(resultado: dict) -> Busqueda:
    """``Busqueda`` a partir de lo que devuelve ``Buscador.buscar``."""
    titulos = {c["id"]: c["title"] for lista in resultado["candidatos"].values() for c in lista}
    descartados = tuple(
        (titulos.get(d["id"], d["id"]), d.get("fraccion_ya_cubierta")) for d in resultado["descartados"]
    )
    cand = resultado["candidatos"]
    return construir_busqueda(
        resultado["playlist"],
        consulta=resultado["consulta"], es_demo=False,
        tipo=resultado["parametros"]["tipo"],
        terminos=tuple(resultado["terminos_obligatorios"]),
        parametros=tuple(resultado["parametros"].items()),
        n_candidatos=(len(cand.get("tema", [])), len(cand.get("historia", []))),
        descartados=descartados,
    )


def _referencia(ajustes: Ajustes) -> dict[str, Any]:
    """Consulta y fecha de la búsqueda guardada, si hay un archivo de referencia."""
    try:
        with (ajustes.datos_ruta / ajustes.busqueda_referencia_archivo).open(encoding="utf-8") as f:
            ref = json.load(f)
    except (OSError, ValueError):
        return {}
    return ref if isinstance(ref, dict) else {}


def cargar_busqueda(ajustes: Ajustes) -> Busqueda:
    """La búsqueda guardada de demostración (``playlist.json``)."""
    ruta = ajustes.datos_ruta / ajustes.playlist_archivo
    try:
        with ruta.open(encoding="utf-8") as f:
            brutos = json.load(f)
    except FileNotFoundError:
        log.error("No existe la búsqueda guardada: %s", ruta)
        return Busqueda((), avisos=(f"No se encuentra {ajustes.playlist_archivo}.",),)
    except (OSError, ValueError) as exc:
        log.error("No se pudo leer %s: %s", ruta, exc)
        return Busqueda((), avisos=(f"No se pudo leer {ajustes.playlist_archivo}.",),)

    ref = _referencia(ajustes)
    consulta = ajustes.demo_consulta.strip() or str(ref.get("consulta") or "").strip()
    fecha = (ajustes.demo_fecha.strip() or str(ref.get("exported_at_utc") or "").strip()
             or fecha_desde_nombre(ajustes.playlist_archivo))
    parametros = ref.get("parametros") if isinstance(ref.get("parametros"), dict) else {}
    return construir_busqueda(
        brutos, consulta=consulta, fecha=fecha, archivo=ajustes.playlist_archivo, es_demo=True,
        tipo=str(parametros.get("tipo") or "ambos"),
        terminos=tuple(ref.get("terminos_obligatorios") or ()),
    )
