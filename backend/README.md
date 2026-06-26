# Backend (Robyn)

## Features implemented in this brick

- Wallet auth nonce issue endpoint
- Wallet signature verification endpoint
- Session lookup and logout endpoints
- Configurable TTL and env-based settings

## Local run

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m app.main
```


## Module-first backend structure

- `app/modules/auth/`: wallet auth brick (api, services, models, utils)
- `app/modules/news/`: news-facing API brick
- `app/core/`: shared runtime configuration
