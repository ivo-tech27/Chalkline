# Chalkline

A College Football Playoff bracket and scenario builder that installs on your iPhone like an app.

- **Bracket:** the 12-team field under the current rules. The five highest-ranked conference champions get automatic bids, every team is seeded by ranking, and the top four get byes. Pick winners or simulate the rest.
- **Top 25 & bubble:** reorder the poll, crown upset conference champions, and track teams outside the rankings.
- **Head-to-head:** compare any two teams, with model win chances.
- **Scores:** log results to update records and shake up the poll.
- **My team (optional):** the app takes on your school's colors.

## Install on iPhone

Open the site in Safari, tap **Share**, then **Add to Home Screen**.

## Data

`docs/data.json` is refreshed every morning by `.github/workflows/chalkline-data.yml`, which pulls the AP Top 25, or the CFP committee rankings once they're released, plus final scores from ESPN's public feed. Run it by hand from the **Actions** tab with **Run workflow**.

## Hosting

GitHub Pages: **Settings → Pages → Deploy from a branch → `main` / `/docs`**.
