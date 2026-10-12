import {defineConfig, loadEnv} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.GEOAGENT_API_TARGET || env.VITE_API_TARGET || "http://127.0.0.1:8000";
  const proxy = {target:apiTarget,changeOrigin:false};
  return {plugins:[react()],server:{proxy:{'/api':proxy,'/mcp':proxy,'/healthz':proxy}},build:{chunkSizeWarningLimit:1200,rollupOptions:{output:{manualChunks(id){if(id.includes('node_modules/maplibre-gl/'))return 'maplibre';if(/node_modules\/(?:@deck\.gl|@luma\.gl|@loaders\.gl|@math\.gl)\//.test(id))return 'deck';}}}}};
});
