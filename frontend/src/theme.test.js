import { THEMES, getTheme, setTheme, applyTheme, activeTheme, steppedCurve, watchOtherTabs, STORAGE_KEY } from './theme';

afterEach(() => {
  jest.restoreAllMocks();
  window.localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

test('Current is the default and is the absence of the attribute', () => {
  expect(getTheme()).toBe('current');
  setTheme('terminal');
  expect(document.documentElement.getAttribute('data-theme')).toBe('terminal');
  setTheme('current');
  expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
});

test('every theme has the fields the picker shows', () => {
  for (const t of THEMES) {
    expect(t.name).toBeTruthy();
    expect(t.description).toBeTruthy();
    expect(t.swatches.length).toBeGreaterThan(1);
  }
});

test('an unknown saved id falls back to Current', () => {
  window.localStorage.setItem(STORAGE_KEY, 'neon');
  expect(getTheme()).toBe('current');
  expect(applyTheme('neon')).toBe('current');
});

test('blocked storage does not break choosing a theme', () => {
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  expect(getTheme()).toBe('current');
  expect(setTheme('minimal')).toBe('minimal');
  expect(document.documentElement.getAttribute('data-theme')).toBe('minimal');
});

test('only Pro terminal draws the equity curve as steps', () => {
  setTheme('natural');
  expect(steppedCurve()).toBe(false);
  setTheme('terminal');
  expect(steppedCurve()).toBe(true);
});

test('a change saved in another tab is applied here', () => {
  const stop = watchOtherTabs();
  try {
    window.localStorage.setItem(STORAGE_KEY, 'natural');
    window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('natural');
  } finally {
    stop();
  }
});

test('the picker reflects the page, even when storage is blocked', () => {
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  setTheme('minimal');
  expect(activeTheme()).toBe('minimal');
});
