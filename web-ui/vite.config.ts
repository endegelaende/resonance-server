import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit()
	],
	server: {
		proxy: {
			// Proxy API requests to the Python backend during development
			'/jsonrpc.js': 'http://localhost:9000',
			'/jsonrpc': 'http://localhost:9000',
			'/cometd': 'http://localhost:9000',
			'/api': 'http://localhost:9000',
			'/stream.mp3': 'http://localhost:9000',
			'/health': 'http://localhost:9000'
		}
	}
});
