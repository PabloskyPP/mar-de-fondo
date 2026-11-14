/*
 * Mar de Fondo · reproductor
 *
 * Dos usos, según la página:
 *   - Entrevista (#reproductor): reproduce un corte [inicio, fin] y se detiene
 *     en «fin». Desde ahí se puede seguir con la entrevista completa.
 *   - Resultados (#recopilacion): encadena los tramos del plan calculado por el
 *     servidor, también al cambiar de entrevista, con un único <video>.
 *
 * Sin dependencias. Todo el texto dinámico se inserta con textContent.
 */
(function (global) {
  "use strict";

  var EPS_FIN = 0.04; // margen (s) al detectar el final de un tramo
  var INTERVALO_MS = 40; // frecuencia de la comprobación del final
  var TOLERANCIA_SALTO = 1.5; // diferencia máxima (s) tras saltar para darlo por bueno
  var ESPERA_SALTO_MS = 8000;
  var TOLERANCIA_DEPURACION = 0.25; // s: diferencia aceptable entre salto pedido y real
  var TOLERANCIA_DURACION = 2; // s: diferencia aceptable entre vídeo y transcripción

  // ------------------------------------------------------------------ utilidades

  function formatear(seg) {
    if (!isFinite(seg) || seg < 0) return "--:--";
    var total = Math.floor(seg);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    var mm = (m < 10 ? "0" : "") + m;
    var ss = (s < 10 ? "0" : "") + s;
    return h ? h + ":" + mm + ":" + ss : mm + ":" + ss;
  }

  function numero(valor) {
    if (valor === undefined || valor === null || valor === "") return null;
    var n = Number(valor);
    return isFinite(n) ? n : null;
  }

  function puedeSaltar(video, t) {
    var s = video.seekable;
    return !!s && s.length > 0 && s.end(s.length - 1) >= t - 0.5;
  }

  function reproducir(video) {
    var p = video.play();
    return p && typeof p.then === "function" ? p : Promise.resolve();
  }

  function textoErrorVideo(video) {
    var codigo = video.error && video.error.code;
    return (
      "No se puede reproducir el vídeo" +
      (codigo ? " (código " + codigo + ")" : "") +
      ". Puede que el servidor no lo encuentre o que el navegador no admita el formato."
    );
  }

  /**
   * Carga `url` si no es ya la fuente, espera los metadatos y salta a `inicio`.
   * Resuelve {saltoOk, sinRangos, fueraDeRango, objetivo, real}: `objetivo` es el instante
   * pedido y `real` la posición del vídeo al terminar el salto. Rechaza si el vídeo no carga.
   * `alSaltar(t)` avisa de un salto hecho por el programa, no por la persona.
   */
  function prepararVideo(video, url, inicio, alSaltar) {
    return new Promise(function (resolve, reject) {
      var terminado = false;
      video.pause();

      function fin(resultado) {
        if (terminado) return;
        terminado = true;
        video.removeEventListener("loadedmetadata", alMeta);
        video.removeEventListener("error", alError);
        resolve(resultado);
      }

      function alError() {
        if (terminado) return;
        terminado = true;
        video.removeEventListener("loadedmetadata", alMeta);
        video.removeEventListener("error", alError);
        reject(video.error || new Error("error de carga"));
      }

      function saltar() {
        var duracion = video.duration;
        var tope = isFinite(duracion) ? Math.max(duracion - 0.1, 0) : inicio;
        var objetivo = Math.min(inicio, tope);
        var fueraDeRango = objetivo < inicio;
        var sinRangos = !puedeSaltar(video, objetivo);

        if (Math.abs(video.currentTime - objetivo) < 0.05) {
          fin({ saltoOk: true, sinRangos: sinRangos, fueraDeRango: fueraDeRango,
                objetivo: objetivo, real: video.currentTime });
          return;
        }
        var temporizador;
        function alTerminar() {
          clearTimeout(temporizador);
          video.removeEventListener("seeked", alTerminar);
          fin({
            saltoOk: Math.abs(video.currentTime - objetivo) <= TOLERANCIA_SALTO,
            sinRangos: sinRangos,
            fueraDeRango: fueraDeRango,
            objetivo: objetivo,
            real: video.currentTime,
          });
        }
        temporizador = setTimeout(function () {
          video.removeEventListener("seeked", alTerminar);
          fin({ saltoOk: false, sinRangos: sinRangos, fueraDeRango: fueraDeRango,
                objetivo: objetivo, real: video.currentTime });
        }, ESPERA_SALTO_MS);
        video.addEventListener("seeked", alTerminar);
        if (alSaltar) alSaltar(objetivo);
        video.currentTime = objetivo;
      }

      function alMeta() {
        video.removeEventListener("loadedmetadata", alMeta);
        saltar();
      }

      if (video.getAttribute("src") !== url) {
        video.setAttribute("src", url);
        video.load();
      }
      video.addEventListener("error", alError);
      if (video.readyState >= 1) saltar();
      else video.addEventListener("loadedmetadata", alMeta);
    });
  }

  /** Avisa cuando la reproducción llega a `fin`; con precisión de ~40 ms. */
  function crearVigilante(video, alFinal) {
    var limite = null;
    var temporizador = null;

    function comprobar() {
      if (limite === null) return;
      if (video.currentTime >= limite - EPS_FIN || video.ended) {
        var alcanzado = limite;
        desarmar();
        video.pause();
        alFinal(alcanzado);
      }
    }
    function armar(fin) {
      limite = fin;
      if (temporizador === null) temporizador = setInterval(comprobar, INTERVALO_MS);
    }
    function desarmar() {
      limite = null;
      if (temporizador !== null) {
        clearInterval(temporizador);
        temporizador = null;
      }
    }
    video.addEventListener("timeupdate", comprobar);
    video.addEventListener("ended", comprobar);
    return { armar: armar, desarmar: desarmar, activo: function () { return limite !== null; } };
  }

  // ------------------------------------------------------------------ Entrevista

  function iniciarEntrevista(raiz, doc) {
    var video = raiz.querySelector("video");
    var url = raiz.getAttribute("data-url-video");
    if (!video || !url) return null;

    function $(id) { return doc.getElementById(id); }
    var ui = {
      etiqueta: $("modo-etiqueta"),
      texto: $("modo-texto"),
      completa: $("btn-completa"),
      repetir: $("btn-repetir"),
      volver: $("btn-volver-corte"),
      mensaje: $("mensaje"),
      duracion: $("aviso-duracion"),
      depuracion: $("depuracion"),
    };
    var depurar = false;
    try { depurar = new URL(global.location.href).searchParams.get("depurar") === "1"; } catch (e) { /* sin URL */ }
    if (depurar && ui.depuracion) ui.depuracion.hidden = false;
    var ultimaPeticion = null; // qué ha pedido la persona: {etiqueta, t}
    var duracionEsperada = numero(raiz.getAttribute("data-duracion"));

    var inicioSel = numero(raiz.getAttribute("data-inicio"));
    var finSel = numero(raiz.getAttribute("data-fin"));
    var corte =
      inicioSel !== null && finSel !== null && finSel > inicioSel
        ? { inicio: inicioSel, fin: finSel, clipId: raiz.getAttribute("data-clip") || null,
            titulo: raiz.getAttribute("data-titulo") || "Corte" }
        : null;
    var modo = "completa"; // "corte" | "fin-corte" | "completa"
    var saltoInterno = null;
    var generacion = 0;

    var vigilante = crearVigilante(video, function () {
      modo = "fin-corte";
      pintarModo();
    });

    function mensaje(texto) { ui.mensaje.textContent = texto || ""; }

    function mostrar(el, si) { if (el) el.hidden = !si; }

    function pintarModo() {
      mostrar(ui.completa, modo === "corte" || modo === "fin-corte");
      mostrar(ui.repetir, modo === "fin-corte");
      mostrar(ui.volver, modo === "completa" && !!corte);
      if (modo === "corte") {
        ui.etiqueta.textContent = "Corte";
        ui.texto.textContent = "«" + corte.titulo + "», de " + formatear(corte.inicio) + " a " +
          formatear(corte.fin) + ". La reproducción se detiene al final del corte.";
      } else if (modo === "fin-corte") {
        ui.etiqueta.textContent = "Corte terminado";
        ui.texto.textContent = "El corte ha terminado en " + formatear(corte.fin) +
          ". Puedes seguir con la entrevista completa o repetir el corte.";
      } else {
        ui.etiqueta.textContent = "Entrevista completa";
        ui.texto.textContent = corte
          ? "Sin límite de final. El corte «" + corte.titulo + "» era de " + formatear(corte.inicio) +
            " a " + formatear(corte.fin) + "."
          : "Sin límite de final.";
      }
    }

    function depuracion(r) {
      if (!depurar || !ui.depuracion || !r || r.real === undefined) return;
      var dif = Math.abs(r.real - r.objetivo);
      var peticion = ultimaPeticion ? ultimaPeticion.etiqueta + " · " : "";
      ui.depuracion.textContent = "Depuración · " + peticion + "pedido " + r.objetivo.toFixed(2) + " s (" +
        formatear(r.objetivo) + ") · posición real " + r.real.toFixed(2) + " s · diferencia " +
        dif.toFixed(2) + " s " + (dif <= TOLERANCIA_DEPURACION ? "✓" : "✗ (el salto no ha caído en el instante pedido)");
    }

    function comprobarDuracion() {
      if (duracionEsperada === null || !ui.duracion || !isFinite(video.duration)) return;
      var dif = Math.abs(video.duration - duracionEsperada);
      if (dif > TOLERANCIA_DURACION) {
        ui.duracion.hidden = false;
        ui.duracion.textContent = "Atención: el vídeo dura " + formatear(video.duration) +
          " y la transcripción corresponde a un audio de " + formatear(duracionEsperada) +
          ". Si no son el mismo material, los capítulos y la transcripción pueden no caer en el instante correcto.";
      } else {
        ui.duracion.hidden = true;
      }
    }

    function avisoSalto(r) {
      depuracion(r);
      if (r.fueraDeRango) mensaje("El corte empieza después del final del vídeo; se ha ido al último instante disponible.");
      else if (!r.saltoOk || r.sinRangos)
        mensaje("Este navegador no ha podido saltar a ese segundo. El servidor de vídeo debe admitir peticiones por rangos.");
      else mensaje("");
    }

    function marcarSalto(t) { saltoInterno = t; }

    function intentarReproducir() {
      return reproducir(video).catch(function (err) {
        if (err && err.name === "NotAllowedError") {
          mensaje("El navegador ha bloqueado el inicio automático. Pulsa reproducir para empezar.");
        } else if (err && err.name !== "AbortError") {
          mensaje(textoErrorVideo(video));
        }
      });
    }

    function empezarCorte(conReproduccion) {
      var mia = ++generacion;
      vigilante.desarmar();
      modo = "corte";
      pintarModo();
      return prepararVideo(video, url, corte.inicio, marcarSalto).then(function (r) {
        if (mia !== generacion) return;
        avisoSalto(r);
        vigilante.armar(corte.fin);
        if (conReproduccion) return intentarReproducir();
      }, function () {
        if (mia === generacion) mensaje(textoErrorVideo(video));
      });
    }

    function pasarACompleta(conReproduccion) {
      generacion++;
      vigilante.desarmar();
      modo = "completa";
      pintarModo();
      mensaje("");
      if (conReproduccion && video.paused) return intentarReproducir();
    }

    function irA(t) {
      if (corte && modo !== "completa" && t >= corte.inicio - 0.05 && t < corte.fin) {
        // Dentro del corte: se mantiene el límite de final.
        generacion++;
        saltoInterno = t;
        var alTerminarSalto = function () {
          video.removeEventListener("seeked", alTerminarSalto);
          depuracion({ objetivo: t, real: video.currentTime });
        };
        video.addEventListener("seeked", alTerminarSalto);
        video.currentTime = t;
        if (modo === "fin-corte") { modo = "corte"; pintarModo(); }
        vigilante.armar(corte.fin);
        return intentarReproducir();
      }
      pasarACompleta(false);
      return prepararVideo(video, url, t, marcarSalto).then(function (r) {
        avisoSalto(r);
        return intentarReproducir();
      }, function () { mensaje(textoErrorVideo(video)); });
    }

    function actualizarUrl(clipId) {
      try {
        var u = new URL(global.location.href);
        u.searchParams.set("clip", clipId);
        u.searchParams.delete("t");
        u.searchParams.delete("fin");
        global.history.replaceState(null, "", u.pathname + u.search + u.hash);
      } catch (e) { /* sin historial: no pasa nada */ }
    }

    // --- Acciones de los botones y de los elementos con tiempo

    function activar(el) {
      var inicio = numero(el.getAttribute("data-inicio"));
      var fin = numero(el.getAttribute("data-fin"));
      var clip = el.getAttribute("data-clip");
      if (inicio === null) return;
      var rotulo = el.querySelector(".titulo") || el.querySelector(".turno-texto");
      ultimaPeticion = { etiqueta: (rotulo ? rotulo.textContent : "").trim().slice(0, 48), t: inicio };
      if (clip && fin !== null && fin > inicio) {
        var t = el.querySelector(".titulo") || el.querySelector(".turno-hablante");
        corte = { inicio: inicio, fin: fin, clipId: clip, titulo: t ? t.textContent : "Corte" };
        actualizarUrl(clip);
        empezarCorte(true);
      } else {
        irA(inicio);
      }
    }

    if (ui.completa) ui.completa.addEventListener("click", function () {
      var estabaAlFinal = modo === "fin-corte";
      pasarACompleta(false);
      if (estabaAlFinal || video.paused) intentarReproducir();
    });
    if (ui.repetir) ui.repetir.addEventListener("click", function () { if (corte) empezarCorte(true); });
    if (ui.volver) ui.volver.addEventListener("click", function () { if (corte) empezarCorte(true); });

    doc.addEventListener("click", function (ev) {
      var el = ev.target.closest && ev.target.closest(".item-tiempo, .turno");
      if (!el || (el.disabled)) return;
      var sel = global.getSelection ? String(global.getSelection()) : "";
      if (el.classList.contains("turno") && sel) return; // la persona está seleccionando texto
      activar(el);
    });

    // --- Reacción a lo que hace la persona con los controles nativos

    video.addEventListener("play", function () {
      // Pulsar «play» al terminar el corte equivale a seguir con la entrevista completa.
      if (modo === "fin-corte") pasarACompleta(false);
    });

    video.addEventListener("seeking", function () {
      if (saltoInterno !== null && Math.abs(video.currentTime - saltoInterno) < 0.3) return;
      if (modo === "corte" && corte) {
        var t = video.currentTime;
        if (t < corte.inicio - 0.5 || t > corte.fin) {
          pasarACompleta(false);
          mensaje("Has salido del corte: ahora se reproduce la entrevista completa.");
        }
      }
    });
    video.addEventListener("seeked", function () { saltoInterno = null; });

    video.addEventListener("error", function () { mensaje(textoErrorVideo(video)); });
    video.addEventListener("loadedmetadata", comprobarDuracion);
    if (video.readyState >= 1) comprobarDuracion();

    // --- Elemento activo en capítulos y transcripción

    function recoger(selector) {
      return Array.prototype.map.call(doc.querySelectorAll(selector), function (el) {
        return { el: el, inicio: numero(el.getAttribute("data-inicio")),
                 fin: numero(el.getAttribute("data-fin")) };
      }).filter(function (x) { return x.inicio !== null; });
    }
    var capitulos = recoger("#lista-capitulos .item-tiempo");
    var turnos = recoger("#transcripcion .turno");
    var listaTexto = doc.getElementById("transcripcion");

    function activo(items, t) {
      var elegido = null;
      for (var i = 0; i < items.length; i++) if (items[i].inicio <= t + 0.01) elegido = items[i];
      if (elegido && elegido.fin !== null && t >= elegido.fin + 1) return null;
      return elegido;
    }
    function marcar(items, elegido, desplazar) {
      var cambio = false;
      items.forEach(function (x) {
        var es = x === elegido;
        if (es && !x.el.classList.contains("activo")) cambio = true;
        x.el.classList.toggle("activo", es);
        if (es) x.el.setAttribute("aria-current", "true"); else x.el.removeAttribute("aria-current");
      });
      if (cambio && desplazar && listaTexto && elegido && !video.paused) {
        listaTexto.scrollTop = Math.max(0, elegido.el.offsetTop - listaTexto.clientHeight / 3);
      }
    }
    video.addEventListener("timeupdate", function () {
      var t = video.currentTime;
      marcar(capitulos, activo(capitulos, t), false);
      marcar(turnos, activo(turnos, t), true);
    });

    // --- Arranque

    pintarModo();
    if (corte) empezarCorte(true);
    else if (inicioSel !== null) {
      pasarACompleta(false);
      prepararVideo(video, url, inicioSel, marcarSalto).then(function (r) {
        avisoSalto(r);
        return intentarReproducir();
      }, function () { mensaje(textoErrorVideo(video)); });
    }

    return { empezarCorte: empezarCorte, irA: irA, estado: function () { return { modo: modo, corte: corte }; } };
  }

  // ------------------------------------------------------------------ Resultados

  function iniciarLista(doc) {
    var boton = doc.getElementById("btn-reproducir");
    var panel = doc.getElementById("recopilacion");
    var datosEl = doc.getElementById("plan-datos");
    if (!boton || !panel || !datosEl) return null;

    var tramos = JSON.parse(datosEl.textContent).tramos;
    var video = doc.getElementById("video-lista");
    var ui = {
      posicion: doc.getElementById("lista-posicion"),
      titulo: doc.getElementById("lista-titulo"),
      persona: doc.getElementById("lista-persona"),
      intervalo: doc.getElementById("lista-intervalo"),
      anterior: doc.getElementById("lista-anterior"),
      siguiente: doc.getElementById("lista-siguiente"),
      cerrar: doc.getElementById("lista-cerrar"),
      abrir: doc.getElementById("lista-abrir"),
      mensaje: doc.getElementById("lista-mensaje"),
    };

    var indice = -1;
    var generacion = 0;
    var saltoInterno = null;
    var vigilante = crearVigilante(video, function () { ir(indice + 1, 1); });

    function mensaje(texto) { ui.mensaje.textContent = texto || ""; }

    function limpiarActivos() {
      Array.prototype.forEach.call(doc.querySelectorAll(".corte.activo"), function (li) {
        li.classList.remove("activo");
        var tag = li.querySelector(".etiqueta-activo");
        if (tag) tag.hidden = true;
      });
    }
    function marcarActivos(tramo) {
      limpiarActivos();
      tramo.anclas.forEach(function (id) {
        var li = doc.getElementById(id);
        if (!li) return;
        li.classList.add("activo");
        var tag = li.querySelector(".etiqueta-activo");
        if (tag) tag.hidden = false;
      });
    }

    function pintarTramo(t) {
      ui.posicion.textContent = "Tramo " + (t.i + 1) + " de " + tramos.length;
      ui.titulo.textContent = t.titulo;
      ui.persona.textContent = t.persona;
      ui.intervalo.textContent = formatear(t.inicio) + "–" + formatear(t.fin) +
        (t.mas_cortes > 0 ? " · une " + (t.mas_cortes + 1) + " cortes solapados" : "");
      var volver = global.location.pathname + global.location.search + "#" + t.anclas[0];
      ui.abrir.setAttribute("href", t.url_entrevista + "&volver=" + encodeURIComponent(volver));
      marcarActivos(t);
    }

    function terminar() {
      vigilante.desarmar();
      video.pause();
      limpiarActivos();
      ui.posicion.textContent = "Fin de la recopilación";
      mensaje("Se han reproducido todos los tramos. Pulsa «Reproducir fragmentos» para empezar de nuevo.");
    }

    function ir(i, sentido) {
      var mia = ++generacion;
      vigilante.desarmar();
      var omitidas = [];
      var k = i;
      while (k >= 0 && k < tramos.length && !tramos[k].url_video) {
        omitidas.push(tramos[k].persona);
        k += sentido;
      }
      if (k >= tramos.length) { terminar(); return Promise.resolve(); }
      if (k < 0) { mensaje("No hay un corte anterior con vídeo."); return Promise.resolve(); }

      indice = k;
      var t = tramos[k];
      pintarTramo(t);
      mensaje(omitidas.length
        ? "Se omiten tramos sin vídeo disponible (" + omitidas.join(", ") + ")."
        : "");

      return prepararVideo(video, t.url_video, t.inicio, function (x) { saltoInterno = x; })
        .then(function (r) {
          if (mia !== generacion) return;
          if (!r.saltoOk || r.sinRangos) {
            mensaje("Este navegador no ha podido saltar a " + formatear(t.inicio) +
              ". El servidor de vídeo debe admitir peticiones por rangos.");
          }
          vigilante.armar(t.fin);
          return reproducir(video);
        })
        .catch(function (err) {
          if (mia !== generacion) return;
          if (err && err.name === "NotAllowedError") {
            mensaje("El navegador ha bloqueado la reproducción. Pulsa reproducir en el vídeo.");
          } else if (err && err.name === "AbortError") {
            return;
          } else {
            mensaje("No se pudo cargar el vídeo de " + t.persona + ". Se pasa al siguiente corte.");
            setTimeout(function () { if (mia === generacion) ir(k + 1, 1); }, 1500);
          }
        });
    }

    function abrir() {
      panel.hidden = false;
      if (panel.scrollIntoView) panel.scrollIntoView({ block: "nearest" });
      return ir(0, 1);
    }
    function cerrar() {
      generacion++;
      vigilante.desarmar();
      video.pause();
      video.removeAttribute("src");
      video.load();
      limpiarActivos();
      panel.hidden = true;
      mensaje("");
      boton.focus();
    }

    boton.addEventListener("click", abrir);
    ui.anterior.addEventListener("click", function () { ir(Math.max(indice - 1, 0), -1); });
    ui.siguiente.addEventListener("click", function () { ir(indice + 1, 1); });
    ui.cerrar.addEventListener("click", cerrar);

    // Si la persona salta fuera del tramo con los controles nativos, no se corta
    // la reproducción: se deja de vigilar el final y se avisa.
    video.addEventListener("seeking", function () {
      if (!vigilante.activo() || indice < 0) return;
      if (saltoInterno !== null && Math.abs(video.currentTime - saltoInterno) < 0.3) return;
      var t = tramos[indice];
      if (video.currentTime < t.inicio - 0.5 || video.currentTime > t.fin) {
        vigilante.desarmar();
        mensaje("Has salido del tramo: la reproducción sigue sin límite. Usa «Corte siguiente» para volver a la recopilación.");
      }
    });
    video.addEventListener("seeked", function () { saltoInterno = null; });
    video.addEventListener("error", function () {
      if (indice >= 0 && video.getAttribute("src")) mensaje(textoErrorVideo(video));
    });

    return { ir: ir, abrir: abrir, cerrar: cerrar, estado: function () { return { indice: indice }; } };
  }

  // ------------------------------------------------------------------ arranque

  function arrancar() {
    var doc = global.document;
    var entrevista = doc.getElementById("reproductor");
    if (entrevista) iniciarEntrevista(entrevista, doc);
    if (doc.getElementById("recopilacion")) iniciarLista(doc);
  }

  var api = {
    formatear: formatear, prepararVideo: prepararVideo, crearVigilante: crearVigilante,
    iniciarEntrevista: iniciarEntrevista, iniciarLista: iniciarLista,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.MarDeFondo = api;

  if (global.document) {
    if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", arrancar);
    else arrancar();
  }
})(typeof window !== "undefined" ? window : globalThis);
