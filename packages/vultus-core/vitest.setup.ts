import '@testing-library/jest-dom';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

// The action hooks (useAction/useActionList) call react-i18next's
// useTranslation. Initialise a minimal instance so `t` resolves (keys echo
// through — tests assert on prop-supplied labels, not translated strings).
if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
    lng: 'en', fallbackLng: 'en', resources: {}, interpolation: { escapeValue: false },
  });
}
