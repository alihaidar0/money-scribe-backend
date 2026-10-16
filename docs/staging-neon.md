# Neon for staging, step by step

The database of staging: how the Neon project was created, what every name is, the exact commands, and what each one
should print. It is step 1 of the one-time setup in [`staging.md`](staging.md); the decision is in
[ADR 0011](adr/0011-staging-hosting.md). It takes about 20 minutes. Neon's console wording changes from time to time, so
follow the purpose of each step if a label differs.

## Rules for the secrets

- Three secrets come out of this page: the password of the app role, the direct connection string (it contains that
  password), and the passphrase of the admin account. Keep them in a password manager. The password of the project owner
  role (`neondb_owner`) is a fourth; it is not used by the app.
- Never put a secret in a file, a chat, a screenshot, an issue or a pull request. A screenshot of the connection dialog is
  safe only while the password stays masked.
- A command that needs the connection string reads it **silently** (`read -rs`), so it is not echoed and not saved in the
  shell history. Do not type it inline after `DATABASE_URL=`.
- If a secret leaks, reset the role's password in Neon (Roles, the three dots, reset password), then update the password
  manager and the `DATABASE_URL` of the Render service.

## What you create

| What | Name or value | Why |
| --- | --- | --- |
| Account | your own email, two-factor sign-in on | The database holds the staging data and its credentials |
| Project | `money-scribe-staging` | One project for staging; production gets its own later |
| Region | AWS Europe Central 1 (Frankfurt) | It must match the region of the Render services |
| PostgreSQL version | 18 | The version of our tests and local stack |
| Default branch | `production` (created by Neon) | Neon's name for the main branch; we use no other |
| App role | `money_scribe_app` | The login of the app, separate from the project owner |
| App database | `money_scribe_staging`, owned by `money_scribe_app` | Django's migrations create tables, so the app's role must own it |
| Connection string | direct (pooling off), no `-pooler` in the host | Django's persistent connections are built for it |

The default database `neondb` and the owner role `neondb_owner` stay as they are and are not used by the app.

## Step 1: the account

1. Sign up at neon.com with your own email.
2. Open the account settings and turn on two-factor sign-in.

## Step 2: the project

1. Click **New project** (**Create project**). Fill in:

   | Field | Value |
   | --- | --- |
   | Project name | `money-scribe-staging` |
   | Region | AWS Europe Central 1 (Frankfurt) |
   | Postgres database | on |
   | Database name | empty (the default, `neondb`) |
   | Postgres version | 18 |
   | Object storage, Functions, AI gateway, Realtime | off (scroll the list: every other service is off too) |

   The dialog shows the free plan for that region: 1 GB of storage, the compute scales to zero when inactive,
   autoscaling up to 2 CU, 10 branches per project.
2. Click **Create project**. The next screen offers an "agent prompt" that installs Neon's command-line tool and writes
   connection settings into a dotenv file. **Do not use it.** We set everything up by hand. Click **Go to project**.
3. The project page (Branch overview) shows the branch `production` as default, the plan **Free plan**, the region,
   one branch, the compute range 0.25 to 2 CU and a history retention of 6 hours. Ignore the rows "Better Auth",
   "Object storage", "Functions", "AI Gateway" and "Realtime", and the "Onboard your agent" panel.

## Step 3: the app role

1. In the left menu, under **Postgres database**, open **Roles**. It lists the default role `neondb_owner`.
2. Click **Add role**, keep the branch `production`, name it `money_scribe_app` and click **Create**.
3. Neon shows the password of the new role **once**. Copy it into the password manager at once. If it was lost, use the
   role's menu to reset the password and save the new one.

Roles created in the console are members of Neon's administrative group, so this role is not low-privilege. For staging
with invented data that is accepted. Production gets a restricted role created with SQL (section 12 of the plan).

## Step 4: the app database

1. Open **Databases** in the same submenu and click **Add database**.
2. Set the branch to `production`, the name to `money_scribe_staging` and the **owner to `money_scribe_app`** (not
   `neondb_owner`). Click **Create**.
3. The list now shows two databases:

   | Database | Owner |
   | --- | --- |
   | `neondb` | `neondb_owner` |
   | `money_scribe_staging` | `money_scribe_app` |

## Step 5: the direct connection string

1. Click **Connect** at the top of the left menu.
2. Select the branch `production`, the compute `Primary`, the database `money_scribe_staging` and the role
   `money_scribe_app`.
3. **Switch Connection pooling off.** With pooling on, the host contains `-pooler`. The direct host looks like this, with
   invented values:

   ```text
   ep-example-123456.c-8.eu-central-1.aws.neon.tech
   ```

4. Keep the form of the string, `postgresql://...`. It ends with `?sslmode=require&channel_binding=require`; both are
   right, and the driver supports `channel_binding`. If a later test fails with an error that mentions it, remove that
   part.
5. Use **Copy snippet** and paste the whole string into the password manager as "Neon staging DATABASE_URL (direct)".
   Do not click **Show password**.

## Step 6: test the connection

Run this in the terminal of the development container. It asks for the string silently (paste it, nothing is shown,
press Enter), connects to Neon and prints what it reached:

```bash
read -rs -p "Neon direct connection string: " NEON_URL; echo
DATABASE_URL="$NEON_URL" python - <<'PY'
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("select version(), current_database(), current_user")
    print(cursor.fetchone())
PY
unset NEON_URL
```

Expected: one line that starts with `PostgreSQL 18`, then `'money_scribe_staging'` and `'money_scribe_app'`. The first call
can take a few seconds while the compute wakes.

## Step 7: apply the migrations

```bash
read -rs -p "Neon direct connection string: " NEON_URL; echo
DATABASE_URL="$NEON_URL" python manage.py migrate
unset NEON_URL
```

Expected: `Apply all migrations: admin, auth, contenttypes, django_celery_beat, outbox, sessions, users`, then one
`Applying ... OK` line per migration, including `users.0002_sessions_and_email_codes`, and no error. This proves the
string, the privileges of the app role and our migrations on PostgreSQL 18. The first deploy then has nothing to
migrate.

## Step 8: the admin account

```bash
read -rs -p "Neon direct connection string: " NEON_URL; echo
DATABASE_URL="$NEON_URL" python manage.py createsuperuser
unset NEON_URL
```

It asks for the email address (the admin sign-in name), the name, and the password twice; the typing is not shown. The
password must pass the same policy as every user's: at least 15 characters, not similar to the email or the name, not a
common or leaked one. A refusal such as "The password is too similar to the email address" is the policy working: answer
`n` to "Bypass password validation" and choose another passphrase. The last line is `Superuser created successfully.`
Save the passphrase in the password manager. The admin panel itself exists only once the app runs on Render, so the
sign-in comes later.

## What the free plan does

- The compute suspends after a period without queries and wakes on the next one; the first query after a pause is
  slower. Our database settings (persistent connections with health checks) reconnect by themselves.
- Limits as shown at creation: 1 GB of storage, autoscaling up to 2 CU, 10 branches per project, a 6-hour history for
  restoring. That is enough for invented data and not a backup strategy; production needs real backups before it holds real
  data.

## If something fails

| Symptom | Likely cause |
| --- | --- |
| `password authentication failed` | The password was copied wrongly, or the string is an old one. Reset the role's password, copy the new string |
| `database "money_scribe_staging" does not exist` | The database was not created, or the string names another one |
| `permission denied` while migrating | The database is not owned by `money_scribe_app` (step 4) |
| A timeout | Outbound port 5432 is blocked on your side; try another network |
| An error that mentions `channel_binding` | Remove `&channel_binding=require` from the end of the string |
| The host contains `-pooler` | Pooling is on; switch it off in the Connect dialog (step 5) |

## Where the string goes next

Into the environment of the Render web service as `DATABASE_URL` ([`staging.md`](staging.md), step 4), and nowhere else.
