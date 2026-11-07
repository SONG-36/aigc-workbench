import {StrictMode} from 'react'
import {createRoot} from 'react-dom/client'
import {Showcase} from './Showcase'
import {publicCases} from './content'
import './showcase.css'

createRoot(document.getElementById('root')!).render(<StrictMode><Showcase cases={publicCases}/></StrictMode>)
