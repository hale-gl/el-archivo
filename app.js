/* ============================================================
   CONFIG
   ============================================================ */
const CATS = {
  series: { label: 'Series', mode: 'seasons',
    tracks: [{ key: 'seasons', unitWord: 'Temporada', unitPlural: 'temporadas', countWord: 'ep.' }] },
  pelicula: { label: 'Películas', mode: 'watched',
    tracks: [] },
  drama: { label: 'Drama', mode: 'seasons',
    tracks: [{ key: 'seasons', unitWord: 'Capitulo', unitPlural: 'capitulos', countWord: 'cap.', linear: true }] },
  anime: { label: 'Anime', mode: 'seasons',
    tracks: [{ key: 'seasons', unitWord: 'Capitulo', unitPlural: 'capitulos', countWord: 'cap.', linear: true }] },
  lectura: { label: 'Manga & Manhwa', mode: 'seasons',
    tracks: [
      { key: 'seasons', unitWord: 'Capitulo', unitPlural: 'capitulos', countWord: 'cap.', linear: true },
    ] },
};
const STATUS_ORDER = ['pendiente', 'en-curso', 'completado'];
const STORAGE_KEY = 'archivo-catalog';
const COVERS_KEY = 'archivo-covers';
const MAX_IMAGE_SIZE = 900;

/* ============================================================
   STATE
   ============================================================ */
let catalog = [];
let covers = {};
let currentTab = 'todo';
let currentStatus = '';
let currentWho = '';
let searchTerm = '';
let editingId = null;
let expandedCards = new Set();
let imageCleared = false;
let onlineMatches = [];
let currentSession = null;

/* ============================================================
   DOM REFS
   ============================================================ */
const grid = document.getElementById('grid');
const tabsEl = document.getElementById('tabs');
const emptyState = document.getElementById('emptyState');
const overlay = document.getElementById('overlay');
const form = document.getElementById('itemForm');
const toast = document.getElementById('toast');
const coverBanner = document.getElementById('coverBanner');
const coverImg = document.getElementById('coverImg');
const coverLabel = document.getElementById('coverLabel');
const coverOverlay = document.getElementById('coverOverlay');
const imageFileInput = document.getElementById('f-image-file');
const imageUrlInput = document.getElementById('f-image');
const imagePreview = document.getElementById('imagePreview');
const imagePreviewImg = document.getElementById('imagePreviewImg');
const coverFileInput = document.getElementById('f-cover-file');
const coverUrlInput = document.getElementById('f-cover-url');
const linkInput = document.getElementById('f-link');
const whoInput = document.getElementById('f-who');
const metadataSearchBtn = document.getElementById('metadataSearchBtn');
const metadataStatus = document.getElementById('metadataStatus');
const metadataResults = document.getElementById('metadataResults');
const usersBtn = document.getElementById('usersBtn');
const userOverlay = document.getElementById('userOverlay');
const userList = document.getElementById('userList');
const userForm = document.getElementById('userForm');
const sidebarSearch = document.getElementById('sidebarSearch');
const sidebarSearchBtn = document.getElementById('sidebarSearchBtn');

/* ============================================================
   HELPERS
   ============================================================ */
function uid() {
  return 'i' + Date.now() + Math.random().toString(36).slice(2, 7);
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[m]));
}

function fallbackHtml(title) {
  return `<div class="poster-fallback">${escapeHtml(title)}</div>`;
}

function statusLabel(s) {
  return s === 'pendiente' ? 'Pendiente' : s === 'en-curso' ? 'En curso' : 'Terminado';
}

function showToast(msg, type = 'success') {
  toast.textContent = msg || 'Guardado ✓';
  toast.classList.toggle('error', type === 'error');
  toast.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.remove('show'), type === 'error' ? 2600 : 1400);
}

function trackSummary(item, track) {
  const entries = item[track.key] || [];
  if (track.linear) {
    const entry = linearEntry(item, track.key);
    return entry.total
      ? `Capitulos ${entry.watched || 0}/${entry.total}`
      : `Capitulos ${entry.watched || 0}`;
  }
  if (!entries.length) return `Sin ${track.unitPlural} aún`;
  const unit = entries.length === 1 ? track.unitWord.toLowerCase() : track.unitPlural;
  const done = entries.filter(s => s.status === 'completado').length;
  const doneWord = done === 1 ? 'completo' : 'completos';
  const totalEp = entries.reduce((a, s) => a + (s.total || 0), 0);
  const watchedEp = entries.reduce((a, s) => a + (s.watched || 0), 0);
  const cw = track.countWord || '';
  const epPart = totalEp ? `${watchedEp}/${totalEp} ${cw}`.trim() : `${watchedEp} ${cw}`.trim();
  return `${entries.length} ${unit} · ${done} ${doneWord} · ${epPart}`;
}

function computeItemStatus(item) {
  return item.status || 'pendiente';
}

function autoStatusFromTracks(item) {
  const cat = CATS[item.category];
  const primary = cat.tracks[0];
  const entries = item[primary.key] || [];
  if (!entries.length) return item.status || 'pendiente';
  if (entries.every(s => s.status === 'completado')) return 'completado';
  if (entries.every(s => s.status === 'pendiente')) return 'pendiente';
  return 'en-curso';
}

function isLinearCategory(category) {
  return CATS[category]?.tracks?.some(track => track.linear);
}

function linearEntry(item, trackKey = 'seasons') {
  const entries = item[trackKey] || [];
  if (entries.length === 1) return entries[0];

  const watched = entries.reduce((sum, entry) => sum + (entry.watched || 0), 0);
  const total = entries.reduce((sum, entry) => sum + (entry.total || 0), 0);
  const status = total && watched >= total
    ? 'completado'
    : watched > 0
      ? 'en-curso'
      : 'pendiente';

  return { number: 1, total: total || null, watched, status };
}

function normalizeLinearProgress(item) {
  if (!isLinearCategory(item.category)) return item;
  const entry = linearEntry(item, 'seasons');
  return { ...item, seasons: [entry], volumes: [] };
}

function parseNonNegativeInt(value) {
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function setPreview(src) {
  if (!src) {
    imagePreview.style.display = 'none';
    imagePreviewImg.src = '';
    return;
  }
  imagePreviewImg.src = src;
  imagePreview.style.display = 'flex';
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    if (!file) return resolve('');
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('No se pudo leer la imagen.'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => resolve(reader.result);
      img.onload = () => {
        const scale = Math.min(1, MAX_IMAGE_SIZE / Math.max(img.width, img.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(img.width * scale));
        canvas.height = Math.max(1, Math.round(img.height * scale));
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL('image/jpeg', 0.86));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

/* ============================================================
   PERSISTENCE
   ============================================================ */
const API_ENDPOINTS = {
  [STORAGE_KEY]: '/api/catalog',
  [COVERS_KEY]: '/api/covers',
};

function normalizeCatalogItem(row) {
  const parseList = v => {
    if (Array.isArray(v)) return v;
    if (typeof v === 'string' && v) {
      try { return JSON.parse(v); } catch (e) { return []; }
    }
    return [];
  };
  return normalizeLinearProgress({
    ...row,
    seasons: parseList(row.seasons),
    volumes: parseList(row.volumes),
    updatedAt: row.updatedAt || (row.updated_at ? new Date(row.updated_at).getTime() : Date.now()),
  });
}

async function storageGet(key, fallback) {
  const endpoint = API_ENDPOINTS[key];
  if (endpoint) {
    try {
      const res = await fetch(endpoint);
      if (res.status === 401) {
        window.location.href = '/login';
        return fallback;
      }
      if (res.ok) {
        const data = await res.json();
        const value = key === STORAGE_KEY ? data.map(normalizeCatalogItem) : data;
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
        return value;
      }
    } catch (e) {
      console.warn('No se pudo leer del servidor, usando localStorage', e);
    }
  }

  try {
    const localValue = localStorage.getItem(key);
    if (localValue) {
      const parsed = JSON.parse(localValue);
      return key === STORAGE_KEY ? parsed.map(normalizeCatalogItem) : parsed;
    }
  } catch (e) {
    console.warn('No se pudo leer localStorage', e);
  }

  return fallback;
}

async function storageSet(key, value) {
  const serialized = JSON.stringify(value);
  try {
    localStorage.setItem(key, serialized);
  } catch (e) {
    alert('No se pudo guardar todo. Prueba con una imagen más pequeña o usa una URL.');
    throw e;
  }

  const endpoint = API_ENDPOINTS[key];
  if (endpoint) {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: serialized,
    });
    if (res.status === 401) {
      window.location.href = '/login';
      return;
    }
    if (!res.ok) throw new Error('Error guardando en el servidor');
  }
}

async function load() {
  catalog = await storageGet(STORAGE_KEY, []);
  covers = await storageGet(COVERS_KEY, {});
  render();
}

async function save(msg) {
  try {
    await storageSet(STORAGE_KEY, catalog);
    showToast(msg || 'Guardado ✓');
  } catch (e) {
    console.error('No se pudo guardar', e);
    showToast('Guardado local; fallo el servidor', 'error');
  }
}

async function saveCovers(msg) {
  try {
    await storageSet(COVERS_KEY, covers);
    showToast(msg || 'Portada guardada ✓');
  } catch (e) {
    console.error('No se pudo guardar la portada', e);
    showToast('Portada local; fallo el servidor', 'error');
  }
}

async function loadSession() {
  const res = await fetch('/api/session');
  const data = await res.json();
  if (!data.authenticated) {
    window.location.href = '/login';
    return false;
  }
  currentSession = data.user;
  usersBtn.style.display = currentSession?.role === 'admin' ? 'inline-block' : 'none';
  return true;
}

function renderUsers(users) {
  const slotLabel = slot => slot === 'P1' ? 'Persona 1' : slot === 'P2' ? 'Persona 2' : 'sin perfil';
  userList.innerHTML = users.map(user => `
    <div class="user-row ${user.active ? '' : 'inactive'}">
      <div>
        <strong>${escapeHtml(user.displayName || user.username)}</strong>
        <span>${escapeHtml(user.username)} · ${escapeHtml(user.role)} · ${user.active ? 'activo' : 'inactivo'} · ${slotLabel(user.profileSlot)}</span>
        ${user.badge ? `<span class="badge badge-${user.badge.level}">${escapeHtml(user.badge.label)}</span>` : ''}
      </div>
      <div class="user-actions">
        ${user.active
          ? `<button type="button" class="link-btn" data-user-edit="${user.id}">Editar</button>`
          : ''}
        ${user.active && user.username !== currentSession?.username
          ? `<button type="button" class="link-btn danger" data-user-disable="${user.id}">Desactivar</button>`
          : ''}
        ${!user.active
          ? `<button type="button" class="link-btn" data-user-activate="${user.id}">Activar</button>`
          : ''}
      </div>
    </div>
  `).join('');
  userList.querySelectorAll('[data-user-edit]').forEach(button => {
    button.addEventListener('click', () => editUser(button.dataset.userEdit));
  });
  userList.querySelectorAll('[data-user-disable]').forEach(button => {
    button.addEventListener('click', () => disableUser(button.dataset.userDisable));
  });
  userList.querySelectorAll('[data-user-activate]').forEach(button => {
    button.addEventListener('click', () => activateUser(button.dataset.userActivate));
  });
}

async function openUsersPanel() {
  const res = await fetch('/api/users');
  if (res.status === 403) {
    showToast('Solo el admin puede ver usuarios', 'error');
    return;
  }
  if (res.status === 401) {
    window.location.href = '/login';
    return;
  }
  renderUsers(await res.json());
  userOverlay.classList.add('show');
  resetUserForm();
}

let editingUserId = null;

function resetUserForm() {
  editingUserId = null;
  userForm.reset();
  document.getElementById('u-color').value = '#3b82f6';
  document.getElementById('modalTitle').textContent = 'Usuarios';
}

async function editUser(userId) {
  const res = await fetch('/api/users');
  if (!res.ok) return;
  const users = await res.json();
  const user = users.find(u => u.id === parseInt(userId));
  if (!user) return;
  
  editingUserId = userId;
  document.getElementById('u-username').value = user.username;
  document.getElementById('u-username').disabled = true;
  document.getElementById('u-display').value = user.displayName || '';
  document.getElementById('u-role').value = user.role;
  document.getElementById('u-slot').value = user.profileSlot || '';
  document.getElementById('u-color').value = user.color || '#3b82f6';
  document.getElementById('u-password').value = '';
  document.getElementById('u-password').placeholder = 'Dejar vacio para mantener contrasena actual';
  document.getElementById('u-password').required = false;
  
  document.querySelector('#userOverlay h2').textContent = 'Editar usuario';
}

async function saveUser(event) {
  event.preventDefault();
  const payload = {
    username: document.getElementById('u-username').value.trim(),
    displayName: document.getElementById('u-display').value.trim(),
    password: document.getElementById('u-password').value,
    role: document.getElementById('u-role').value,
    profileSlot: document.getElementById('u-slot').value || null,
    color: document.getElementById('u-color').value || '#3b82f6',
  };
  
  let res;
  if (editingUserId) {
    res = await fetch(`/api/users/${editingUserId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        displayName: payload.displayName,
        color: payload.color,
        profileSlot: payload.profileSlot,
      }),
    });
  } else {
    if (!payload.password || payload.password.length < 6) {
      showToast('Contrasena minimo 6 caracteres', 'error');
      return;
    }
    res = await fetch('/api/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }
  
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.error || 'No se pudo guardar usuario', 'error');
    return;
  }
  
  resetUserForm();
  showToast(editingUserId ? 'Usuario actualizado' : 'Usuario guardado');
  await openUsersPanel();
  await loadProfiles();
}

async function disableUser(userId) {
  if (!confirm('Desactivar este usuario?')) return;
  const res = await fetch(`/api/users/${userId}`, { method: 'DELETE' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.error || 'No se pudo desactivar', 'error');
    return;
  }
  showToast('Usuario desactivado');
  await openUsersPanel();
  await loadProfiles();
}

async function activateUser(userId) {
  const res = await fetch(`/api/users/${userId}/activate`, { method: 'POST' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.error || 'No se pudo activar', 'error');
    return;
  }
  showToast('Usuario activado');
  await openUsersPanel();
  await loadProfiles();
}

async function loadProfiles() {
  try {
    const res = await fetch('/api/profiles');
    if (!res.ok) return;
    const profiles = await res.json();
    const bySlot = Object.fromEntries(profiles.map(p => [p.slot, p]));
    document.querySelectorAll('.who-btn[data-who]').forEach(btn => {
      const slot = btn.dataset.who;
      const profile = bySlot[slot];
      const label = profile ? profile.displayName : (slot === 'P1' ? 'Persona 1' : slot === 'P2' ? 'Persona 2' : 'Compartido');
      btn.textContent = label;
      if (profile && profile.color) {
        btn.style.backgroundColor = profile.color;
        btn.style.color = getContrastColor(profile.color);
      } else {
        btn.style.backgroundColor = '';
        btn.style.color = '';
      }
      if (profile && profile.badge) {
        btn.title = `${profile.badge.label} (${profile.badge.count} series/anime)`;
      } else {
        btn.title = '';
      }
    });
    if (whoInput) {
      Array.from(whoInput.options).forEach(opt => {
        if (opt.value === 'P1' || opt.value === 'P2') {
          const profile = bySlot[opt.value];
          opt.textContent = profile ? profile.displayName : (opt.value === 'P1' ? 'Persona 1' : 'Persona 2');
        }
      });
    }
  } catch (e) {
    console.error('No se pudieron cargar los perfiles', e);
  }
}

function getContrastColor(hexcolor) {
  if (!hexcolor) return '';
  const r = parseInt(hexcolor.slice(1, 3), 16);
  const g = parseInt(hexcolor.slice(3, 5), 16);
  const b = parseInt(hexcolor.slice(5, 7), 16);
  const yiq = ((r * 299) + (g * 587) + (b * 114)) / 1000;
  return (yiq >= 128) ? '#000000' : '#ffffff';
}

if (sidebarSearch) {
  sidebarSearch.addEventListener('input', (e) => {
    searchTerm = e.target.value.trim();
    render();
  });
}

if (sidebarSearchBtn) {
  sidebarSearchBtn.addEventListener('click', () => {
    if (sidebarSearch) {
      searchTerm = sidebarSearch.value.trim();
      render();
    }
  });
}

/* ============================================================
   TABS / FILTERS
   ============================================================ */
function buildTabs() {
  const profileItems = catalog.filter(matchesCurrentProfile);
  const counts = { todo: profileItems.length };
  Object.keys(CATS).forEach(c => counts[c] = profileItems.filter(i => i.category === c).length);
  const tabs = [{ key: 'todo', label: 'Todo' }, ...Object.entries(CATS).map(([k, v]) => ({ key: k, label: v.label }))];
  tabsEl.innerHTML = tabs.map(t => `
    <div class="tab ${t.key === currentTab ? 'active' : ''}" data-tab="${t.key}">
      ${t.label}<span class="count">${counts[t.key] || 0}</span>
    </div>`).join('');
  tabsEl.querySelectorAll('.tab').forEach(el => {
    el.addEventListener('click', () => { currentTab = el.dataset.tab; render(); });
  });
}

function matchesCurrentProfile(item) {
  return (item.who || '') === currentWho;
}

function filtered() {
  return catalog.filter(i => {
    if (!matchesCurrentProfile(i)) return false;
    if (currentTab !== 'todo' && i.category !== currentTab) return false;
    if (currentStatus && computeItemStatus(i) !== currentStatus) return false;
    if (searchTerm && !i.title.toLowerCase().includes(searchTerm.toLowerCase())) return false;
    return true;
  });
}

/* ============================================================
   RENDER
   ============================================================ */
function render() {
  buildTabs();
  renderCoverBanner();
  const items = filtered();
  emptyState.style.display = items.length ? 'none' : 'block';
  grid.innerHTML = items.map(i => renderCard(i)).join('');
  bindCardEvents();
}

function renderCoverBanner() {
  if (currentTab === 'todo') {
    coverBanner.style.display = 'none';
    return;
  }
  coverBanner.style.display = 'block';
  const url = covers[currentTab] || '';
  coverLabel.textContent = CATS[currentTab].label;
  if (url) {
    coverImg.src = url;
    coverImg.style.display = 'block';
    coverBanner.style.background = '';
  } else {
    coverImg.style.display = 'none';
    coverBanner.style.background = 'var(--panel)';
  }
}

function renderCard(i) {
  const cat = CATS[i.category];
  const posterImageHtml = i.image
    ? `<img class="poster" src="${escapeHtml(i.image)}" onerror="this.outerHTML=fallbackHtml('${escapeHtml(i.title).replace(/'/g, "\\'")}')">`
    : fallbackHtml(i.title);
  const posterHtml = i.link
    ? `<a class="poster-link" href="${escapeHtml(i.link)}" target="_blank" rel="noopener noreferrer" title="Abrir enlace">${posterImageHtml}</a>`
    : posterImageHtml;
  const status = computeItemStatus(i);
  const stubHtml = cat.mode === 'seasons'
    ? cat.tracks.map(track => renderTrackStub(i, track)).join('')
    : '';
  const linkHtml = i.link
    ? `<a class="link-btn" href="${escapeHtml(i.link)}" target="_blank" rel="noopener noreferrer">Abrir</a>`
    : '';
  const whoHtml = i.who ? `<span class="who-tag">${escapeHtml(i.who)}</span>` : '';

  const statusSelect = `
    <div class="status-select">
      ${STATUS_ORDER.map(st => `
        <button class="status-opt status-${st} ${status === st ? 'active' : ''}" data-act="set-status" data-id="${i.id}" data-status="${st}">${statusLabel(st)}</button>
      `).join('')}
    </div>`;

  return `
    <div class="card">
      <div class="cat-tag">${i.category === 'lectura' && i.subtype ? i.subtype : cat.label}</div>
      ${posterHtml}
      <div class="card-body">
        <div class="card-title">${escapeHtml(i.title)}</div>
        ${whoHtml}
        ${statusSelect}
        <div class="card-actions">
          ${linkHtml}
          <button class="link-btn" data-act="edit" data-id="${i.id}">Editar</button>
          <button class="link-btn danger" data-act="delete" data-id="${i.id}">Eliminar</button>
        </div>
        ${stubHtml}
      </div>
    </div>`;
}

function renderTrackStub(i, track) {
  if (track.linear) return renderLinearTrack(i, track);

  const expandKey = `${i.id}:${track.key}`;
  const isOpen = expandedCards.has(expandKey);
  const entries = (i[track.key] || []).slice().sort((a, b) => a.number - b.number);
  const initial = track.unitWord[0];

  const rows = entries.map(s => {
    const watched = s.watched || 0;
    const total = s.total || 0;
    const atLimit = total && watched >= total;
    const progress = total ? Math.min(100, Math.round((watched / total) * 100)) : 0;
    const totalLabel = total ? `${total} cap.` : 'sin total';
    return `
      <div class="season-row" data-num="${s.number}">
        <div class="season-head">
          <span class="season-num">${initial}${s.number}</span>
          <span class="season-total">${watched} / ${totalLabel}</span>
        </div>
        <div class="watch-progress" aria-hidden="true"><span style="width:${progress}%"></span></div>
        <div class="season-actions-row">
          <button class="mini-control" data-act="t-dec" data-id="${i.id}" data-track="${track.key}" data-num="${s.number}">−</button>
          <input class="count-input" type="number" min="0" ${total ? `max="${total}"` : ''} value="${watched}" data-act="t-set-watched" data-id="${i.id}" data-track="${track.key}" data-num="${s.number}" aria-label="Capitulos vistos">
          <button class="watch-btn" data-act="t-inc" data-id="${i.id}" data-track="${track.key}" data-num="${s.number}" ${atLimit ? 'disabled' : ''}>+1 visto</button>
          <button class="status-chip-btn status-${s.status}" data-act="t-status" data-id="${i.id}" data-track="${track.key}" data-num="${s.number}">${statusLabel(s.status)}</button>
        </div>
      </div>`;
  }).join('') || '<div class="season-empty">Configura las temporadas desde Editar.</div>';

  return `
    <div class="stub">
      <button class="seasons-toggle ${isOpen ? 'open' : ''}" data-act="toggle-track" data-id="${i.id}" data-track="${track.key}">
        <span>${trackSummary(i, track)}</span>
        <span class="chevron">▾</span>
      </button>
      <div class="seasons-panel ${isOpen ? 'open' : ''}">
        <div class="seasons-inner">
          ${rows}
        </div>
      </div>
    </div>`;
}

function renderLinearTrack(i, track) {
  const entry = linearEntry(i, track.key);
  const watched = entry.watched || 0;
  const total = entry.total || 0;
  const progress = total ? Math.min(100, Math.round((watched / total) * 100)) : 0;
  const atLimit = total && watched >= total;

  return `
    <div class="stub">
      <div class="linear-chapters">
        <div class="linear-title">Capitulos</div>
        <div class="season-head">
          <span class="linear-label">Vistos</span>
          <span class="season-total">${watched} / ${total ? total + ' cap.' : 'sin total'}</span>
        </div>
        <div class="watch-progress" aria-hidden="true"><span style="width:${progress}%"></span></div>
        <div class="season-actions-row">
            <button class="mini-control" data-act="linear-watch" data-id="${i.id}" data-track="${track.key}" data-delta="-1">-</button>
            <input class="count-input" type="number" min="0" ${total ? `max="${total}"` : ''} value="${watched}" data-act="linear-set-watched" data-id="${i.id}" data-track="${track.key}" aria-label="Capitulos vistos">
            <button class="watch-btn" data-act="linear-watch" data-id="${i.id}" data-track="${track.key}" data-delta="1" ${atLimit ? 'disabled' : ''}>+1 visto</button>
            <button class="status-chip-btn status-${entry.status}" data-act="linear-status" data-id="${i.id}" data-track="${track.key}">${statusLabel(entry.status)}</button>
        </div>
      </div>
    </div>`;
}

function bindCardEvents() {
  grid.querySelectorAll('[data-act="edit"]').forEach(b => b.addEventListener('click', () => openEdit(b.dataset.id)));
  grid.querySelectorAll('[data-act="delete"]').forEach(b => b.addEventListener('click', () => removeItem(b.dataset.id)));
  grid.querySelectorAll('[data-act="set-status"]').forEach(b => b.addEventListener('click', () => setItemStatus(b.dataset.id, b.dataset.status)));
  grid.querySelectorAll('[data-act="toggle-track"]').forEach(b => b.addEventListener('click', () => {
    const key = `${b.dataset.id}:${b.dataset.track}`;
    if (expandedCards.has(key)) expandedCards.delete(key); else expandedCards.add(key);
    render();
  }));
  grid.querySelectorAll('[data-act="t-inc"]').forEach(b => b.addEventListener('click', () => stepTrackEntry(b.dataset.id, b.dataset.track, +b.dataset.num, 1)));
  grid.querySelectorAll('[data-act="t-dec"]').forEach(b => b.addEventListener('click', () => stepTrackEntry(b.dataset.id, b.dataset.track, +b.dataset.num, -1)));
  grid.querySelectorAll('[data-act="t-set-watched"]').forEach(input => input.addEventListener('change', () => setTrackWatched(input.dataset.id, input.dataset.track, +input.dataset.num, input.value)));
  grid.querySelectorAll('[data-act="t-status"]').forEach(b => b.addEventListener('click', () => cycleTrackStatus(b.dataset.id, b.dataset.track, +b.dataset.num)));
  grid.querySelectorAll('[data-act="linear-watch"]').forEach(b => b.addEventListener('click', () => adjustLinearWatched(b.dataset.id, b.dataset.track, +b.dataset.delta)));
  grid.querySelectorAll('[data-act="linear-set-watched"]').forEach(input => input.addEventListener('change', () => setLinearWatched(input.dataset.id, input.dataset.track, input.value)));
  grid.querySelectorAll('[data-act="linear-status"]').forEach(b => b.addEventListener('click', () => cycleLinearStatus(b.dataset.id, b.dataset.track)));
}

/* ============================================================
   ACTIONS
   ============================================================ */
async function setItemStatus(id, status) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  item.status = status;
  render();
  await save();
}

function trackConfig(item, trackKey) {
  return CATS[item.category].tracks.find(t => t.key === trackKey);
}

function setLinearEntry(item, trackKey, entry) {
  item[trackKey] = [entry];
  item.status = autoStatusFromTracks(item);
  expandedCards.add(`${item.id}:${trackKey}`);
}

function updateLinearEntryProgress(entry) {
  if (entry.total) entry.watched = Math.min(entry.watched, entry.total);
  if (entry.total && entry.watched >= entry.total) entry.status = 'completado';
  else if (entry.watched > 0) entry.status = 'en-curso';
  else entry.status = 'pendiente';
}

async function adjustLinearWatched(id, trackKey, delta) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  const entry = linearEntry(item, trackKey);
  entry.watched = Math.max(0, (entry.watched || 0) + delta);
  updateLinearEntryProgress(entry);
  setLinearEntry(item, trackKey, entry);
  render();
  await save();
}

async function setLinearWatched(id, trackKey, value) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  const entry = linearEntry(item, trackKey);
  entry.watched = parseNonNegativeInt(value);
  updateLinearEntryProgress(entry);
  setLinearEntry(item, trackKey, entry);
  render();
  await save();
}

async function cycleLinearStatus(id, trackKey) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  const entry = linearEntry(item, trackKey);
  const order = ['pendiente', 'en-curso', 'completado'];
  entry.status = order[(order.indexOf(entry.status) + 1) % order.length];
  if (entry.status === 'pendiente') entry.watched = 0;
  if (entry.status === 'completado' && entry.total) entry.watched = entry.total;
  setLinearEntry(item, trackKey, entry);
  render();
  await save();
}

async function addTrackEntry(id, trackKey, number, total) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  if (!item[trackKey]) item[trackKey] = [];
  item[trackKey].push({ number, total: total || null, watched: 0, status: 'pendiente' });
  item.status = autoStatusFromTracks(item);
  expandedCards.add(`${id}:${trackKey}`);
  render();
  await save(`${trackConfig(item, trackKey).unitWord} añadido/a ✓`);
}

async function stepTrackEntry(id, trackKey, number, delta) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  const s = (item[trackKey] || []).find(entry => entry.number === number);
  if (!s) return;
  s.watched = Math.max(0, (s.watched || 0) + delta);
  updateTrackEntryProgress(s);
  item.status = autoStatusFromTracks(item);
  render();
  await save();
}

function updateTrackEntryProgress(s) {
  if (s.total) s.watched = Math.min(s.watched, s.total);
  if (s.total && s.watched >= s.total) s.status = 'completado';
  else if (s.watched > 0) s.status = 'en-curso';
  else s.status = 'pendiente';
}

async function setTrackWatched(id, trackKey, number, value) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  const s = (item[trackKey] || []).find(entry => entry.number === number);
  if (!s) return;
  s.watched = parseNonNegativeInt(value);
  updateTrackEntryProgress(s);
  item.status = autoStatusFromTracks(item);
  render();
  await save();
}

async function cycleTrackStatus(id, trackKey, number) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  const s = (item[trackKey] || []).find(entry => entry.number === number);
  if (!s) return;
  const order = ['pendiente', 'en-curso', 'completado'];
  s.status = order[(order.indexOf(s.status) + 1) % order.length];
  if (s.status === 'completado' && s.total) s.watched = s.total;
  if (s.status === 'pendiente') s.watched = 0;
  item.status = autoStatusFromTracks(item);
  render();
  await save();
}

async function deleteTrackEntry(id, trackKey, number) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  item[trackKey] = (item[trackKey] || []).filter(s => s.number !== number);
  item.status = autoStatusFromTracks(item);
  expandedCards.add(`${id}:${trackKey}`);
  render();
  await save('Eliminado/a');
}

async function removeItem(id) {
  if (!confirm('¿Eliminar este título del archivo?')) return;
  catalog = catalog.filter(i => i.id !== id);
  render();
  await save('Eliminado');
}

function resetMetadataLookup(message = 'Busca poster, enlace y capitulos.') {
  onlineMatches = [];
  if (metadataStatus) metadataStatus.textContent = message;
  if (metadataResults) metadataResults.innerHTML = '';
}

function metadataDetailText(item) {
  const parts = [];
  if (item.year) parts.push(item.year);
  if (item.seasons?.length) parts.push(`${item.seasons.length} temporadas`);
  if (item.total) parts.push(`${item.total} capitulos`);
  if (item.providers?.length) parts.push(item.providers.join(', '));
  return parts.join(' · ') || item.sourceLabel || '';
}

function renderMetadataResults(results) {
  onlineMatches = results || [];
  if (!metadataResults) return;
  if (!onlineMatches.length) {
    metadataResults.innerHTML = '<div class="metadata-empty">No encontre coincidencias para esta categoria.</div>';
    return;
  }
  metadataResults.innerHTML = onlineMatches.map((item, index) => `
    <button type="button" class="metadata-result" data-online-index="${index}">
      <span class="metadata-thumb">
        ${item.image ? `<img src="${escapeHtml(item.image)}" alt="">` : '<span></span>'}
      </span>
      <span class="metadata-copy">
        <strong>${escapeHtml(item.title || 'Sin titulo')}</strong>
        <small>${escapeHtml(metadataDetailText(item))}</small>
      </span>
      <span class="metadata-source">${escapeHtml(item.sourceLabel || 'API')}</span>
    </button>
  `).join('');
  metadataResults.querySelectorAll('[data-online-index]').forEach(button => {
    button.addEventListener('click', () => applyMetadataResult(onlineMatches[+button.dataset.onlineIndex]));
  });
}

async function searchOnlineMetadata() {
  const titleInput = document.getElementById('f-title');
  const query = titleInput.value.trim();
  const category = document.getElementById('f-category').value;
  if (query.length < 2) {
    resetMetadataLookup('Escribe un titulo primero.');
    titleInput.focus();
    return;
  }

  metadataSearchBtn.disabled = true;
  metadataStatus.textContent = 'Buscando...';
  metadataResults.innerHTML = '';
  try {
    const params = new URLSearchParams({ q: query, category });
    const res = await fetch(`/api/metadata/search?${params.toString()}`);
    if (!res.ok) throw new Error('metadata');
    const data = await res.json();
    renderMetadataResults(data.results || []);
    metadataStatus.textContent = data.results?.length
      ? 'Elige el resultado correcto.'
      : 'No encontre resultados.';
  } catch (e) {
    console.error('No se pudo buscar metadata', e);
    resetMetadataLookup('No se pudo buscar online.');
  } finally {
    metadataSearchBtn.disabled = false;
  }
}

function openProviderSearch(provider) {
  const titleInput = document.getElementById('f-title');
  const title = titleInput.value.trim();
  if (!title) {
    resetMetadataLookup('Escribe un titulo para buscar en Google.');
    titleInput.focus();
    return;
  }

  const label = provider === 'jkanime' ? 'Jkanime' : 'Seriesflix';
  const query = `${title} ${label}`;
  window.open(`https://www.google.com/search?q=${encodeURIComponent(query)}`, '_blank', 'noopener,noreferrer');
  metadataStatus.textContent = `Busca el resultado de ${label} y pega el enlace correcto abajo.`;
}

function applyMetadataResult(item) {
  if (!item) return;
  document.getElementById('f-title').value = item.title || document.getElementById('f-title').value;
  linkInput.value = item.link || linkInput.value;
  if (item.image) {
    imageCleared = false;
    imageFileInput.value = '';
    imageUrlInput.value = item.image;
    setPreview(item.image);
  }

  const category = document.getElementById('f-category').value;
  const cat = CATS[category];
  if (cat?.mode === 'seasons') {
    syncFormVisibility();
    cat.tracks.forEach(track => {
      if (track.linear) {
        const input = document.querySelector(`#f-progress-editor [data-progress-track="${track.key}"]`);
        const total = item.total || item.seasons?.reduce((sum, row) => sum + (row.total || 0), 0) || '';
        if (input && total) input.value = total;
        return;
      }

      const list = document.querySelector(`#f-progress-editor [data-progress-list="${track.key}"]`);
      if (!list || !item.seasons?.length) return;
      const initial = list.dataset.progressInitial || 'T';
      list.innerHTML = item.seasons
        .filter(row => row.number && row.total)
        .map(row => renderProgressEditorRow(track.key, initial, row.number, row.total))
        .join('');
      bindProgressEditorEvents();
    });
  }

  metadataStatus.textContent = `${item.sourceLabel || 'Online'} aplicado. Revisa y guarda.`;
  showToast('Datos online aplicados');
}

function openEdit(id) {
  const item = catalog.find(i => i.id === id);
  if (!item) return;
  editingId = id;
  imageCleared = false;
  resetMetadataLookup();
  document.getElementById('modalTitle').textContent = 'Editar título';
  document.getElementById('f-title').value = item.title;
  linkInput.value = item.link || '';
  whoInput.value = item.who || '';
  imageUrlInput.value = item.image && !item.image.startsWith('data:') ? item.image : '';
  imageFileInput.value = '';
  setPreview(item.image || '');
  document.getElementById('f-category').value = item.category;
  document.getElementById('f-subtype').value = item.subtype || 'manga';
  document.getElementById('f-status').value = item.status || 'pendiente';
  document.getElementById('deleteBtn').style.display = 'inline-block';
  syncFormVisibility();
  renderProgressEditor();
  overlay.classList.add('show');
}

function openAdd() {
  editingId = null;
  imageCleared = false;
  form.reset();
  resetMetadataLookup();
  setPreview('');
  linkInput.value = '';
  whoInput.value = currentWho;
  document.getElementById('modalTitle').textContent = 'Añadir título';
  document.getElementById('f-category').value = currentTab === 'todo' ? 'series' : currentTab;
  document.getElementById('f-status').value = 'pendiente';
  document.getElementById('deleteBtn').style.display = 'none';
  syncFormVisibility();
  renderProgressEditor();
  overlay.classList.add('show');
}

function syncFormVisibility() {
  const cat = document.getElementById('f-category').value;
  const mode = CATS[cat].mode;
  document.getElementById('f-subtype-wrap').style.display = cat === 'lectura' ? 'block' : 'none';
  document.getElementById('f-seasons-hint').style.display = mode === 'seasons' ? 'block' : 'none';
  if (mode === 'seasons') {
    const hasLinearTrack = CATS[cat].tracks.some(t => t.linear);
    const names = CATS[cat].tracks.map(t => t.unitPlural).join(' y ');
    document.getElementById('f-seasons-hint').textContent = hasLinearTrack
      ? 'En la tarjeta marcas capitulos vistos. Aqui editas el total.'
      : `Configura aqui ${names} y el total de capitulos. En la tarjeta solo marcas avance.`;
  }
  document.getElementById('f-status').closest('.field').style.display = mode === 'seasons' ? 'none' : 'block';
  renderProgressEditor();
}

function renderProgressEditor() {
  const wrap = document.getElementById('f-progress-editor');
  if (!wrap) return;
  const category = document.getElementById('f-category').value;
  const cat = CATS[category];
  const item = editingId ? catalog.find(i => i.id === editingId) : null;
  if (cat.mode !== 'seasons') {
    wrap.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }

  const sections = cat.tracks.map(track => {
    if (track.linear) {
      const entry = item ? linearEntry(item, track.key) : { number: 1, total: null, watched: 0, status: 'pendiente' };
      return `
        <div class="progress-editor-row">
          <span>Total de capitulos</span>
          <input class="progress-total-input" type="number" min="0" value="${entry.total || ''}" data-progress-track="${track.key}" data-progress-num="1" placeholder="Sin total">
        </div>`;
    }

    const entries = item
      ? (item[track.key] || []).slice().sort((a, b) => a.number - b.number)
      : [{ number: 1, total: null, watched: 0, status: 'pendiente' }];
    const initial = track.unitWord[0];
    const rows = entries.map(entry => renderProgressEditorRow(track.key, initial, entry.number, entry.total || '')).join('');
    return `
      <div class="progress-editor-list" data-progress-list="${track.key}" data-progress-initial="${initial}">
        ${rows}
      </div>
      <button type="button" class="progress-editor-add" data-progress-add="${track.key}">+ Temporada</button>`;
  }).join('');

  wrap.style.display = 'block';
  wrap.innerHTML = `
    <label>Temporadas y capitulos</label>
    <div class="progress-editor-help">Define aqui cuantas temporadas tiene y el limite de capitulos por temporada.</div>
    ${sections}
  `;
  bindProgressEditorEvents();
}

function renderProgressEditorRow(trackKey, initial, number, total) {
  return `
    <div class="progress-editor-row" data-progress-row data-progress-track="${trackKey}" data-progress-num="${number}">
      <span>${initial}${number}</span>
      <input class="progress-total-input" type="number" min="0" value="${total || ''}" placeholder="Caps.">
      <button type="button" class="progress-editor-remove" data-progress-remove title="Eliminar temporada">×</button>
    </div>`;
}

function bindProgressEditorEvents() {
  document.querySelectorAll('[data-progress-add]').forEach(button => {
    button.onclick = () => {
      const trackKey = button.dataset.progressAdd;
      const list = document.querySelector(`[data-progress-list="${trackKey}"]`);
      if (!list) return;
      const initial = list.dataset.progressInitial || 'T';
      const nums = Array.from(list.querySelectorAll('[data-progress-row]')).map(row => +row.dataset.progressNum || 0);
      const next = nums.length ? Math.max(...nums) + 1 : 1;
      list.insertAdjacentHTML('beforeend', renderProgressEditorRow(trackKey, initial, next, ''));
      bindProgressEditorEvents();
    };
  });

  document.querySelectorAll('[data-progress-remove]').forEach(button => {
    button.onclick = () => {
      const row = button.closest('[data-progress-row]');
      if (row) row.remove();
    };
  });
}

function applyProgressEditorValues(data, category) {
  const cat = CATS[category];
  if (!cat || cat.mode !== 'seasons') return;

  cat.tracks.forEach(track => {
    if (track.linear) {
      const input = document.querySelector(`#f-progress-editor [data-progress-track="${track.key}"]`);
      const existing = data[track.key]?.[0] || { number: 1, watched: 0, status: 'pendiente' };
      const total = parseNonNegativeInt(input?.value || '') || null;
      const entry = { ...existing, number: 1, total };
      if (!entry.watched) entry.watched = 0;
      entry.total = total;
      updateLinearEntryProgress(entry);
      data[track.key] = [entry];
      return;
    }

    const existingEntries = data[track.key] || [];
    const rows = Array.from(document.querySelectorAll(`#f-progress-editor [data-progress-row][data-progress-track="${track.key}"]`));
    data[track.key] = rows.map((row, index) => {
      const number = +row.dataset.progressNum || index + 1;
      const total = parseNonNegativeInt(row.querySelector('.progress-total-input')?.value || '') || null;
      const existing = existingEntries.find(entry => entry.number === number);
      const entry = existing
        ? { ...existing, total }
        : { number, total, watched: 0, status: 'pendiente' };
      updateTrackEntryProgress(entry);
      return entry;
    }).sort((a, b) => a.number - b.number);
  });

  data.status = autoStatusFromTracks(data);
}

/* ============================================================
   EVENT BINDINGS
   ============================================================ */
document.getElementById('f-category').addEventListener('change', () => {
  syncFormVisibility();
  resetMetadataLookup();
});
metadataSearchBtn.addEventListener('click', searchOnlineMetadata);
document.querySelectorAll('[data-provider-search]').forEach(button => {
  button.addEventListener('click', () => openProviderSearch(button.dataset.providerSearch));
});
document.getElementById('f-title').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.ctrlKey) {
    e.preventDefault();
    searchOnlineMetadata();
  }
});
document.getElementById('openAdd').addEventListener('click', openAdd);
usersBtn.addEventListener('click', openUsersPanel);
document.getElementById('usersCancelBtn').addEventListener('click', () => userOverlay.classList.remove('show'));
userOverlay.addEventListener('click', e => { if (e.target === userOverlay) userOverlay.classList.remove('show'); });
userForm.addEventListener('submit', saveUser);
document.getElementById('logoutBtn').addEventListener('click', async () => {
  try {
    await fetch('/api/logout', { method: 'POST' });
  } finally {
    window.location.href = '/login';
  }
});
document.getElementById('cancelBtn').addEventListener('click', () => overlay.classList.remove('show'));
overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('show'); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    overlay.classList.remove('show');
    coverOverlay.classList.remove('show');
  }
});

imageFileInput.addEventListener('change', async () => {
  const file = imageFileInput.files[0];
  if (!file) return;
  imageCleared = false;
  const dataUrl = await fileToDataUrl(file);
  setPreview(dataUrl);
});

imageUrlInput.addEventListener('input', () => {
  imageCleared = false;
  if (imageUrlInput.value.trim()) setPreview(imageUrlInput.value.trim());
});

document.getElementById('clearImageBtn').addEventListener('click', () => {
  imageCleared = true;
  imageFileInput.value = '';
  imageUrlInput.value = '';
  setPreview('');
});

document.getElementById('deleteBtn').addEventListener('click', async () => {
  if (editingId && confirm('¿Eliminar este título del archivo?')) {
    catalog = catalog.filter(i => i.id !== editingId);
    overlay.classList.remove('show');
    render();
    await save('Eliminado');
  }
});

form.addEventListener('submit', async e => {
  e.preventDefault();
  const category = document.getElementById('f-category').value;
  const mode = CATS[category].mode;
  const existing = editingId ? catalog.find(i => i.id === editingId) : null;
  const uploadedImage = imageFileInput.files[0] ? await fileToDataUrl(imageFileInput.files[0]) : '';
  const imageUrl = imageUrlInput.value.trim();
  const image = imageCleared ? '' : uploadedImage || imageUrl || (existing ? existing.image || '' : '');

  const data = {
    title: document.getElementById('f-title').value.trim(),
    image,
    link: linkInput.value.trim(),
    category,
    subtype: category === 'lectura' ? document.getElementById('f-subtype').value : null,
    who: whoInput.value,
    updatedAt: Date.now(),
  };
  if (mode === 'watched') {
    data.status = document.getElementById('f-status').value;
  } else {
    data.status = existing ? autoStatusFromTracks({ ...existing, ...data }) : 'pendiente';
  }
  if (!data.title) return;

  if (editingId) {
    const idx = catalog.findIndex(i => i.id === editingId);
    if (mode === 'seasons') {
      CATS[category].tracks.forEach(t => {
        data[t.key] = t.linear ? [linearEntry(catalog[idx], t.key)] : catalog[idx][t.key] || [];
      });
      if (isLinearCategory(category)) data.volumes = [];
      applyProgressEditorValues(data, category);
    }
    catalog[idx] = { ...catalog[idx], ...data };
  } else {
    if (mode === 'seasons') {
      CATS[category].tracks.forEach(t => {
        data[t.key] = t.linear ? [{ number: 1, total: null, watched: 0, status: 'pendiente' }] : [];
      });
      if (isLinearCategory(category)) data.volumes = [];
      applyProgressEditorValues(data, category);
    }
    catalog.unshift({ id: uid(), ...data });
  }
  overlay.classList.remove('show');
  render();
  await save('Guardado ✓');
});

document.getElementById('search').addEventListener('input', e => {
  searchTerm = e.target.value;
  render();
});

document.getElementById('statusFilter').addEventListener('click', e => {
  if (!e.target.classList.contains('chip')) return;
  document.querySelectorAll('#statusFilter .chip').forEach(c => c.classList.remove('active'));
  e.target.classList.add('active');
  currentStatus = e.target.dataset.status;
  render();
});

document.querySelectorAll('.who-btn[data-who]').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.who-btn[data-who]').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    currentWho = b.dataset.who;
    render();
  });
});

/* ============================================================
   COVER BANNER MODAL
   ============================================================ */
document.getElementById('coverEditBtn').addEventListener('click', () => {
  if (currentTab === 'todo') return;
  document.getElementById('coverCatLabel').textContent = CATS[currentTab].label;
  coverUrlInput.value = covers[currentTab] && !covers[currentTab].startsWith('data:') ? covers[currentTab] : '';
  coverFileInput.value = '';
  coverOverlay.classList.add('show');
});
document.getElementById('coverCancelBtn').addEventListener('click', () => coverOverlay.classList.remove('show'));
coverOverlay.addEventListener('click', e => { if (e.target === coverOverlay) coverOverlay.classList.remove('show'); });

document.getElementById('coverSaveBtn').addEventListener('click', async () => {
  const uploadedCover = coverFileInput.files[0] ? await fileToDataUrl(coverFileInput.files[0]) : '';
  const url = coverUrlInput.value.trim();
  covers[currentTab] = uploadedCover || url || covers[currentTab] || '';
  coverOverlay.classList.remove('show');
  render();
  await saveCovers('Portada guardada ✓');
});

document.getElementById('coverRemoveBtn').addEventListener('click', async () => {
  covers[currentTab] = '';
  coverOverlay.classList.remove('show');
  render();
  await saveCovers('Portada eliminada');
});

/* ============================================================
   INIT
   ============================================================ */
async function init() {
  if (await loadSession()) {
    await loadProfiles();
    await load();
  }
}

init();
