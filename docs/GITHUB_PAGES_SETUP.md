# GitHub Pages Setup (modaitrader)

This repo now includes a landing page in `docs/`:

- `docs/index.html`
- `docs/styles.css`
- `docs/app.js`
- `docs/.nojekyll`
- `docs/assets/*`
- `docs/screenshots/*`

## Enable Pages in GitHub

1. Open repository settings:
   - `https://github.com/WeAreTheArtMakers/modaitrader/settings/pages`
2. In **Build and deployment**:
   - **Source**: `Deploy from a branch`
   - **Branch**: `main`
   - **Folder**: `/docs`
3. Click **Save**.
4. Wait 1-5 minutes for first deploy.

## Expected Site URL

- `https://wearetheartmakers.github.io/modaitrader/`

## Update Flow

- Edit files under `docs/`.
- Commit and push to `main`.
- GitHub Pages redeploys automatically.
