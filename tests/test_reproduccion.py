from __future__ import annotations

from dataclasses import dataclass

from app.config import Ajustes
from app.reproduccion import MINIMO_TRAMO, construir_plan
from app.resultados import cargar_busqueda


@dataclass
class C:
    clip_id: str
    interview_id: str
    inicio: float
    fin: float


def solapan(a, b) -> bool:
    return a.inicio < b.fin and b.inicio < a.fin


def test_sin_solapes_en_la_lista_real():
    cortes = cargar_busqueda(Ajustes(_env_file=None)).cortes
    plan = construir_plan(cortes)
    assert len(cortes) == 32
    for i, a in enumerate(plan.tramos):
        for b in plan.tramos[i + 1:]:
            if a.interview_id == b.interview_id:
                assert not solapan(a, b), (a, b)


def test_la_lista_real_no_pierde_tiempo_unico():
    """Todo instante de un corte original sigue cubierto por algún tramo."""
    cortes = cargar_busqueda(Ajustes(_env_file=None)).cortes
    plan = construir_plan(cortes)
    for c in cortes:
        propios = [t for t in plan.tramos if t.interview_id == c.interview_id]
        paso = 0.05
        t = c.inicio
        while t < c.fin:
            assert any(x.inicio - 1e-6 <= t <= x.fin + 1e-6 for x in propios), (c.clip_id, t)
            t += paso


def test_la_lista_real_se_acorta():
    plan = construir_plan(cargar_busqueda(Ajustes(_env_file=None)).cortes)
    assert plan.duracion_total < plan.duracion_cortes
    assert plan.repetido_evitado > 180
    assert len(plan.tramos) == 30


def test_consecutivos_solapados_se_unen_cronologicamente():
    plan = construir_plan([C("a", "X", 100, 130), C("b", "X", 100, 190)])
    assert [(t.inicio, t.fin) for t in plan.tramos] == [(100, 190)]
    assert plan.tramos[0].clip_ids == ("a", "b")


def test_union_hacia_atras_no_salta_atras():
    plan = construir_plan([C("a", "X", 200, 300), C("b", "X", 150, 250)])
    assert [(t.inicio, t.fin) for t in plan.tramos] == [(150, 300)]


def test_no_consecutivos_se_recortan_y_conservan_el_orden():
    plan = construir_plan([C("a", "X", 100, 130), C("z", "Y", 0, 10), C("b", "X", 100, 190)])
    assert [(t.interview_id, t.inicio, t.fin) for t in plan.tramos] == [
        ("X", 100, 130), ("Y", 0, 10), ("X", 130, 190)]


def test_lo_ya_visto_en_medio_parte_el_tramo_en_dos():
    plan = construir_plan([C("a", "X", 50, 60), C("z", "Y", 0, 5), C("b", "X", 40, 80)])
    assert [(t.interview_id, t.inicio, t.fin) for t in plan.tramos] == [
        ("X", 50, 60), ("Y", 0, 5), ("X", 40, 50), ("X", 60, 80)]


def test_corte_ya_cubierto_se_omite():
    plan = construir_plan([C("a", "X", 0, 100), C("z", "Y", 0, 5), C("b", "X", 20, 30)])
    assert plan.omitidos == ("b",)
    assert len(plan.tramos) == 2


def test_mismo_intervalo_en_otro_video_no_se_recorta():
    plan = construir_plan([C("a", "X", 0, 10), C("b", "Y", 0, 10)])
    assert len(plan.tramos) == 2 and plan.repetido_evitado == 0


def test_restos_minusculos_se_descartan():
    plan = construir_plan([C("a", "X", 0, 10), C("z", "Y", 0, 5),
                           C("b", "X", 0, 10 + MINIMO_TRAMO / 2)])
    assert plan.omitidos == ("b",)


def test_lista_vacia():
    plan = construir_plan([])
    assert plan.tramos == () and plan.duracion_total == 0
