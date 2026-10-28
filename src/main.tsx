import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import SessionGate from './SessionGate';
import './style.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><SessionGate><App/></SessionGate></React.StrictMode>);
import './prototype.css';
import './events.css';
import './research-dock.css';
