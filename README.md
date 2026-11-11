# CivicDesk — AI-Based Smart Grievance Management System

CivicDesk is a full-stack educational prototype for submitting, classifying, tracking, and managing institutional grievances. It combines a Flask web application, SQLite persistence, role-based access, a TF-IDF + Logistic Regression classifier, optional email notifications, and admin analytics.

> **Prototype notice:** This is a portfolio/learning project, not a production-ready institutional complaint service. Do not enter real sensitive information. Before real deployment, complete a privacy, security, accessibility, and operational review.

## Features

- Student registration, sign-in, and personal dashboard
- AI category prediction using the existing scikit-learn pipeline
- Persistent SQLite complaint records with unique tracking IDs
- Complaint status timeline and admin status updates
- Admin dashboard with department/status filters
- Analytics charts for categories and statuses
- Transparent keyword-based priority heuristic (not a trained urgency model)
- SMTP notifications to departments and students (best-effort)\n- Signed, expiring password-reset and email-verification links\n- Resolution feedback (1–5 stars)\n- Model confidence display when the classifier supports predict_proba\n- Conservative duplicate suggestion for similar unresolved complaints\n- Monthly analytics and average feedback rating
- Password hashing, CSRF protection, role checks, input validation
- Health endpoint and automated smoke tests

## Architecture

```text
Student portal ─────┐
                    ├── Flask routes + authentication ── SQLite
Admin portal ───────┘              │
                                   ├── ML classifier (TF-IDF + Logistic Regression)
                                   ├── Status history / analytics
                                   └── Optional SMTP notifications
```

## Database design

| Table | Purpose | Important fields |
|---|---|---|
| `user` | Student, global admin, and department admin accounts | `id`, `name`, `email`, `password_hash`, `role`, `department` |
| `complaint` | One row per submitted grievance | `tracking_id`, `user_id`, `complaint_text`, `category`, `priority`, `status`, timestamps |
| `status_history` | Append-only status events | `complaint_id`, `status`, `note`, `changed_by`, `created_at` |

Relationships:
- One user can submit many complaints.
- One complaint can have many status-history events.
- Student users can only access their own complaints.
- Global admins can view and update all complaints.\n- Department admins can only view and update complaints classified into their assigned department.

SQLite is created automatically at `instance/civicdesk.sqlite` on first app start. Back up this file if you need to preserve local records. `db.create_all()` creates missing tables but does not migrate existing schemas; for future schema changes, add Flask-Migrate/Alembic migrations.

## Quick start (Windows PowerShell)

```powershell
# Extract this ZIP, then open the project folder in VS Code
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 1. Keep your trained model

If you already trained the previous CivicDesk classifier, copy:

```text
old-project\models\grievance_pipeline.joblib
```

to:

```text
civicdesk-fullstack\models\grievance_pipeline.joblib
```

If you do not have that file in this folder, train it again using the dataset:

```powershell
python train_model.py
```

The supplied dataset is at `data/complaints_dataset.xlsx`. The vectorizer is part of the saved sklearn Pipeline; no separate vectorizer file is required.

### 2. Configure environment

```powershell
Copy-Item .env.example .env
```

Open `.env` and set a strong random `SECRET_KEY`. For example, generate one with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Set `COOKIE_SECURE=0` for local HTTP development. Set it to `1` only when the deployed site uses HTTPS.

### 3. Start the app

```powershell
python app.py
```

Open http://127.0.0.1:5000. The SQLite database and tables are created automatically.

### 4. Create an administrator

In PowerShell, set admin values for the current terminal session (replace all example values):

```powershell
$env:ADMIN_NAME="CivicDesk Administrator"
$env:ADMIN_EMAIL="admin@your-college.edu"
$env:ADMIN_PASSWORD="Use-A-Long-Unique-Password-Here"
flask --app app create-admin
```

Then sign in using that admin email and password. This creates a global administrator with access to all departments.

### Create a department administrator

Set the same account variables, plus the department name:

```powershell
$env:ADMIN_NAME="Hostel Department Admin"
$env:ADMIN_EMAIL="hostel-admin@your-college.edu"
$env:ADMIN_PASSWORD="Use-A-Different-Long-Password"
$env:ADMIN_DEPARTMENT="Hostel"
flask --app app create-department-admin
```

`ADMIN_DEPARTMENT` must exactly match one of: `Academics`, `Canteen`, `Hostel`, `Library`, `Transport`, or `Others`.

Department administrators are restricted server-side to their assigned category: their dashboard, analytics, detail pages, and update actions cannot access other departments. Global admins retain institution-wide access. The public registration page only creates student accounts.

Do not hardcode admin credentials in source code or commit them to Git.

### 5. Optional SMTP email

Set `SMTP_USERNAME`, `SMTP_PASSWORD`, and the matching `DEPARTMENT_EMAIL_*` recipient in `.env`. For Gmail, use an app password with 2-Step Verification; never use your normal account password. Email is best-effort and is not proof of institutional receipt.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Additional account and email features

- Password reset: `/forgot-password`; reset links expire after one hour.
- Email verification: `/verify-email/<token>`; links expire after 24 hours.
- Set `REQUIRE_EMAIL_VERIFICATION=1` to require verification before login. Keep it `0` for local development without SMTP.
- Student notifications are sent on submission and status changes when SMTP is configured.
- Duplicate detection compares recent unresolved grievances in the same predicted category using TF-IDF cosine similarity. It only warns; it never blocks a submission.
- Priority remains a transparent keyword heuristic, not an ML urgency model. Confidence is the classifier's maximum class probability, not necessarily calibrated confidence.

## Deploy to Render

1. Push the project to a GitHub repository.
2. In Render, create a Blueprint from the repository and use `render.yaml`, or create a Python web service and PostgreSQL database manually.
3. Configure `SECRET_KEY`, `DATABASE_URL`, `MODEL_PATH`, SMTP credentials, and department recipient addresses in the service environment.
4. Add your trusted model artifact as a secure deployment asset or retrain during build from a properly licensed dataset. Do not commit private data or secrets.
5. Run the `create-admin` / `create-department-admin` CLI commands through a one-off shell or deploy shell.
6. Verify `/health`, sign-in, database persistence, email delivery, and access-control tests.

Render's local filesystem is not a durable database location. Use PostgreSQL for deployed persistence; SQLite is intended for local development. Review current hosting plan limits and pricing before deployment.

## Routes

| Route | Access | Purpose |
|---|---|---|
| `/` | Public | Landing page |
| `/register`, `/login` | Public | Student account |
| `/dashboard` | Signed in | Student dashboard / admin redirect |
| `/submit` | Student | Submit grievance |
| `/track` | Student | Look up own tracking ID |
| `/complaints/<tracking_id>` | Owner or admin | Details and timeline |
| `/admin` | Admin | Manage/filter grievances |
| `/admin/analytics` | Admin | Charts and counts |
| `/health` | Public | App/model readiness |

## Priority logic

The current priority is a simple transparent keyword heuristic. Terms such as `fire`, `unsafe`, `harassment`, or `emergency` result in `Urgent`; selected service/safety keywords result in `High`; otherwise it is `Normal`. This is not an ML urgency prediction, may miss context, and must not replace human triage or emergency services.

## Model evaluation

The training script uses a stratified holdout split and fits TF-IDF inside an sklearn `Pipeline` to avoid train/test vocabulary leakage. The reported accuracy is only a dataset-specific holdout result. Evaluate on genuinely new, representative complaints before making performance claims.

## Security and privacy

- Passwords are stored as Werkzeug password hashes, never plaintext.
- CSRF protection is enabled on POST forms.
- Students can only access their own grievance records.
- Admin creation is a CLI operation; public signup cannot select the admin role.
- Keep `.env`, the SQLite database, real complaint data, and model artifacts out of public Git.
- Revoke any credential previously exposed in an old source file or Git commit.
- Use HTTPS, secure cookies, backups, retention/deletion rules, rate limiting, monitoring, and a proper privacy notice before deployment.
- Only load trusted scikit-learn/joblib model artifacts; pickle-based formats can execute code when loaded.

## Known limitations / roadmap

- Improve classification using more diverse, real-world complaint examples.
- Add human review for uncertain model predictions.
- Improve model evaluation using unseen and deduplicated complaints.
- Add database migration history using Flask-Migrate.
- Improve analytics with resolution-time and service-level metrics.
- Add file attachments and a student notification inbox. 
## License

MIT License. See `LICENSE`.
