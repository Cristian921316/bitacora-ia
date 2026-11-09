// Barra superior compartida. Se incluye con: <script src="/assets/nav.js"></script>
//  - ADMIN: menú completo.   - GUARDIA: sin menú, solo su nombre, cambiar clave y salir.
// También vuelve al login cuando la sesión expira y a /clave cuando hay que cambiar la contraseña.
(function () {
  const _fetch = window.fetch.bind(window);
  const detener = () => new Promise(() => {});
  window.fetch = async (...args) => {
    const r = await _fetch(...args);
    if (r.status === 401) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
      return detener();
    }
    if (r.status === 403) {
      try {
        const d = await r.clone().json();
        if (d.detail === 'CAMBIAR_CLAVE') { location.href = '/clave'; return detener(); }
      } catch (e) { /* respuesta sin JSON */ }
    }
    return r;
  };

  const items = [
    ['/garita', '🛡️ Garita'],
    ['/reporte', '📋 Reporte'],
    ['/residentes', '👥 Residentes'],
    ['/usuarios', '🔑 Usuarios'],
    ['/configuracion', '⚙️ Configuración'],
    ['/residente', '🎙️ Pantalla residente'],
  ];
  const aqui = location.pathname.replace(/\/$/, '');
  const esc = s => (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function salir() {
    await _fetch('/api/salir', { method: 'POST' });
    location.href = '/login';
  }

  async function montar() {
    let yo;
    try {
      const r = await fetch('/api/yo');
      if (!r.ok) return;
      yo = await r.json();
    } catch (e) { return; }
    window.yo = yo;

    const bar = document.createElement('nav');
    bar.className = 'w-full flex items-center gap-1 px-3 py-2 bg-slate-950 border-b border-slate-800 text-sm overflow-x-auto shrink-0';
    const menu = yo.rol === 'ADMIN'
      ? items.map(([href, txt]) =>
          `<a href="${href}" class="px-3 py-1.5 rounded-md whitespace-nowrap ${
            aqui === href ? 'bg-indigo-600 text-white font-semibold' : 'text-slate-300 hover:bg-slate-800'}">${txt}</a>`).join('')
      : '';
    bar.innerHTML =
      `<span class="font-bold text-indigo-300 mr-3 whitespace-nowrap">Bitácora${yo.rol === 'ADMIN' ? ' · Admin' : ' · Garita'}</span>` +
      menu +
      `<span class="ml-auto flex items-center gap-2 whitespace-nowrap">
         <span class="text-xs text-slate-400">${esc(yo.nombre)}</span>
         <a href="/clave" class="text-xs text-slate-400 hover:text-slate-200 underline">Cambiar clave</a>
         <button id="btnSalir" class="px-3 py-1.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200">Salir</button>
       </span>`;
    document.body.prepend(bar);
    document.getElementById('btnSalir').onclick = salir;
    document.dispatchEvent(new CustomEvent('yo-listo', { detail: yo }));
  }

  if (document.body) montar(); else document.addEventListener('DOMContentLoaded', montar);
})();
