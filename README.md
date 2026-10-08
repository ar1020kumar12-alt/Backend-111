# Student Hub Backend - Netlify + Render

Backend/data layer for Student Hub.

Supports:
- online player profiles
- XP and levels
- global leaderboard
- player search
- friend requests and friends
- 1v1 learning challenges and scores
- player-to-player chat
- SQLite fallback for testing
- PostgreSQL via DATABASE_URL

## Render deployment
Upload these files to the ROOT of a GitHub repository:
- main.py
- requirements.txt
- render.yaml
- README.md
- API_CONNECT.js

Render settings:
Build: `pip install -r requirements.txt`
Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

After deployment, open your Render URL plus `/health`.
It should return `{"ok":true}`.

## Netlify connection
After Render gives you a URL, open API_CONNECT.js and replace:
`https://YOUR-RENDER-SERVICE.onrender.com`
with your real Render URL.
Then use `studentHubAPI('/leaderboard')`, etc., from your frontend JavaScript.

## Database
SQLite is included for testing. For persistent online data, use PostgreSQL and set Render's DATABASE_URL environment variable. Never put a database password in Netlify frontend code.

## Security
This is an MVP multiplayer backend using client IDs. Before a large public launch, add proper authentication, password hashing, rate limits, moderation, and a production database.
