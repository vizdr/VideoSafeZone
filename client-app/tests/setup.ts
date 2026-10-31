// What jsdom lacks and the UI library uses: matchMedia (OpenVue's dropdowns ask it whether the
// screen is narrow). Answers "doesn't match", as a desktop browser would for a phone query.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  }) as MediaQueryList;
}
