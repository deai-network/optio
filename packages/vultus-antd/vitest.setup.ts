import '@testing-library/jest-dom';
// Mirror production: antd 5 static methods (Modal.confirm, message.*) need the
// React 19 compatibility patch, otherwise they no-op under React 19 + antd 5.
import '@ant-design/v5-patch-for-react-19';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
    lng: 'en', fallbackLng: 'en', resources: {}, interpolation: { escapeValue: false },
  });
}

// jsdom implements neither matchMedia (antd Form's responsive Row/Col use it)
// nor ResizeObserver — stub both so components can mount.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}
