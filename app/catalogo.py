"""Entrevistas, capítulos, transcripciones y mapa de vídeos.

Fuentes, de menor a mayor prioridad para cada dato:

1. ``video_mapping.json``: identificador, persona y nombre del MP4. Sus
   ``video_path`` apuntan a Colab y **no se usan como ruta**: solo se toma el
   nombre del archivo, que se busca en ``MDF_VIDEOS_DIR``.
2. La búsqueda guardada: añade las entrevistas que falten y su persona.
3. ``temas_preparados.json``: los capítulos (segmentos temáticos) de todas las
   entrevistas. Cada uno conserva su ``id`` real (``…_seg01``), título, resumen,
   ``start``/``end``, historias enlazadas, avisos de calidad y notas de
   transcripción.
4. Una transcripción con marcas temporales por entrevista, en la carpeta de
   transcripciones (ver ``transcripcion.py`` para los formatos). El archivo se
   busca por ``<interview_id>.json``, ``<interview_id>_transcripcion_corregida.json``
   o, si el nombre no lleva el sufijo numérico del identificador
   (``Pilar_Barrientos_Conde_…`` frente a ``Pilar_Barrientos_Conde_02_2``), por
   coincidencia de prefijo, siempre que sea única.
5. ``entrevistas.json`` (opcional): título, lugar, temas y, solo si no hay
   otra fuente, capítulos y transcripción en el formato propio del portal.

Si falta una fuente o no tiene el formato esperado, el portal sigue
funcionando: la pantalla de Entrevista usa los cortes de la búsqueda.

Formato opcional de ``entrevistas.json`` (tiempos en segundos; ``start_time`` y
``end_time`` valen igual que ``start`` y ``end``)::

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .config import Ajustes
from .tiempos import leer_fin, leer_inicio, leer_numero, leer_texto, numero
from .transcripcion import Transcripcion, Turno, leer_transcripcion, turnos_desde_lista

if TYPE_CHECKING:  # pragma: no cover
    from .resultados import Corte

log = logging.getLogger("mardefondo.catalogo")


@dataclass(frozen=True)
class AvisoCalidad:
    tipo: str
    motivo: str
    inicio: float | None = None
    fin: float | None = None


@dataclass(frozen=True)
class Capitulo:
    titulo: str
    inicio: float
    fin: float | None
    id: str = ""
    resumen: str = ""
    historias: tuple[str, ...] = ()
    avisos: tuple[AvisoCalidad, ...] = ()
    notas: tuple[str, ...] = ()


@dataclass(frozen=True)
class Procedencia:
    """Campos de procedencia que traen los archivos de entrada (si los traen)."""

    transcripcion_id: str | None = None
    idioma_transcripcion: str | None = None
    prob_idioma: float | None = None
    duracion_audio: float | None = None
    archivo_transcripcion: str | None = None
    formato_transcripcion: str | None = None
    archivo_origen_temas: str | None = None
    idioma_temas: str | None = None
    n_capitulos: int = 0

    @property
    def vacia(self) -> bool:
        return not any((self.transcripcion_id, self.idioma_transcripcion, self.duracion_audio,
                        self.archivo_transcripcion, self.archivo_origen_temas, self.idioma_temas))


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
    procedencia: Procedencia = Procedencia()

    @property
    def encabezado(self) -> str:
        return self.titulo or self.persona

    @property
    def iniciales(self) -> str:
        partes = [p for p in re.split(r"\s+", self.persona) if p]
        return "".join(p[0] for p in partes[:2]).upper() or "?"

    @property
    def notas_transcripcion(self) -> tuple[str, ...]:
        """Notas de los capítulos, sin repetir y en orden de aparición."""
        return tuple(dict.fromkeys(n for c in self.capitulos for n in c.notas))

    @property
    def tramos_dudosos(self) -> tuple[tuple[float, float, str], ...]:
        """Intervalos con transcripción poco fiable: (inicio, fin, motivo)."""
        tramos: dict[tuple[float, float], str] = {}
        for c in self.capitulos:
            for a in c.avisos:
                ini = a.inicio if a.inicio is not None else c.inicio
                fin = a.fin if a.fin is not None else (c.fin if c.fin is not None else c.inicio)
                tramos.setdefault((ini, fin), a.motivo)
        return tuple((i, f, m) for (i, f), m in sorted(tramos.items()))


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

def _leer_json(ruta: Path) -> Any | None:
    try:
        with ruta.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer %s: %s", ruta, exc)
        return None


def _avisos_calidad(brutos: Any) -> tuple[AvisoCalidad, ...]:
    if not isinstance(brutos, list):
        return ()
    salida = []
    for b in brutos:
        if isinstance(b, str) and b.strip():
            salida.append(AvisoCalidad("calidad", b.strip()))
        elif isinstance(b, dict):
            intervalo = b.get("affected_interval")
            intervalo = intervalo if isinstance(intervalo, dict) else {}
            salida.append(AvisoCalidad(
                tipo=leer_texto(b, "kind", "tipo") or "calidad",
                motivo=leer_texto(b, "reason", "motivo") or leer_texto(b, "kind") or "Revisar",
                inicio=leer_inicio(intervalo),
                fin=leer_fin(intervalo),
            ))
    return tuple(salida)


def _lista_de_textos(brutos: Any) -> tuple[str, ...]:
    if not isinstance(brutos, list):
        return ()
    return tuple(t.strip() for t in brutos if isinstance(t, str) and t.strip())


def _capitulo(item: Any) -> Capitulo | None:
    """Un segmento temático. Acepta ``start``/``start_time`` y ``end``/``end_time``."""
    if not isinstance(item, dict):
        return None
    titulo, inicio = leer_texto(item, "title", "titulo"), leer_inicio(item)
    if titulo is None or inicio is None or inicio < 0:
        return None
    fin = leer_fin(item)
    return Capitulo(
        titulo=titulo,
        inicio=inicio,
        fin=fin if fin is not None and fin > inicio else None,
        id=leer_texto(item, "id", "segment_id", "topic_id") or "",
        resumen=leer_texto(item, "summary", "resumen") or "",
        historias=_lista_de_textos(item.get("story_ids")),
        avisos=_avisos_calidad(item.get("quality_flags")),
        notas=_lista_de_textos(item.get("transcription_notes")),
    )


def _capitulos(brutos: Any) -> tuple[Capitulo, ...]:
    """Capítulos de una lista; el fin que falte se toma del inicio del siguiente."""
    if not isinstance(brutos, list):
        return ()
    caps = sorted((c for c in map(_capitulo, brutos) if c), key=lambda c: c.inicio)
    salida = []
    for i, c in enumerate(caps):
        if c.fin is None and i + 1 < len(caps):
            c = replace(c, fin=caps[i + 1].inicio if caps[i + 1].inicio > c.inicio else None)
        salida.append(c)
    return tuple(salida)


def _leer_temas(ruta: Path) -> tuple[dict[str, tuple[Capitulo, ...]], dict[str, dict[str, str]], dict[str, str]]:
    """Lee ``temas_preparados.json``.

    Devuelve (capítulos por entrevista, procedencia por entrevista, persona por
    entrevista). Los elementos sin entrevista, título o intervalo válido se
    omiten con un aviso en el registro.
    """
    datos = _leer_json(ruta)
    if datos is None:
        return {}, {}, {}
    lista = datos.get("temas") if isinstance(datos, dict) else datos
    if not isinstance(lista, list):
        log.warning("%s no tiene el formato esperado; se ignora.", ruta)
        return {}, {}, {}

    brutos: dict[str, list[Any]] = {}
    origen: dict[str, dict[str, str]] = {}
    personas: dict[str, str] = {}
    omitidos = 0
    for item in lista:
        if not isinstance(item, dict) or str(item.get("record_type", "topic")).lower() in ("story", "historia"):
            continue
        iid = leer_texto(item, "interview_id")
        if iid is None or _capitulo(item) is None:
            omitidos += 1
            continue
        brutos.setdefault(iid, []).append(item)
        procedencia = origen.setdefault(iid, {})
        for clave, campo in (("source_json", "origen"), ("language", "idioma")):
            valor = leer_texto(item, clave)
            if valor:
                procedencia.setdefault(campo, valor)
        persona = leer_texto(item, "speaker")
        if persona:
            personas.setdefault(iid, persona)
    if omitidos:
        log.warning("%d elementos de %s no son capítulos válidos y se omiten.", omitidos, ruta.name)
    return {iid: _capitulos(items) for iid, items in brutos.items()}, origen, personas


def _normalizar_nombre(nombre: str) -> str:
    return unicodedata.normalize("NFC", nombre).casefold()


_SUFIJOS_TRANSCRIPCION = ("_transcripcion_corregida", "_transcripcion", "_transcript")


def buscar_transcripcion(ajustes: Ajustes, interview_id: str) -> Path | None:
    """Localiza el archivo de transcripción de una entrevista (ver docstring del módulo)."""
    carpeta = ajustes.transcripciones_ruta

    extra = ajustes.transcripciones_extra.get(interview_id)
    if extra:
        ruta = Path(extra).expanduser()
        ruta = ruta if ruta.is_absolute() else carpeta / ruta
        if ruta.is_file():
            return ruta
        log.warning("MDF_TRANSCRIPCIONES_EXTRA apunta a %s, que no existe.", ruta)
        return None

    if not carpeta.is_dir():
        return None
    for nombre in (f"{interview_id}.json", *(f"{interview_id}{s}.json" for s in _SUFIJOS_TRANSCRIPCION)):
        ruta = _buscar_en(carpeta, nombre)
        if ruta:
            return ruta

    objetivo = _normalizar_nombre(interview_id)
    candidatos = []
    for hijo in sorted(carpeta.glob("*.json")):
        base = _normalizar_nombre(hijo.stem)
        for sufijo in _SUFIJOS_TRANSCRIPCION:
            if base.endswith(sufijo):
                base = base[: -len(sufijo)]
                break
        if base == objetivo or objetivo.startswith(base + "_"):
            candidatos.append(hijo)
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        log.warning("Hay varias transcripciones posibles para %s: %s. No se usa ninguna.",
                    interview_id, ", ".join(c.name for c in candidatos))
    return None


def _turnos(brutos: Any) -> tuple[Turno, ...]:
    return turnos_desde_lista(brutos) if isinstance(brutos, list) else ()


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
        if isinstance(item, dict) and leer_texto(item, "interview_id"):
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

    caps_temas, origen_temas, personas_temas = _leer_temas(datos_dir / ajustes.temas_archivo)
    extra = _entrevistas_extra(datos_dir / ajustes.entrevistas_archivo)

    orden: list[str] = []
    personas: dict[str, str] = {}
    videos: dict[str, str] = {}

    for iid, datos in entradas_mapa.items():
        if not isinstance(datos, dict):
            continue
        orden.append(iid)
        personas[iid] = leer_texto(datos, "speaker") or ""
        ruta_video = leer_texto(datos, "video_path") or ""
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

    for iid in (*caps_temas, *extra):
        if iid not in personas:
            orden.append(iid)
            personas[iid] = personas_temas.get(iid, "")
            videos[iid] = ""
            log.warning("La entrevista %s tiene capítulos o datos pero no está en el mapa de vídeos.", iid)
        elif not personas[iid]:
            personas[iid] = personas_temas.get(iid, "")

    entrevistas = []
    for iid in dict.fromkeys(orden):
        e = extra.get(iid, {})
        temas = e.get("temas")

        capitulos = caps_temas.get(iid) or _capitulos(e.get("capitulos"))

        lectura: Transcripcion | None = None
        archivo = buscar_transcripcion(ajustes, iid)
        if archivo is not None:
            lectura = leer_transcripcion(archivo)
            if lectura is None:
                archivo = None
        turnos = lectura.turnos if lectura else _turnos(e.get("transcripcion"))

        origen = origen_temas.get(iid, {})
        procedencia = Procedencia(
            transcripcion_id=lectura.transcripcion_id if lectura else None,
            idioma_transcripcion=lectura.idioma if lectura else None,
            prob_idioma=lectura.prob_idioma if lectura else None,
            duracion_audio=lectura.duracion_audio if lectura else None,
            archivo_transcripcion=archivo.name if archivo else None,
            formato_transcripcion=lectura.formato if lectura else None,
            archivo_origen_temas=origen.get("origen"),
            idioma_temas=origen.get("idioma"),
            n_capitulos=len(caps_temas.get(iid, ())),
        )

        entrevistas.append(
            Entrevista(
                interview_id=iid,
                persona=leer_texto(e, "speaker") or personas.get(iid) or iid,
                titulo=leer_texto(e, "title"),
                lugar=leer_texto(e, "lugar"),
                temas=tuple(t.strip() for t in temas if isinstance(t, str) and t.strip())
                if isinstance(temas, list) else (),
                duracion=leer_numero(e, "duration") or procedencia.duracion_audio,
                nombre_video=videos.get(iid, ""),
                capitulos=capitulos,
                transcripcion=turnos,
                coleccion=coleccion,
                procedencia=procedencia,
            )
        )
    return Catalogo(ajustes, entrevistas)
