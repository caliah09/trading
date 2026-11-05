# Hosting the dashboard

The screener was built local-only, which is why it had no authentication: a
process bound to `127.0.0.1` is reachable only from your own machine. Putting
it on the internet changes the threat model entirely, so this guide covers the
password first and the deployment second.

> **The app refuses to start** with `--host 0.0.0.0` unless a password is
> configured. That is deliberate — an unauthenticated public bind is not
> something to allow through with a warning the operator may never read.

---

## 1. Set a password

```bash
python -m screener hash-password
```

It prompts twice, echoes nothing, and prints:

```
  SCREENER_PASSWORD_HASH=scrypt$3f9a...$8c21...
```

Put **that hash** in your host's environment, not the password. It is a one-way
scrypt hash, so someone who reads your environment variables cannot use what
they find to sign in anywhere. Minimum 12 characters is enforced — this guards
a URL anyone can reach.

Changing the hash signs out every existing session, because the session signing
key is derived from it.

| Variable | Purpose |
|---|---|
| `SCREENER_PASSWORD_HASH` | **Required for hosting.** Output of `hash-password`. |
| `SCREENER_PASSWORD` | Plaintext fallback, hashed on startup. Logs a warning. Prefer the hash. |
| `SCREENER_SECRET_KEY` | Optional explicit session signing key. Derived from the hash otherwise. |
| `SCREENER_SESSION_DAYS` | Session lifetime, default 7. |
| `SCREENER_INSECURE_COOKIE` | Set to `1` **only** for plain-HTTP testing. Drops the `Secure` flag. |
| `SCREENER_DB` | Database path. Point this at your mounted volume. |
| `PORT` | Listening port. Most hosts assign this automatically. |

---

## 2. Pick a host — and the honest version of the cost

The thing that decides this is **persistent storage**. The database holds your
price history, the implied-volatility series that IV Rank needs a year to
accumulate, and your journal decisions. Decisions are the only records here
that cannot be regenerated from market data. On a filesystem that resets, they
are gone on every restart.

| | Persistent disk | Idle behaviour | Realistic cost |
|---|---|---|---|
| **Fly.io** | Volumes available | Can suspend and auto-wake | Pay-as-you-go per second |
| **Render** | Disks require a paid instance | Free tier sleeps after ~15 min, ~1 min cold start | Free tier exists, but without a disk |
| **Railway** | Volumes available | Stays up | Trial credit, then a monthly plan |
| **Your own VM** | Yes | Stays up | Whatever the VM costs |

Free tiers have narrowed considerably. Expect roughly **$5/month** for a small
always-on instance with a volume. Treat any specific figure here as needing a
check against current pricing before you commit.

**If you want to stay free:** run on Render's free tier with no disk, and let
the GitHub Actions refresh own the data. Market data survives because the
workflow republishes it; journal decisions recorded in the hosted app do not
survive a restart. Record those locally instead.

---

## 3. Deploy

### Fly.io

```bash
fly launch --no-deploy                       # answer no to a database
fly volumes create screener_data --size 1
fly secrets set SCREENER_PASSWORD_HASH='scrypt$...'
fly deploy
```

`fly.toml` already mounts the volume at `/data`, forces HTTPS, and health-checks
`/health`. It pins **one machine**: two would each run the internal scheduler
and write the same SQLite file, which is how you corrupt it.

### Render

```bash
# render.yaml is in the repo. Create the service from it, then:
#   Dashboard → Environment → add SCREENER_PASSWORD_HASH
```

`render.yaml` requests a 1 GB disk, which needs a paid instance type. Remove the
`disk:` block to run free, and read the caveat above first.

### Railway

1. **New Project → Deploy from GitHub repo → your fork.**
2. **Settings → Source → set the branch.** Railway defaults to the repository's
   default branch; if yours is not `main`, set it explicitly or you will deploy
   the wrong code — or nothing.
3. **Add a Volume**, mount path `/data`.
4. **Variables → `SCREENER_PASSWORD_HASH`** (or `SCREENER_PASSWORD`).
5. **Settings → Networking → Generate Domain.**

Railway assigns `$PORT` and terminates TLS, so nothing else needs configuring.
`railway.json` in the repository points its health check at `/health` and pins
one replica — two would each run the internal scheduler and write the same
SQLite file, which is how you corrupt it.

#### When the health check fails

Build and Deploy succeeding while **Network → Healthcheck** fails means the app
never answered on its port. Open the deploy logs; the cause is almost always one
of these, and each has a distinct signature.

| In the logs | Cause | Fix |
|---|---|---|
| `REFUSING TO START` | No password configured. The app will not bind a public address unprotected. | Set `SCREENER_PASSWORD_HASH` (or `SCREENER_PASSWORD`) and redeploy. |
| `Cannot open the database at …` | The mounted volume is not writable. | Check the volume is mounted where `SCREENER_DB` points. |
| Nothing after the startup banner | The health check is probing a path that redirects. `/` returns 303 to the login page. | Point the health check at `/health`, which is unauthenticated and returns 200. |
| `ModuleNotFoundError` | Built from the wrong branch or without the Dockerfile. | Settings → Source → confirm the branch. |

The health check must use `/health`. Every other route redirects to the login
page when authentication is on, and a 3xx is not a pass.

> **You do not need `RAILWAY_RUN_UID=0`.** Railway's deploy diagnosis suggests
> it when the healthcheck fails with `unable to open database file`, and it does
> work — by running the whole container as root. The entrypoint handles the same
> problem properly: it starts as root, takes ownership of the mounted volume,
> then drops to an unprivileged user before starting the app. If you set that
> variable during an earlier failed deploy, remove it.

### Plain Docker, anywhere

```bash
docker build -t screener .
docker run -d -p 8765:8765 \
  -e SCREENER_PASSWORD_HASH='scrypt$...' \
  -v screener-data:/data \
  screener
```

Put it behind a reverse proxy that terminates TLS. For local testing over plain
HTTP, add `-e SCREENER_INSECURE_COOKIE=1`, because a `Secure` cookie will not be
sent over HTTP and sign-in will appear to silently fail.

### Volume ownership, and why the container starts as root

Chowning `/data` at build time is not enough. A platform that mounts a volume
there replaces the directory with a fresh root-owned one, so the build-time
ownership is irrelevant and an unprivileged process cannot create the database.
It surfaces as `sqlite3.OperationalError: unable to open database file` — a
message that names neither the path nor the cause.

`docker-entrypoint.sh` therefore starts as root, takes ownership of whatever is
mounted at the database's directory, and only then drops to the `screener` user
with `setpriv` before exec'ing the app. **The application process is still
unprivileged**; only the few milliseconds of setup are not.

If a platform forces the container to start non-root, the entrypoint detects
that and runs anyway, and the app's startup error explains exactly what it found
— which user it is running as, who owns the directory, and what to change.

---

## 4. What hosting changes about the schedule

An always-on hosted instance runs the **internal scheduler** itself, at market
open and close, so the GitHub Actions workflow becomes belt-and-braces rather
than essential.

Keep the workflow if your host sleeps when idle — a sleeping service never fires
its timer, and the Action will keep the data branch current regardless.

Do **not** run both the hosted scheduler and the Action writing to the same
database, unless the Action is only publishing to the data branch (which is what
it does) and the host reads from its own volume. Two writers on one SQLite file
is the thing to avoid.

---

## 5. Security notes

What the login does:

- **scrypt** hashing, ~70 ms per verification, salted per password.
- **Signed stateless session tokens**, so they survive a restart with no
  server-side store. The token carries an expiry and an HMAC over it, and a
  tampered token fails verification.
- **`HttpOnly`, `SameSite=Lax`, `Secure` cookies.** JavaScript cannot read the
  session.
- **Per-client login throttling** — 8 failures in 15 minutes and that client is
  blocked, because one password on a public URL is otherwise guessable at
  network speed.

What it does **not** do, so you can judge the risk yourself:

- **No multi-user support.** One password, one role, full access.
- **No 2FA.**
- **No audit log** of logins beyond application log lines.
- **No CSRF tokens.** `SameSite=Lax` is the only cross-site protection, which is
  adequate for a single-user tool but is not defence in depth.

Given that, keep the URL unadvertised, use a long unique password, and do not
reuse one from elsewhere.

---

## 6. If your repository is private

Making the repository private is the right call if you would rather your
watchlist not be public. Two consequences:

- **GitHub Pages stops working** unless you are on a paid plan. That only
  affected the rendered README, not the app.
- **Actions keeps working.** Private repositories get a monthly allowance of
  free Actions minutes, and two refreshes a day is a small fraction of it.

The `screener-data` branch becomes private with the repository, so published
rankings are visible only to you.
