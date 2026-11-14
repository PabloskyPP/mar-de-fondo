from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from tests.conftest import TAMANO_VIDEO, _contenido


def test_paginas_principales(cliente):
    for url in ("/", "/catalogo", "/resultados", "/entrevista/Carmen_Vileda_Rey"):
        r = cliente.get(url)
        assert r.status_code == 200, url
        assert "Mar de Fondo" in r.text


def test_la_demostracion_es_visible(cliente):
    for url in ("/resultados", "/resultados?q=emigraci%C3%B3n"):
        html = cliente.get(url).text
        assert 'class="aviso-demo"' in html and "Demostración" in html
        assert "32 cortes de 6 entrevistas" in html
    assert "Has escrito «emigración»" in cliente.get("/resultados?q=emigraci%C3%B3n").text
    assert "búsqueda guardada de demostración" in cliente.get("/").text


def test_el_texto_de_la_consulta_se_muestra_si_se_configura(carpeta_videos):
    from tests.conftest import hacer_cliente
    with hacer_cliente(videos_dir=carpeta_videos, demo_consulta="palilladas",
                       demo_fecha="2026-10-22T19:01:50Z") as c:
        html = c.get("/resultados").text
    assert "«palilladas»" in html and "22/10/2026 19:01 UTC" in html


def test_resultados_muestra_los_32_cortes_con_sus_datos(cliente):
    html = cliente.get("/resultados").text
    assert html.count('<li class="corte"') == 32
    assert "Palilladas con luz de carburo" in html
    assert "01:40–02:09" in html and "Carmen Vileda Rey" in html
    assert "Sí, eh, entón si" in html


def test_cada_corte_abre_la_entrevista_en_su_segundo_y_vuelve(cliente):
    html = cliente.get("/resultados?q=prueba").text
    enlaces = re.findall(r'<a class="boton" href="([^"]+)">Ver corte', html)
    assert len(enlaces) == 32
    api = {c["clip_id"]: c for c in cliente.get("/api/busqueda").json()["cortes"]}
    for href in enlaces:
        href = unescape(href)
        partes = urlsplit(href)
        q = parse_qs(partes.query)
        clip = api[q["clip"][0]]
        pagina = cliente.get(href)
        assert pagina.status_code == 200
        assert f'data-inicio="{clip["inicio"]}"' in pagina.text
        assert f'data-fin="{clip["fin"]}"' in pagina.text
        # el enlace de vuelta conserva la consulta y el ancla del corte
        volver = unescape(re.search(r'<a href="([^"]+)">‹ Volver a resultados', pagina.text).group(1))
        assert volver == f"/resultados?q=prueba#corte-{clip['clip_id']}"
        assert f'id="corte-{clip["clip_id"]}"' in html


def test_volver_no_admite_redirecciones_externas(cliente):
    for malo in ("//evil.com", "https://evil.com", "/otra", "javascript:alert(1)", "/\\evil"):
        html = cliente.get("/entrevista/Carmen_Vileda_Rey", params={"volver": malo}).text
        assert '<a href="/resultados">‹ Volver a resultados' in html, malo


def test_entrevista_distingue_corte_y_completa(cliente):
    corte = cliente.get("/entrevista/Carmen_Vileda_Rey?clip=Carmen_Vileda_Rey_hist02__clip01").text
    assert 'data-modo="corte"' in corte and "se detiene al final del corte" in corte
    assert 'id="btn-completa"' in corte
    completa = cliente.get("/entrevista/Carmen_Vileda_Rey").text
    assert 'data-modo="completa"' in completa and 'data-fin=""' in completa


def test_entrevista_con_tiempo_manual_y_valores_basura(cliente):
    ok = cliente.get("/entrevista/Carmen_Vileda_Rey?t=30&fin=45").text
    assert 'data-inicio="30.0"' in ok and 'data-fin="45.0"' in ok
    for malo in ("?t=abc", "?t=-4", "?t=nan", "?t=inf"):
        r = cliente.get("/entrevista/Carmen_Vileda_Rey" + malo)
        assert r.status_code == 200 and 'data-inicio=""' in r.text
    sin_fin = cliente.get("/entrevista/Carmen_Vileda_Rey?t=30&fin=10").text
    assert 'data-fin=""' in sin_fin


def test_corte_inexistente_o_de_otra_entrevista_avisa(cliente):
    r = cliente.get("/entrevista/Carmen_Vileda_Rey?clip=nope")
    assert "no está en la búsqueda cargada" in r.text and 'data-inicio=""' in r.text
    r = cliente.get("/entrevista/Carmen_Vileda_Rey?clip=MariaJoseRoa_seg01__clip01")
    assert "pertenece a otra entrevista" in r.text


def test_entrevista_con_capitulos_pero_sin_transcripcion_usa_los_cortes_para_el_texto(cliente):
    html = cliente.get("/entrevista/MariaJoseRoa").text
    assert "En esta entrevista" in html and html.count('data-capitulo="') == 20
    assert "Cortes de esta búsqueda" in html and html.count('data-accion="corte"') == 12
    assert "no es la entrevista entera" in html
    assert len(re.findall(r'<li class="turno', html)) == 12


def test_entrevista_sin_ningun_dato_extra_usa_los_cortes_y_lo_dice(carpeta_videos, datos_copia):
    from tests.conftest import hacer_cliente
    (datos_copia / "temas_preparados.json").unlink()
    with hacer_cliente(videos_dir=carpeta_videos, datos_dir=datos_copia) as c:
        html = c.get("/entrevista/MariaJoseRoa").text
    assert "Cortes de la búsqueda" in html and "aún no tiene capítulos propios" in html
    assert "no es la entrevista entera" in html


def test_entrevista_desconocida_404(cliente):
    r = cliente.get("/entrevista/NoExiste")
    assert r.status_code == 404 and "No existe la entrevista" in r.text


def test_catalogo_filtra_y_tiene_estado_vacio(cliente):
    assert "6 entrevistas" in cliente.get("/catalogo").text
    r = cliente.get("/catalogo?persona=mar%C3%ADa+jos%C3%A9")
    assert "2 de 6 entrevistas" in r.text and "Quitar filtros" in r.text
    vacio = cliente.get("/catalogo?persona=zzz").text
    assert "Ninguna entrevista coincide" in vacio


def test_plan_de_la_api_y_pagina(cliente):
    plan = cliente.get("/api/plan").json()
    assert plan["resumen"]["n_tramos"] == 30
    assert all(t["url_video"] for t in plan["tramos"])
    html = cliente.get("/resultados").text
    assert 'id="plan-datos"' in html and "Reproducir fragmentos" in html
    assert "Se evitan 3 min 49 s" in html


# ---------------------------------------------------------------- vídeos

def test_video_completo_y_cabeceras(cliente):
    r = cliente.get("/videos/Maria_Antelo_Lema")
    assert r.status_code == 200 and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4" and len(r.content) == TAMANO_VIDEO
    assert cliente.head("/videos/Maria_Antelo_Lema").status_code == 200


def test_video_admite_rangos_para_saltar(cliente, mapa):
    ids = list(mapa["interviews"])
    i = ids.index("Maria_Antelo_Lema")
    esperado = _contenido(i)
    for rango, trozo in (("bytes=200000-200999", esperado[200000:201000]),
                         ("bytes=0-99", esperado[:100]),
                         ("bytes=299000-", esperado[299000:])):
        r = cliente.get("/videos/Maria_Antelo_Lema", headers={"Range": rango})
        assert r.status_code == 206, rango
        assert r.content == trozo
        assert r.headers["content-range"].endswith(f"/{TAMANO_VIDEO}")
    r = cliente.get("/videos/Maria_Antelo_Lema", headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


def test_video_ausente_da_mensaje_claro_en_api_y_en_pantalla(cliente_sin_videos):
    r = cliente_sin_videos.get("/videos/Carmen_Vileda_Rey")
    assert r.status_code == 404
    assert "Carmen Vileda Rey.mp4" in r.json()["detail"]
    html = cliente_sin_videos.get("/entrevista/Carmen_Vileda_Rey?clip=Carmen_Vileda_Rey_hist02__clip01").text
    assert "Vídeo no disponible" in html and "Carmen Vileda Rey.mp4" in html
    assert "<video" not in html and "MDF_VIDEOS_DIR" in html
    assert 'data-clip' in html  # sigue mostrando capítulos y texto
    assert "Vídeo no disponible" in cliente_sin_videos.get("/catalogo").text
    assert "Faltan los vídeos de 6 entrevistas" in cliente_sin_videos.get("/resultados").text
    plan = cliente_sin_videos.get("/api/plan").json()
    assert all(t["url_video"] is None for t in plan["tramos"])


def test_video_de_entrevista_desconocida_404(cliente):
    assert cliente.get("/videos/../../etc/passwd").status_code == 404
    assert cliente.get("/videos/NoExiste").status_code == 404


def test_diagnostico_de_videos(cliente, cliente_sin_videos):
    assert all(v["disponible"] for v in cliente.get("/api/videos").json())
    faltan = cliente_sin_videos.get("/api/videos").json()
    assert not any(v["disponible"] for v in faltan) and faltan[0]["motivo"]
