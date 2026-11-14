"""Rutas web, API y acceso a los vídeos de Mar de Fondo.

Arranque local::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import math
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .catalogo import Catalogo, Entrevista, cargar_catalogo
from .config import Ajustes, obtener_ajustes
from .reproduccion import Plan, construir_plan
from .resultados import (
    Busqueda,
    Corte,
    cargar_busqueda,
    extracto,
    formatear_duracion,
    formatear_fecha,
    formatear_tiempo,
)

log = logging.getLogger("mardefondo")

DIRECTORIO_APP = Path(__file__).resolve().parent
RUTAS_VOLVER_PERMITIDAS = {"/", "/resultados", "/catalogo"}


# ---------------------------------------------------------------------------
# Utilidades de URL
# ---------------------------------------------------------------------------

def ancla_corte(clip_id: str) -> str:
    return "corte-" + re.sub(r"[^A-Za-z0-9_-]", "-", clip_id)


def url_resultados(q: str = "", ancla: str = "") -> str:
    url = "/resultados" + (f"?{urlencode({'q': q})}" if q else "")
    return f"{url}#{ancla}" if ancla else url


def url_entrevista(
    interview_id: str,
    clip_id: str | None = None,
    volver: str | None = None,
    t: float | None = None,
) -> str:
    params: dict[str, str] = {}
    if clip_id:
        params["clip"] = clip_id
    elif t is not None:
        params["t"] = f"{t:g}"
    if volver:
        params["volver"] = volver
    base = f"/entrevista/{interview_id}"
    return f"{base}?{urlencode(params)}" if params else base


def volver_seguro(valor: str | None, defecto: str = "/resultados") -> str:
    """Admite solo rutas internas conocidas (evita redirecciones abiertas)."""
    if not valor or not valor.startswith("/") or valor.startswith("//"):
        return defecto
    if "\\" in valor or any(ord(c) < 32 for c in valor):
        return defecto
    if urlsplit(valor).path not in RUTAS_VOLVER_PERMITIDAS:
        return defecto
    return valor


def _segundos(valor: str | None) -> float | None:
    if valor is None:
        return None
    try:
        n = float(valor)
    except ValueError:
        return None
    return n if math.isfinite(n) and n >= 0 else None


# ---------------------------------------------------------------------------
# Construcción de datos para las plantillas
# ---------------------------------------------------------------------------

def _plan_para_navegador(
    plan: Plan, busqueda: Busqueda, catalogo: Catalogo
) -> dict[str, Any]:
    cortes = {c.clip_id: c for c in busqueda.cortes}
    disponibles = {
        e.interview_id: catalogo.estado_video(e.interview_id).disponible
        for e in catalogo.todas()
    }
    tramos = []
    for i, t in enumerate(plan.tramos):
        primero = cortes[t.clip_ids[0]]
        tramos.append(
            {
                "i": i,
                "interview_id": t.interview_id,
                "persona": primero.persona,
                "titulo": primero.titulo,
                "mas_cortes": len(t.clip_ids) - 1,
                "clip_ids": list(t.clip_ids),
                "anclas": [ancla_corte(c) for c in t.clip_ids],
                "inicio": round(t.inicio, 2),
                "fin": round(t.fin, 2),
                "url_video": f"/videos/{t.interview_id}" if disponibles.get(t.interview_id) else None,
                "url_entrevista": url_entrevista(t.interview_id, t.clip_ids[0]),
            }
        )
    return {
        "tramos": tramos,
        "omitidos": list(plan.omitidos),
        "resumen": {
            "n_tramos": len(plan.tramos),
            "duracion_total": round(plan.duracion_total, 2),
            "duracion_cortes": round(plan.duracion_cortes, 2),
            "repetido_evitado": round(plan.repetido_evitado, 2),
        },
    }


def _capitulos_y_texto(
    entrevista: Entrevista, cortes: list[Corte]
) -> dict[str, Any]:
    """Capítulos y transcripción de la entrevista.

    Usa los capítulos y la transcripción cargados cuando existen; si no, los
    cortes de la búsqueda (que son solo una parte de la entrevista y se
    etiquetan como tal).
    """
    ordenados = sorted(cortes, key=lambda c: c.inicio)
    dudosos = entrevista.tramos_dudosos

    def motivo_dudoso(inicio: float, fin: float | None) -> str | None:
        fin = inicio if fin is None else fin
        for a, b, motivo in dudosos:
            if inicio < b and (fin > a or a <= inicio <= b):
                return motivo
        return None

    if entrevista.capitulos:
        capitulos = [
            {"id": c.id, "titulo": c.titulo, "inicio": c.inicio, "fin": c.fin,
             "resumen": c.resumen, "historias": list(c.historias),
             "avisos": [a.motivo for a in c.avisos], "clip_id": None}
            for c in entrevista.capitulos
        ]
        origen_cap = "catalogo"
    else:
        capitulos = [
            {"id": "", "titulo": c.titulo, "inicio": c.inicio, "fin": c.fin,
             "resumen": "", "historias": [], "avisos": [], "clip_id": c.clip_id}
            for c in ordenados
        ]
        origen_cap = "busqueda"

    if entrevista.transcripcion:
        turnos = [
            {"inicio": t.inicio, "fin": t.fin, "texto": t.texto, "hablante": t.hablante,
             "tipo": t.tipo, "pausa": t.pausa, "tras_pausa": t.tras_pausa,
             "dudoso": motivo_dudoso(t.inicio, t.fin), "clip_id": None}
            for t in entrevista.transcripcion
        ]
        origen_txt = "catalogo"
    else:
        turnos = [
            {"inicio": c.inicio, "fin": c.fin, "texto": c.texto, "hablante": c.titulo,
             "tipo": "habla", "pausa": 0.0, "tras_pausa": False,
             "dudoso": None, "clip_id": c.clip_id}
            for c in ordenados if c.texto
        ]
        origen_txt = "busqueda"
    return {
        "capitulos": capitulos, "origen_capitulos": origen_cap,
        "turnos": turnos, "origen_transcripcion": origen_txt,
        "procedencia": entrevista.procedencia,
        "notas_transcripcion": entrevista.notas_transcripcion,
        "tramos_dudosos": dudosos,
    }


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

def crear_app(ajustes: Ajustes | None = None) -> FastAPI:
    ajustes = ajustes or obtener_ajustes()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        busqueda = cargar_busqueda(ajustes)
        catalogo = cargar_catalogo(ajustes, busqueda.cortes)
        app.state.ajustes = ajustes
        app.state.busqueda = busqueda
        app.state.catalogo = catalogo
        app.state.plan = construir_plan(busqueda.cortes)

        carpeta = ajustes.videos_ruta
        log.info("Búsqueda guardada: %d cortes, %d entrevistas.",
                 len(busqueda.cortes), busqueda.n_entrevistas)
        log.info("Carpeta de vídeos: %s", carpeta or "(sin definir: MDF_VIDEOS_DIR)")
        for e in catalogo.todas():
            estado = catalogo.estado_video(e.interview_id)
            if estado.disponible:
                log.info("  vídeo OK      %-30s %s", e.interview_id, estado.ruta)
            else:
                log.warning("  vídeo FALTA   %-30s espera «%s» (%s)",
                            e.interview_id, estado.esperado, estado.motivo)
        yield

    app = FastAPI(title="Mar de Fondo", docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=DIRECTORIO_APP / "static"), name="static")

    plantillas = Jinja2Templates(directory=str(DIRECTORIO_APP / "templates"))
    env = plantillas.env
    env.filters["tiempo"] = formatear_tiempo
    env.filters["duracion"] = formatear_duracion
    env.filters["fecha"] = formatear_fecha
    env.filters["extracto"] = extracto
    env.globals.update(
        url_resultados=url_resultados,
        url_entrevista=url_entrevista,
        ancla_corte=ancla_corte,
    )

    def estado(request: Request):
        s = request.app.state
        return s.busqueda, s.catalogo, s.plan

    def responder(request: Request, plantilla: str, contexto: dict[str, Any], status: int = 200):
        return plantillas.TemplateResponse(request, plantilla, contexto, status_code=status)

    # ---- Vistas ---------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def inicio(request: Request):
        busqueda, catalogo, _ = estado(request)
        return responder(request, "inicio.html", {
            "seccion": "inicio", "busqueda": busqueda,
            "entrevistas": catalogo.todas(), "temas": catalogo.temas,
        })

    @app.get("/catalogo", response_class=HTMLResponse)
    async def pagina_catalogo(request: Request, persona: str = "", tema: str = "", lugar: str = ""):
        busqueda, catalogo, _ = estado(request)
        todas = catalogo.todas()
        filtradas = catalogo.filtrar(persona, tema, lugar)
        filas = []
        for e in filtradas:
            filas.append({
                "e": e,
                "n_cortes": len(busqueda.de_entrevista(e.interview_id)),
                "video": catalogo.estado_video(e.interview_id),
            })
        return responder(request, "catalogo.html", {
            "seccion": "catalogo", "filas": filas, "total": len(todas),
            "persona": persona, "tema": tema, "lugar": lugar,
            "temas": catalogo.temas, "lugares": catalogo.lugares,
            "hay_filtros": bool(persona.strip() or tema or lugar),
        })

    @app.get("/resultados", response_class=HTMLResponse)
    async def pagina_resultados(request: Request, q: str = ""):
        busqueda, catalogo, plan = estado(request)
        q = q.strip()
        sin_video = [
            e for e in catalogo.todas()
            if busqueda.de_entrevista(e.interview_id)
            and not catalogo.estado_video(e.interview_id).disponible
        ]
        datos_plan = _plan_para_navegador(plan, busqueda, catalogo)
        cortes_por_id = {c.clip_id: c for c in busqueda.cortes}
        tabla = [
            {**t, "cortes": [cortes_por_id[c] for c in t["clip_ids"]]}
            for t in datos_plan["tramos"]
        ]
        volver = url_resultados(q)
        return responder(request, "resultados.html", {
            "seccion": "resultados", "busqueda": busqueda, "q": q,
            "volver": volver, "plan": datos_plan, "tabla_plan": tabla,
            "sin_video": sin_video,
            "n_reproducibles": sum(1 for t in datos_plan["tramos"] if t["url_video"]),
            "omitidos": [cortes_por_id[c] for c in plan.omitidos],
        })

    @app.get("/entrevista/{interview_id}", response_class=HTMLResponse)
    async def pagina_entrevista(
        request: Request, interview_id: str,
        clip: str | None = None, t: str | None = None,
        fin: str | None = None, volver: str | None = None,
    ):
        busqueda, catalogo, _ = estado(request)
        entrevista = catalogo.obtener(interview_id)
        if entrevista is None:
            raise StarletteHTTPException(404, f"No existe la entrevista «{interview_id}».")

        avisos: list[str] = []
        seleccion: dict[str, Any] | None = None
        if clip:
            corte = busqueda.por_id(clip)
            if corte is None:
                avisos.append(f"El corte «{clip}» no está en la búsqueda cargada; se abre la entrevista completa.")
            elif corte.interview_id != interview_id:
                avisos.append("Ese corte pertenece a otra entrevista; se abre la entrevista completa.")
            else:
                seleccion = {"clip_id": corte.clip_id, "titulo": corte.titulo,
                             "inicio": corte.inicio, "fin": corte.fin}
        elif (inicio := _segundos(t)) is not None:
            tope = _segundos(fin)
            seleccion = {"clip_id": None, "titulo": "Selección", "inicio": inicio,
                         "fin": tope if tope is not None and tope > inicio else None}

        cortes = busqueda.de_entrevista(interview_id)
        video = catalogo.estado_video(interview_id)
        volver_url = volver_seguro(volver, url_resultados())
        return responder(request, "entrevista.html", {
            "seccion": "catalogo", "e": entrevista, "video": video,
            "url_video": f"/videos/{interview_id}" if video.disponible else None,
            "seleccion": seleccion, "avisos": avisos, "cortes": cortes,
            "volver": volver_url,
            "volver_etiqueta": "Volver a resultados" if volver_url.startswith("/resultados")
                               else "Volver al catálogo" if volver_url == "/catalogo" else "Volver al inicio",
            **_capitulos_y_texto(entrevista, cortes),
        })

    # ---- Vídeos ---------------------------------------------------------

    @app.api_route("/videos/{interview_id}", methods=["GET", "HEAD"])
    async def servir_video(request: Request, interview_id: str):
        _, catalogo, _ = estado(request)
        if catalogo.obtener(interview_id) is None:
            return JSONResponse({"detail": f"No existe la entrevista «{interview_id}»."}, status_code=404)
        video = catalogo.estado_video(interview_id)
        if not video.disponible:
            return JSONResponse(
                {"detail": f"Falta el vídeo de «{interview_id}»: se espera «{video.esperado}» ({video.motivo})."},
                status_code=404,
            )
        tipo = mimetypes.guess_type(video.ruta.name)[0] or "video/mp4"
        # FileResponse atiende «Range» (206) y envía «Accept-Ranges: bytes»,
        # que es lo que permite saltar a cualquier segundo sin descargar todo.
        return FileResponse(video.ruta, media_type=tipo)

    # ---- API ------------------------------------------------------------

    @app.get("/api/busqueda")
    async def api_busqueda(request: Request):
        busqueda, _, _ = estado(request)
        return {
            "es_demo": busqueda.es_demo,
            "consulta": busqueda.consulta or None,
            "fecha": busqueda.fecha or None,
            "archivo": busqueda.archivo,
            "n_cortes": len(busqueda.cortes),
            "avisos": list(busqueda.avisos),
            "cortes": [
                {
                    "clip_id": c.clip_id, "record_id": c.record_id, "tipo": c.tipo,
                    "interview_id": c.interview_id, "persona": c.persona,
                    "titulo": c.titulo, "inicio": c.inicio, "fin": c.fin,
                    "similitud": c.similitud, "texto": c.texto,
                    "url_entrevista": url_entrevista(c.interview_id, c.clip_id),
                    "url_video": f"/videos/{c.interview_id}",
                }
                for c in busqueda.cortes
            ],
        }

    @app.get("/api/plan")
    async def api_plan(request: Request):
        busqueda, catalogo, plan = estado(request)
        return _plan_para_navegador(plan, busqueda, catalogo)

    @app.get("/api/entrevistas")
    async def api_entrevistas(request: Request):
        busqueda, catalogo, _ = estado(request)
        return [
            {
                "interview_id": e.interview_id, "persona": e.persona,
                "titulo": e.titulo, "lugar": e.lugar, "temas": list(e.temas),
                "n_cortes": len(busqueda.de_entrevista(e.interview_id)),
                "tiene_capitulos": bool(e.capitulos),
                "tiene_transcripcion": bool(e.transcripcion),
                "video_disponible": catalogo.estado_video(e.interview_id).disponible,
            }
            for e in catalogo.todas()
        ]

    @app.get("/api/videos")
    async def api_videos(request: Request):
        """Diagnóstico: qué vídeos se encuentran y cuáles faltan."""
        _, catalogo, _ = estado(request)
        salida = []
        for e in catalogo.todas():
            v = catalogo.estado_video(e.interview_id)
            tam = None
            if v.ruta is not None:
                try:
                    tam = v.ruta.stat().st_size
                except OSError:
                    tam = None
            salida.append({
                "interview_id": e.interview_id, "disponible": v.disponible,
                "archivo": v.ruta.name if v.ruta else v.esperado,
                "motivo": v.motivo or None, "bytes": tam,
            })
        return salida

    # ---- Errores --------------------------------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def error_http(request: Request, exc: StarletteHTTPException):
        ruta = request.url.path
        if ruta.startswith(("/api", "/videos", "/static")):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
        mensaje = exc.detail if exc.status_code != 404 or "entrevista" in str(exc.detail) \
            else "No existe esta página."
        return responder(request, "error.html", {
            "seccion": "", "codigo": exc.status_code, "mensaje": mensaje,
        }, status=exc.status_code)

    return app


app = crear_app()
