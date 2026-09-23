import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':'http://127.0.0.1:8000'}},build:{chunkSizeWarningLimit:1200,rollupOptions:{output:{manualChunks(id){if(id.includes('node_modules/maplibre-gl/'))return 'maplibre';if(/node_modules\/(?:@deck\.gl|@luma\.gl|@loaders\.gl|@math\.gl)\//.test(id))return 'deck';}}}}});
