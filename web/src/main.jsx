import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
// Type is bundled, so the app looks the same offline and on every machine.
// Archivo: variable weight AND width (62–125%) — body at 100%, display at 125%.
// Martian Mono: variable width — readouts run at 87.5%.
import '@fontsource-variable/archivo/wdth.css'
import '@fontsource-variable/archivo/wdth-italic.css'
import '@fontsource-variable/martian-mono/wdth.css'
import './index.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
