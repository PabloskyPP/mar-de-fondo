"""Entrevistas, transcripciones y mapa de vídeos.

Fuentes, de menor a mayor prioridad para cada dato:

1. ``video_mapping.json``: identificador, persona y nombre del MP4. Sus
   ``video_path`` apuntan a Colab y **no se usan como ruta**: solo se toma el
   nombre del archivo, que se busca en ``MDF_VIDEOS_DIR``.
2. La búsqueda guardada: añade las entrevistas que falten y su persona.
3. ``entrevistas.json`` (opcional): título, lugar, temas, duración, capítulos y
   transcripción. Si no existe o no tiene el formato esperado, el portal sigue
   funcionando y la pantalla de Entrevista usa los cortes de la búsqueda.

Formato esperado de ``entrevistas.json`` (todo salvo ``interview_id`` es
opcional; los tiempos van en segundos)::

    {"entrevistas": [{
        "interview_id": "Carmen_Vileda_Rey",
        "speaker": "Carmen Vileda Rey",
        "title": "…", "lugar": "…", "temas": ["…"], "duration": 839.0,
        "capitulos": [{"title": "…", "start": 0.0, "end": 95.2}],
        "transcripcion": [{"start": 3.1, "end": 9.8, "text": "…", "speaker": "…"}]
    }]}
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .config import Ajustes

if TYPE_CHECKING:  # pragma: no cover
    from .resultados import Corte

log = logging.getLogger("mardefondo.catalogo")


@dataclass(frozen=True)
class Capitulo:
    titulo: str
    inicio: float
    fin: float | None


@dataclass(frozen=True)
class Turno:
    inicio: float
    fin: float | None
    texto: str
    hablante: str | None


@dataclass(frozen=True)
class Entrevista:
    interview_id: str
    persona: str
    titulo: str | None = None
    lugar: str | None = None
    temas: tuple[str, ...] = ()
    duracion: float | None = None
    nombre_video: str = ""
    capitulos: tuple[Capitulo, ...] = ()
    transcripcion: tuple[Turno, ...] = ()
    coleccion: str | None = None

    @property
    def encabezado(self) -> str:
        return self.titulo or self.persona

    @property
    def iniciales(self) -> str:
        partes = [p for p in re.split(r"\s+", self.persona) if p]
        return "".join(p[0] for p in partes[:2]).upper() or "?"


@dataclass(frozen=True)
class EstadoVideo:
    ruta: Path | None
    esperado: str
    motivo: str = ""

    @property
    def disponible(self) -> bool:
        return self.ruta is not None


# ---------------------------------------------------------------------------
# Localización de los MP4
# ---------------------------------------------------------------------------

def _nombre_base(ruta: str) -> str:
    """Último componente de una ruta, venga con «/» (Colab) o con «\\»."""
    return re.split(r"[\\/]", ruta.strip())[-1]


def _clave(nombre: str) -> str:
    return unicodedata.normalize("NFC", nombre).casefold()


def _buscar_en(carpeta: Path, nombre: str) -> Path | None:
    """Busca ``nombre`` en ``carpeta`` tolerando mayúsculas y NFC/NFD."""
    directo = carpeta / nombre
    if directo.is_file():
        return directo
    try:
        objetivo = _clave(nombre)
        for hijo in carpeta.iterdir():
            if hijo.is_file() and _clave(hijo.name) == objetivo:
                return hijo
    except OSError:
        return None
    return None


def localizar_video(
    ajustes: Ajustes, interview_id: str, nombre_mapa: str
) -> EstadoVideo:
    """Resuelve el MP4 de una entrevista a partir de la configuración local."""
    esperado = nombre_mapa or f"{interview_id}.mp4"
    carpeta = ajustes.videos_ruta

    extra = ajustes.videos_extra.get(interview_id)
    if extra:
        ruta = Path(extra).expanduser()
        if not ruta.is_absolute():
            if carpeta is None:
                return EstadoVideo(None, extra, "MDF_VIDEOS_DIR no está definido")
            ruta = carpeta / ruta
        if ruta.is_file():
            return EstadoVideo(ruta, ruta.name)
        return EstadoVideo(None, ruta.name, "el archivo de MDF_VIDEOS_EXTRA no existe")

    if carpeta is None:
        return EstadoVideo(None, esperado, "MDF_VIDEOS_DIR no está definido")
    if not carpeta.is_dir():
        return EstadoVideo(None, esperado, "la carpeta de vídeos no existe")

    candidatos = [esperado]
    if f"{interview_id}.mp4" != esperado:
        candidatos.append(f"{interview_id}.mp4")
    for nombre in candidatos:
        ruta = _buscar_en(carpeta, nombre)
        if ruta:
            return EstadoVideo(ruta, ruta.name)
    return EstadoVideo(None, esperado, "no se encuentra en la carpeta de vídeos")


# ---------------------------------------------------------------------------
# Lectura de datos
# ---------------------------------------------------------------------------

def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor or valor in (float("inf"), float("-inf")):
        return None
    return float(valor)


def _texto(valor: Any) -> str | None:
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    return None


def _leer_json(ruta: Path) -> Any | None:
    try:
        with ruta.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer %s: %s", ruta, exc)
        return None


def _capitulos(brutos: Any) -> tuple[Capitulo, ...]:
    if not isinstance(brutos, list):
        return ()
    items: list[tuple[str, float, float | None]] = []
    for b in brutos:
        if not isinstance(b, dict):
            continue
        titulo, inicio = _texto(b.get("title")), _numero(b.get("start"))
        if titulo is None or inicio is None or inicio < 0:
            continue
        items.append((titulo, inicio, _numero(b.get("end"))))
    items.sort(key=lambda x: x[1])
    salida = []
    for i, (titulo, inicio, fin) in enumerate(items):
        if fin is None and i + 1 < len(items):
            fin = items[i + 1][1]
        salida.append(Capitulo(titulo, inicio, fin if fin and fin > inicio else None))
    return tuple(salida)


def _turnos(brutos: Any) -> tuple[Turno, ...]:
    if not isinstance(brutos, list):
        return ()
    salida = []
    for b in brutos:
        if not isinstance(b, dict):
            continue
        texto, inicio = _texto(b.get("text")), _numero(b.get("start"))
        if texto is None or inicio is None or inicio < 0:
            continue
        fin = _numero(b.get("end"))
        salida.append(
            Turno(inicio, fin if fin and fin > inicio else None, texto,
                  _texto(b.get("speaker")))
        )
    salida.sort(key=lambda t: t.inicio)
    return tuple(salida)


def _entrevistas_extra(ruta: Path) -> dict[str, dict[str, Any]]:
    datos = _leer_json(ruta)
    if datos is None:
        return {}
    lista = datos.get("entrevistas") if isinstance(datos, dict) else datos
    if not isinstance(lista, list):
        log.warning("%s no tiene el formato esperado; se ignora.", ruta)
        return {}
    salida: dict[str, dict[str, Any]] = {}
    for item in lista:
        if isinstance(item, dict) and _texto(item.get("interview_id")):
            salida[item["interview_id"].strip()] = item
    return salida


class Catalogo:
    def __init__(self, ajustes: Ajustes, entrevistas: Iterable[Entrevista]):
        self._ajustes = ajustes
        self._entrevistas = {e.interview_id: e for e in entrevistas}

    def __len__(self) -> int:
        return len(self._entrevistas)

    def todas(self) -> list[Entrevista]:
        return list(self._entrevistas.values())

    def obtener(self, interview_id: str) -> Entrevista | None:
        return self._entrevistas.get(interview_id)

    def estado_video(self, interview_id: str) -> EstadoVideo:
        e = self._entrevistas.get(interview_id)
        nombre = e.nombre_video if e else ""
        return localizar_video(self._ajustes, interview_id, nombre)

    @property
    def lugares(self) -> list[str]:
        return sorted({e.lugar for e in self._entrevistas.values() if e.lugar},
                      key=_clave)

    @property
    def temas(self) -> list[str]:
        return sorted({t for e in self._entrevistas.values() for t in e.temas},
                      key=_clave)

    def filtrar(
        self, persona: str = "", tema: str = "", lugar: str = ""
    ) -> list[Entrevista]:
        aguja = _clave(persona.strip())
        resultado = []
        for e in self._entrevistas.values():
            if aguja and aguja not in _clave(f"{e.persona} {e.titulo or ''}"):
                continue
            if tema and tema not in e.temas:
                continue
            if lugar and e.lugar != lugar:
                continue
            resultado.append(e)
        return resultado


def cargar_catalogo(ajustes: Ajustes, cortes: Iterable["Corte"] = ()) -> Catalogo:
    datos_dir = ajustes.datos_ruta

    mapa = _leer_json(datos_dir / ajustes.mapa_videos_archivo) or {}
    entradas_mapa = mapa.get("interviews", {}) if isinstance(mapa, dict) else {}
    if not entradas_mapa:
        log.warning(
            "Sin mapa de vídeos válido en %s: se usarán solo los identificadores "
            "de la búsqueda.", datos_dir / ajustes.mapa_videos_archivo,
        )
    coleccion = None
    if isinstance(mapa, dict) and "Carriza" in str(mapa.get("description", "")):
        coleccion = "Carriza"

    extra = _entrevistas_extra(datos_dir / ajustes.entrevistas_archivo)

    orden: list[str] = []
    personas: dict[str, str] = {}
    videos: dict[str, str] = {}

    for iid, datos in entradas_mapa.items():
        if not isinstance(datos, dict):
            continue
        orden.append(iid)
        personas[iid] = _texto(datos.get("speaker")) or ""
        ruta_video = _texto(datos.get("video_path")) or ""
        videos[iid] = _nombre_base(ruta_video) if ruta_video else ""

    for corte in cortes:
        if corte.interview_id not in personas:
            orden.append(corte.interview_id)
            personas[corte.interview_id] = corte.persona
            videos[corte.interview_id] = ""
            log.warning(
                "La entrevista %s aparece en la búsqueda pero no en el mapa de "
                "vídeos.", corte.interview_id,
            )
        elif not personas[corte.interview_id]:
            personas[corte.interview_id] = corte.persona

    for iid in extra:
        if iid not in personas:
            orden.append(iid)
            personas[iid] = ""
            videos[iid] = ""

    entrevistas = []
    for iid in dict.fromkeys(orden):
        e = extra.get(iid, {})
        temas = e.get("temas")
        entrevistas.append(
            Entrevista(
                interview_id=iid,
                persona=_texto(e.get("speaker")) or personas.get(iid) or iid,
                titulo=_texto(e.get("title")),
                lugar=_texto(e.get("lugar")),
                temas=tuple(t.strip() for t in temas if _texto(t))
                if isinstance(temas, list) else (),
                duracion=_numero(e.get("duration")),
                nombre_video=videos.get(iid, ""),
                capitulos=_capitulos(e.get("capitulos")),
                transcripcion=_turnos(e.get("transcripcion")),
                coleccion=coleccion,
            )
        )
    return Catalogo(ajustes, entrevistas)
