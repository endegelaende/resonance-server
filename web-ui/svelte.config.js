import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
const config = {
    preprocess: vitePreprocess(),

    kit: {
        adapter: adapter({
            pages: "build",
            assets: "build",
            fallback: "index.html",
            precompress: false,
            strict: true,
        }),
        paths: {
            base: "",
        },
        prerender: {
            handleHttpError: ({ path, referrer, message }) => {
                // Ignore missing favicon - it's optional
                if (path === "/favicon.png") {
                    return;
                }
                // Throw for other errors
                throw new Error(message);
            },
        },
    },
};

export default config;
