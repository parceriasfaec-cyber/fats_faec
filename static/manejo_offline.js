/* Fila offline do Manejo reprodutivo.
   - Guarda no celular (IndexedDB) os registros feitos sem sinal;
   - envia para o servidor sozinho quando a internet volta (e pelo botão);
   - lembra touro/sêmen/doadora de um animal para o próximo (localStorage). */
(function () {
  'use strict';
  var BANCO = 'fats-manejo', LOJA = 'fila', API_PADRAO = '/manejo/api/salvar';
  // cada tela diz para onde enviar (o portal do produtor usa o link dele)
  function apiAtual() { return window.MANEJO_API || API_PADRAO; }
  var LEMBRAR = 'fats-manejo-lembrar', VALIDADE_MS = 12 * 60 * 60 * 1000;
  var enviando = false;

  // ---------- IndexedDB ----------
  function abrir() {
    return new Promise(function (ok, erro) {
      if (!window.indexedDB) return erro(new Error('sem IndexedDB'));
      var q = indexedDB.open(BANCO, 1);
      q.onupgradeneeded = function () { q.result.createObjectStore(LOJA, { keyPath: 'id', autoIncrement: true }); };
      q.onsuccess = function () { ok(q.result); };
      q.onerror = function () { erro(q.error); };
    });
  }
  function transacao(modo, fn) {
    return abrir().then(function (db) {
      return new Promise(function (ok, erro) {
        var t = db.transaction(LOJA, modo), r = fn(t.objectStore(LOJA));
        t.oncomplete = function () { db.close(); ok(r && r.result); };
        t.onerror = t.onabort = function () { db.close(); erro(t.error); };
      });
    });
  }
  function adicionar(registro) { return transacao('readwrite', function (s) { return s.add({ dados: registro, em: Date.now(), api: apiAtual() }); }); }
  function listar() { return transacao('readonly', function (s) { return s.getAll(); }).then(function (l) { return l || []; }); }
  function remover(ids) { return transacao('readwrite', function (s) { ids.forEach(function (i) { s.delete(i); }); }); }
  function contar() {
    var api = apiAtual();
    return listar().then(function (l) { return l.filter(function (i) { return (i.api || API_PADRAO) === api; }).length; }).catch(function () { return 0; });
  }

  // ---------- envio ----------
  function enviar(registros, limiteMs, url) {
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, limiteMs || 15000);
    return fetch(url || apiAtual(), {
      method: 'POST', credentials: 'same-origin', signal: ctl ? ctl.signal : undefined,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ registros: registros })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        clearTimeout(timer);
        if (!r.ok || !j.ok) throw new Error(j.erro || ('HTTP ' + r.status));
        return j.resultados;
      });
    }, function (e) { clearTimeout(timer); throw e; });
  }
  // tenta enviar um; se não der, guarda na fila. Devolve {destino, status}
  function salvar(registro) {
    return enviar([registro], 10000).then(function (res) {
      return { destino: 'servidor', status: res[0].status, mensagem: res[0].mensagem };
    }).catch(function () {
      return adicionar(registro).then(function () { return { destino: 'celular', status: 'fila' }; });
    });
  }
  function sincronizar() {
    if (enviando) return Promise.resolve({ enviados: 0 });
    enviando = true;
    var api = apiAtual();
    return listar().then(function (todos) {
      var itens = todos.filter(function (i) { return (i.api || API_PADRAO) === api; });
      if (!itens.length) return { enviados: 0, restantes: 0 };
      var lote = itens.slice(0, 100);
      return enviar(lote.map(function (i) { return i.dados; }), 30000, api).then(function (res) {
        var apagar = [], criados = 0, repetidos = 0, descartados = 0, naoEncontrados = [];
        lote.forEach(function (it, k) {
          var st = res[k] && res[k].status;
          if (st === 'criado') { criados++; apagar.push(it.id); }
          else if (st === 'duplicado') { repetidos++; apagar.push(it.id); }
          else if (st === 'invalido') { descartados++; apagar.push(it.id); }
          else if (st === 'nao_encontrado') { naoEncontrados.push(res[k].brinco || ''); apagar.push(it.id); }
        });
        return remover(apagar).then(function () {
          return { enviados: criados, repetidos: repetidos, descartados: descartados,
                   naoEncontrados: naoEncontrados, restantes: itens.length - apagar.length };
        });
      });
    }).then(function (r) { enviando = false; atualizarAviso(); return r; },
            function (e) { enviando = false; atualizarAviso(); throw e; });
  }

  // ---------- avisos na tela ----------
  function aviso(texto, tipo) {
    var el = document.getElementById('mr-toast');
    if (!el) {
      el = document.createElement('div'); el.id = 'mr-toast';
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:fixed;left:12px;right:12px;top:64px;z-index:100;padding:12px 14px;border-radius:6px;font-size:.95rem;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.25);transition:opacity .25s;max-width:560px;margin:0 auto;';
      document.body.appendChild(el);
    }
    var cores = { ok: ['#e7e6d3', '#2b2015', '#4e5c2e'], aviso: ['#f5e5c4', '#8a4f0e', '#a15d12'], erro: ['#f2ded6', '#7c2e21', '#7c2e21'] }[tipo || 'ok'];
    el.style.background = cores[0]; el.style.color = cores[1]; el.style.border = '1px solid ' + cores[2];
    el.textContent = texto; el.style.opacity = '1'; el.style.display = 'block';
    clearTimeout(el._t); el._t = setTimeout(function () { el.style.opacity = '0'; setTimeout(function () { el.style.display = 'none'; }, 300); }, 4500);
  }
  function atualizarAviso() {
    return contar().then(function (n) {
      var box = document.getElementById('fila-aviso');
      if (box) {
        box.hidden = n === 0;
        var t = box.querySelector('[data-fila-texto]');
        if (t) t.textContent = '⏳ ' + n + (n === 1 ? ' registro guardado' : ' registros guardados') + ' no celular, aguardando envio.';
      }
      window.dispatchEvent(new CustomEvent('manejo-fila', { detail: { pendentes: n } }));
      return n;
    });
  }
  function sincronizarComAviso(mostrarSeVazio) {
    return sincronizar().then(function (r) {
      if (r.enviados || r.repetidos) {
        aviso('✔ ' + (r.enviados + r.repetidos) + ' registro(s) do celular enviado(s) ao sistema.', 'ok');
        if (document.body.classList.contains('tela-manejo-lista')) setTimeout(function () { location.reload(); }, 1200);
      } else if (mostrarSeVazio) {
        aviso('Nada pendente para enviar.', 'ok');
      }
      if (r.descartados) aviso(r.descartados + ' registro(s) incompleto(s) foram descartados.', 'aviso');
      if (r.naoEncontrados && r.naoEncontrados.length) aviso('Brinco não encontrado entre os seus animais (não registrado): ' + r.naoEncontrados.join(', '), 'erro');
      return r;
    }).catch(function () {
      if (mostrarSeVazio) aviso('Sem sinal ainda. Os registros continuam guardados no celular.', 'aviso');
    });
  }

  // ---------- "lembrar do animal anterior" ----------
  function lembrar(registro, campos) {
    try {
      var guardar = { em: Date.now(), dados: {} };
      campos.forEach(function (c) { if (registro[c]) guardar.dados[c] = registro[c]; });
      localStorage.setItem(LEMBRAR, JSON.stringify(guardar));
    } catch (e) { /* armazenamento bloqueado: segue sem lembrar */ }
  }
  function lembrado() {
    try {
      var g = JSON.parse(localStorage.getItem(LEMBRAR) || 'null');
      if (g && Date.now() - g.em < VALIDADE_MS) return g.dados;
    } catch (e) { /* ignora */ }
    return {};
  }

  // ---------- inicialização ----------
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () { /* sem SW: só não abre offline */ });
  }
  window.ManejoFila = { salvar: salvar, sincronizar: sincronizarComAviso, contar: contar, aviso: aviso, lembrar: lembrar, lembrado: lembrado, atualizar: atualizarAviso };
  window.addEventListener('online', function () { sincronizarComAviso(false); });
  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('fila-enviar');
    if (btn) btn.addEventListener('click', function () { sincronizarComAviso(true); });
    atualizarAviso().then(function (n) { if (n && navigator.onLine !== false) sincronizarComAviso(false); });
    setInterval(function () { contar().then(function (n) { if (n && navigator.onLine !== false) sincronizarComAviso(false); }); }, 30000);
  });
})();
