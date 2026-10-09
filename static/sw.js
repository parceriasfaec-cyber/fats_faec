/* Service worker do Manejo reprodutivo: deixa a tela de registro abrir sem
   sinal. Os registros feitos offline ficam na fila do celular (IndexedDB) e
   são enviados por static/manejo_offline.js quando a internet volta. */
const VERSAO = 'fats-manejo-v1';
const FIXOS = ['/static/manejo_offline.js', '/static/icone-192.png', '/static/icone-512.png'];
const TELAS = ['/manejo/novo', '/manejo'];

// só guarda páginas "limpas": sem aviso (flash) e sem redirecionamento
async function guardarPagina(chave, resposta) {
  if (!resposta || !resposta.ok || resposta.redirected) return;
  const texto = await resposta.clone().text();
  if (texto.includes('class="flash')) return;
  const cache = await caches.open(VERSAO);
  await cache.put(chave, resposta);
}

self.addEventListener('install', (ev) => {
  ev.waitUntil((async () => {
    const cache = await caches.open(VERSAO);
    await cache.addAll(FIXOS);
    for (const url of TELAS) {
      try { await guardarPagina(url, await fetch(url, { credentials: 'same-origin' })); } catch (e) { /* sem sinal na instalação */ }
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil((async () => {
    for (const nome of await caches.keys()) if (nome !== VERSAO) await caches.delete(nome);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (ev) => {
  const req = ev.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // arquivos estáticos: usa o que tem guardado e atualiza em segundo plano
  if (url.pathname.startsWith('/static/')) {
    ev.respondWith((async () => {
      const cache = await caches.open(VERSAO);
      const guardado = await cache.match(req);
      const rede = fetch(req).then((r) => { if (r.ok) cache.put(req, r.clone()); return r; }).catch(() => null);
      return guardado || (await rede) || Response.error();
    })());
    return;
  }

  // telas do manejo: tenta a internet (até 4 s); sem sinal, usa a cópia guardada
  const ehTela = req.mode === 'navigate' && (url.pathname === '/manejo' || url.pathname === '/manejo/novo');
  if (ehTela) {
    ev.respondWith((async () => {
      const chave = url.pathname + url.search;
      try {
        const rede = fetch(req);
        const limite = new Promise((_, rej) => setTimeout(() => rej(new Error('lento')), 4000));
        const resposta = await Promise.race([rede, limite]);
        ev.waitUntil(guardarPagina(chave, resposta.clone()).catch(() => {}));
        return resposta;
      } catch (e) {
        const cache = await caches.open(VERSAO);
        return (await cache.match(chave)) || (await cache.match(url.pathname)) || (await cache.match('/manejo/novo')) || Response.error();
      }
    })());
  }
});
