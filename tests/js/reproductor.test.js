// Pruebas de la lógica de reproductor.js con un <video> simulado (jsdom).
// No sustituyen a una prueba con un navegador real: ver README.
//
//   cd tests/js && npm install && npm test
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const CODIGO = fs.readFileSync(
  path.join(__dirname, "..", "..", "app", "static", "js", "reproductor.js"), "utf8");

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));
async function hasta(cond, ms = 4000) {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("tiempo de espera agotado");
    await esperar(10);
  }
}

/** <video> simulado: el reloj avanza 0,1 s de vídeo cada 10 ms reales. */
function simularVideo(doc, { duracion = 2100, sinRangos = false } = {}) {
  const v = doc.createElement("video");
  let t = 0, pausado = true, listo = 0, finalizado = false;
  const historial = [];
  v.visitado = historial; // [{src, t}] por cada salto
  v.reproducciones = [];  // intervalos realmente reproducidos
  let inicioRep = null;
  const cerrarRep = () => {
    if (inicioRep !== null) { v.reproducciones.push([inicioRep.src, inicioRep.t, t]); inicioRep = null; }
  };
  Object.defineProperties(v, {
    currentTime: {
      get: () => t,
      set: (x) => {
        cerrarRep();
        t = x; historial.push({ src: v.getAttribute("src"), t: x });
        setTimeout(() => v.dispatchEvent(new doc.defaultView.Event("seeking")), 0);
        setTimeout(() => {
          if (!pausado) inicioRep = { src: v.getAttribute("src"), t };
          v.dispatchEvent(new doc.defaultView.Event("seeked"));
          v.dispatchEvent(new doc.defaultView.Event("timeupdate"));
        }, 3);
      },
    },
    paused: { get: () => pausado },
    ended: { get: () => finalizado },
    readyState: { get: () => listo },
    duration: { get: () => (listo ? duracion : NaN) },
    seekable: { get: () => ({ length: listo ? 1 : 0, end: () => (sinRangos ? 5 : duracion) }) },
  });
  v.play = () => {
    pausado = false; inicioRep = { src: v.getAttribute("src"), t };
    v.dispatchEvent(new doc.defaultView.Event("play"));
    return Promise.resolve();
  };
  v.pause = () => { cerrarRep(); pausado = true; v.dispatchEvent(new doc.defaultView.Event("pause")); };
  v.load = () => {
    cerrarRep(); listo = 0; t = 0; pausado = true;
    setTimeout(() => { listo = 1; v.dispatchEvent(new doc.defaultView.Event("loadedmetadata")); }, 5);
  };
  setInterval(() => {
    if (!pausado) { t += 0.1; v.dispatchEvent(new doc.defaultView.Event("timeupdate")); }
  }, 10);
  return v;
}

function montar(html, opciones) {
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/resultados?q=x" });
  const w = dom.window;
  w.eval(CODIGO);
  const video = simularVideo(w.document, opciones);
  const marcador = w.document.querySelector("video");
  marcador.replaceWith(video);
  video.id = marcador.id;
  if (marcador.getAttribute("src")) {
    // Un navegador real empieza a cargar solo un <video> que ya trae src.
    video.setAttribute("src", marcador.getAttribute("src"));
    video.load();
  }
  return { w, doc: w.document, video, api: w.MarDeFondo };
}

const HTML_ENTREVISTA = `
<section id="reproductor" data-url-video="/videos/A" data-inicio="100" data-fin="102"
         data-clip="c1" data-titulo="Un corte">
  <video src="/videos/A"></video>
  <span id="modo-etiqueta"></span><span id="modo-texto"></span>
  <button id="btn-completa" hidden></button><button id="btn-repetir" hidden></button>
  <button id="btn-volver-corte" hidden></button><p id="mensaje"></p>
</section>`;

test("el corte empieza en start, se detiene en end y no sigue solo", async () => {
  const { doc, video, api } = montar(HTML_ENTREVISTA);
  const ctrl = api.iniciarEntrevista(doc.getElementById("reproductor"), doc);
  await hasta(() => ctrl.estado().modo === "fin-corte");
  assert.ok(video.paused, "debe quedar en pausa");
  assert.ok(video.currentTime >= 101.95 && video.currentTime < 102.4, `t=${video.currentTime}`);
  assert.equal(video.visitado[0].t, 100, "primer salto al segundo exacto");
  assert.equal(doc.getElementById("modo-etiqueta").textContent, "Corte terminado");
  assert.equal(doc.getElementById("btn-completa").hidden, false);
  assert.equal(doc.getElementById("btn-repetir").hidden, false);
  await esperar(150);
  assert.ok(video.paused && video.currentTime < 102.4, "no avanza por su cuenta");
});

test("«Seguir viendo la entrevista completa» quita el límite y continúa", async () => {
  const { doc, video, api } = montar(HTML_ENTREVISTA);
  const ctrl = api.iniciarEntrevista(doc.getElementById("reproductor"), doc);
  await hasta(() => ctrl.estado().modo === "fin-corte");
  doc.getElementById("btn-completa").click();
  assert.equal(ctrl.estado().modo, "completa");
  await hasta(() => video.currentTime > 103);
  assert.equal(video.paused, false);
  assert.equal(doc.getElementById("modo-etiqueta").textContent, "Entrevista completa");
  assert.equal(doc.getElementById("btn-volver-corte").hidden, false);
});

test("pulsar play al terminar el corte equivale a seguir con la completa", async () => {
  const { doc, video, api } = montar(HTML_ENTREVISTA);
  const ctrl = api.iniciarEntrevista(doc.getElementById("reproductor"), doc);
  await hasta(() => ctrl.estado().modo === "fin-corte");
  video.play();
  assert.equal(ctrl.estado().modo, "completa");
  await hasta(() => video.currentTime > 103);
});

test("«Repetir el corte» vuelve a start y vuelve a parar en end", async () => {
  const { doc, video, api } = montar(HTML_ENTREVISTA);
  const ctrl = api.iniciarEntrevista(doc.getElementById("reproductor"), doc);
  await hasta(() => ctrl.estado().modo === "fin-corte");
  doc.getElementById("btn-repetir").click();
  await hasta(() => ctrl.estado().modo === "corte");
  await hasta(() => ctrl.estado().modo === "fin-corte");
  assert.ok(video.visitado.filter((x) => x.t === 100).length >= 2);
});

test("saltar con los controles fuera del corte pasa a entrevista completa", async () => {
  const { doc, video, api } = montar(HTML_ENTREVISTA);
  const ctrl = api.iniciarEntrevista(doc.getElementById("reproductor"), doc);
  await hasta(() => !video.paused && ctrl.estado().modo === "corte");
  video.currentTime = 900;
  await hasta(() => ctrl.estado().modo === "completa");
  await esperar(200);
  assert.equal(video.paused, false, "no se corta la reproducción");
  assert.ok(video.currentTime > 900);
});

test("si el navegador no puede saltar, se avisa con un mensaje claro", async () => {
  const { doc, video, api } = montar(HTML_ENTREVISTA, { sinRangos: true });
  const ctrl = api.iniciarEntrevista(doc.getElementById("reproductor"), doc);
  await hasta(() => !video.paused);
  assert.match(doc.getElementById("mensaje").textContent, /no ha podido saltar/);
  assert.ok(ctrl);
});

const PLAN = {
  tramos: [
    { i: 0, interview_id: "A", persona: "Ana", titulo: "T0", mas_cortes: 0, anclas: ["corte-a"], inicio: 10, fin: 11, url_video: "/videos/A", url_entrevista: "/entrevista/A?clip=a" },
    { i: 1, interview_id: "A", persona: "Ana", titulo: "T1", mas_cortes: 1, anclas: ["corte-b", "corte-c"], inicio: 50, fin: 51, url_video: "/videos/A", url_entrevista: "/entrevista/A?clip=b" },
    { i: 2, interview_id: "X", persona: "Sin vídeo", titulo: "T2", mas_cortes: 0, anclas: ["corte-x"], inicio: 5, fin: 6, url_video: null, url_entrevista: "/entrevista/X?clip=x" },
    { i: 3, interview_id: "B", persona: "Berta", titulo: "T3", mas_cortes: 0, anclas: ["corte-d"], inicio: 5, fin: 6, url_video: "/videos/B", url_entrevista: "/entrevista/B?clip=d" },
  ],
};
const HTML_LISTA = `
<button id="btn-reproducir"></button>
<section id="recopilacion" hidden>
  <video id="video-lista"></video>
  <p id="lista-posicion"></p><h2 id="lista-titulo"></h2><p id="lista-persona"></p><p id="lista-intervalo"></p>
  <button id="lista-anterior"></button><button id="lista-siguiente"></button><button id="lista-cerrar"></button>
  <a id="lista-abrir" href="#"></a><p id="lista-mensaje"></p>
</section>
${["a", "b", "c", "x", "d"].map((i) => `<li class="corte" id="corte-${i}"><span class="etiqueta-activo" hidden></span></li>`).join("")}
<script type="application/json" id="plan-datos">${JSON.stringify(PLAN)}</script>`;

test("Reproducir fragmentos encadena tramos, cambia de entrevista y omite los sin vídeo", async () => {
  const { doc, video, api } = montar(HTML_LISTA);
  const lista = api.iniciarLista(doc);
  doc.getElementById("btn-reproducir").click();
  assert.equal(doc.getElementById("recopilacion").hidden, false);
  await hasta(() => doc.getElementById("lista-posicion").textContent === "Fin de la recopilación", 8000);

  const tramos = video.reproducciones.filter(([, a, b]) => b - a > 0.3);
  const resumen = tramos.map(([src, a, b]) => [src, Math.round(a), Math.round(b)]);
  assert.deepEqual(resumen, [["/videos/A", 10, 11], ["/videos/A", 50, 51], ["/videos/B", 5, 6]]);
  assert.match(doc.getElementById("lista-mensaje").textContent, /Se han reproducido todos/);
  assert.equal(doc.querySelectorAll(".corte.activo").length, 0, "al terminar no queda nada marcado");
  assert.ok(video.paused);
  assert.ok(lista);
});

test("durante la lista se marca el corte activo y se enlaza a la entrevista con «volver»", async () => {
  const { doc, video, api } = montar(HTML_LISTA);
  api.iniciarLista(doc);
  doc.getElementById("btn-reproducir").click();
  await hasta(() => doc.getElementById("lista-posicion").textContent === "Tramo 2 de 4");
  assert.ok(doc.getElementById("corte-b").classList.contains("activo"));
  assert.ok(doc.getElementById("corte-c").classList.contains("activo"));
  assert.equal(doc.getElementById("corte-a").classList.contains("activo"), false);
  assert.match(doc.getElementById("lista-intervalo").textContent, /une 2 cortes/);
  const href = doc.getElementById("lista-abrir").getAttribute("href");
  assert.match(href, /^\/entrevista\/A\?clip=b&volver=/);
  assert.equal(new URL("http://x" + decodeURIComponent(href.split("volver=")[1])).hash, "#corte-b");
  assert.ok(video);
});

test("«Corte siguiente» y «Corte anterior» saltan sin repetir ni quedarse bloqueados", async () => {
  const { doc, api } = montar(HTML_LISTA);
  const lista = api.iniciarLista(doc);
  doc.getElementById("btn-reproducir").click();
  await hasta(() => lista.estado().indice === 0);
  doc.getElementById("lista-siguiente").click();
  await hasta(() => lista.estado().indice === 1);
  doc.getElementById("lista-siguiente").click();       // el 2 no tiene vídeo → va al 3
  await hasta(() => lista.estado().indice === 3);
  assert.match(doc.getElementById("lista-mensaje").textContent, /Se omiten tramos sin vídeo/);
  doc.getElementById("lista-anterior").click();        // el 2 no tiene vídeo → va al 1
  await hasta(() => lista.estado().indice === 1);
});

test("cerrar el reproductor detiene todo", async () => {
  const { doc, video, api } = montar(HTML_LISTA);
  api.iniciarLista(doc);
  doc.getElementById("btn-reproducir").click();
  await hasta(() => !video.paused);
  doc.getElementById("lista-cerrar").click();
  assert.equal(doc.getElementById("recopilacion").hidden, true);
  assert.ok(video.paused);
});
