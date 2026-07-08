# Golf Player Dashboard

Single-file GitHub Pages dashboard for PGA Tour player cards and stats.

Open `index.html` locally or view the published GitHub Pages site after deployment.

## Data updates

GitHub Actions runs `.github/workflows/update-golf-data.yml` every Monday at 11:30 UTC and can also be started manually from the Actions tab.

The workflow runs `collect_golf_stats.py`, syncs the generated stats into `index.html` with `sync_dashboard_data.py`, and commits the updated dashboard files when data changes.
