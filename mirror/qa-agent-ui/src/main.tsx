import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TooltipPrimitive.Provider delayDuration={300} skipDelayDuration={300}>
      <App />
    </TooltipPrimitive.Provider>
  </StrictMode>,
)
