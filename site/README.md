# ja-media-toolkit Documentation Site

This directory contains the user-facing documentation for the `ja-media-toolkit`, built with **Astro** and **Starlight**.

## 🎯 Purpose & Scope

This site is the **Usage & Onboarding** layer. It is designed for:
- **End Users**: Step-by-step guides on how to install and run the tools.
- **LLMs/Agents**: A structured, readable source of truth for how the toolkit functions.

### ⚠️ Usage vs. Architecture
To keep the user experience clean, we separate the "How" from the "Why":
- **`site/` (This folder)**: Focuses on **Usage**. (e.g., "How do I run transcribe?")
- **`docs/` (Root folder)**: Focuses on **Architecture**. (e.g., "Why did we design the ASR boundary this way?")

## 🌐 Infrastructure & Routing

The site is deployed as a static build, typically served alongside the toolkit's services via **Caddy**.

### The Caddyfile
The [`Caddyfile](./Caddyfile)` in this directory defines the routing for the entire project ecosystem. It performs two critical roles:
1.  **Static Hosting**: Serves the built Astro site from `/usr/share/caddy`.
2.  **Unified API Gateway**: Implements the "Global Root URL" by reverse-proxying `/api/v1/*` requests to the appropriate backend containers (e.g., `anime-crosswalk` and `kitsunekko-subtitles`).

This allows clients to use a single base URL (e.g., `http://ja-media.local`) for both the documentation and all backend services.

---

## 🧞 Commands

All commands are run from the `site/` directory:

| Command                   | Action                                           |
| :------------------------ | :----------------------------------------------- |
| `npm install`             | Installs dependencies                            |
| `npm run dev`             | Starts local dev server at `localhost:4321`      |
| `npm run build`           | Build your production site to `./dist/`          |
| `npm run preview`         | Preview your build locally, before deploying     |
| `npm run astro ...`       | Run CLI commands like `astro add`, `astro check` |
| `npm run astro -- --help` | Get help using the Astro CLI                     |

## 📂 Project Structure

- `src/content/docs/`: All Markdown files for the guides.
- `public/`: Static assets (favicons, etc.).
- `src/assets/`: Images and assets embedded in docs.
