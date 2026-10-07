// Small shared runtime used by views: navigation, view-scoped polling, theme preference.
import { hydrateIcons } from './ui.js';

const timers = new Set();

/** setInterval that is automatically cleared when the user navigates away. */
export function poll(fn, ms) {
  const id = setInterval(fn, ms);
  timers.add(id);
  return id;
}

export function clearPolls() {
  timers.forEach(clearInterval);
  timers.clear();
}

export function navigate(path) {
  location.hash = `#${path}`;
}

export function getThemePref() {
  return localStorage.getItem('kobits.theme') || 'system';
}

export function applyTheme() {
  const pref = getThemePref();
  const dark = pref === 'dark' || (pref === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.querySelector('[data-icon]').dataset.icon = dark ? 'sun' : 'moon';
    document.getElementById('theme-label').textContent = dark ? 'Light mode' : 'Dark mode';
    hydrateIcons(btn);
  }
}

export function setThemePref(pref) {
  localStorage.setItem('kobits.theme', pref);
  applyTheme();
}
