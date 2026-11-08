/* Theme choice, saved in this browser only. The look itself lives in themes.css;
   public/index.html applies the saved theme before the app paints (keep its ids in step). */

export const STORAGE_KEY = 'journal-theme';

export const THEMES = [
  {
    id: 'current',
    name: 'Original',
    description: 'Navy and amber, the journal as it has always looked.',
    swatches: ['#0A0E12', '#F0A93B', '#37D399', '#FF4D5E'],
    browserColor: '#12161C',
  },
  {
    id: 'minimal',
    name: 'Minimal',
    description: 'Black on near-white with hairlines. Colour only where it means something.',
    swatches: ['#FAFAF9', '#111111', '#16794A', '#C42B2B'],
    browserColor: '#FAFAF9',
  },
  {
    id: 'natural',
    name: 'Natural',
    description: 'Warm paper, plum buttons and a book serif.',
    swatches: ['#F3EEE4', '#6A4C7A', '#2A6A40', '#A9372B'],
    browserColor: '#F3EEE4',
  },
  {
    id: 'terminal',
    name: 'Pro terminal',
    description: 'True black, square and dense, with amber headings and a step equity line.',
    swatches: ['#000000', '#FFA028', '#3FD68F', '#FF5A4F'],
    browserColor: '#000000',
  },
];

const known = (id) => THEMES.some((t) => t.id === id);

/* The theme the page is showing right now (what the picker should mark as selected). */
export function activeTheme() {
  const id = document.documentElement.getAttribute('data-theme');
  return known(id) ? id : 'current';
}

export function getTheme() {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return known(saved) ? saved : 'current';
  } catch (e) {
    return 'current';
  }
}

/* Puts the theme on <html>. Original is the absence of the attribute. */
export function applyTheme(id) {
  const theme = THEMES.find((t) => t.id === id) || THEMES[0];
  const root = document.documentElement;
  if (theme.id === 'current') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme.id);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme.browserColor);
  return theme.id;
}

export function setTheme(id) {
  const applied = applyTheme(id);
  try {
    if (applied === 'current') window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, applied);
  } catch (e) {
    /* storage blocked: the theme still applies for this visit */
  }
  return applied;
}

/* Other open journal tabs follow a change made in this one. */
export function watchOtherTabs() {
  const onStorage = (e) => { if (e.key === STORAGE_KEY) applyTheme(getTheme()); };
  window.addEventListener('storage', onStorage);
  return () => window.removeEventListener('storage', onStorage);
}

/* Is the active theme drawn with steps (Pro terminal)? Read at render; pages remount. */
export const steppedCurve = () =>
  typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme') === 'terminal';
