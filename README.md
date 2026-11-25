# MailPilot – Email Marketing Automation (PY-AT-001)

A Flask-based email marketing automation platform using SQLAlchemy and MySQL-compatible database configuration. SQLite is the zero-configuration local default.

## Features
- Account registration, login, password hashing, protected pages
- Subscriber CRUD status management, search, CSV import with explicit consent checks
- Segments and subscriber membership
- HTML email templates
- Campaign drafts, immediate sending, scheduling and recipient records
- SMTP delivery or safe console-preview mode
- Open pixel and HTTP(S) click tracking
- Analytics dashboard
- Delayed automation sequences and CLI scheduler
- Automated tests

## Requirements
Python 3.10+ recommended. MySQL 8 is optional for local setup; SQLite works immediately.

## Windows setup
```powershell
cd Email_Marketing_Automation
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```
Put the generated value into `SECRET_KEY` in `.env`. Keep `.env` private.

Start the app:
```powershell
flask --app app run --debug
```
Open http://127.0.0.1:5000 and register an account. Initialize the database tables once:
```powershell
flask --app app init-db
```
Then run the server.

## MySQL setup
Create database and user in MySQL:
```sql
CREATE DATABASE email_marketing_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'email_user'@'localhost' IDENTIFIED BY 'CHANGE_THIS_PASSWORD';
GRANT ALL PRIVILEGES ON email_marketing_db.* TO 'email_user'@'localhost';
FLUSH PRIVILEGES;
```
Set in `.env`:
```env
DATABASE_URL=mysql+pymysql://email_user:CHANGE_THIS_PASSWORD@localhost/email_marketing_db?charset=utf8mb4
```
Restart the app. Use URL-encoded characters if the password contains reserved URL characters.

## Email delivery
Default `MAIL_MODE=console` prints a preview to the server terminal; it does not deliver external email.
For real delivery, set `MAIL_MODE=smtp`, `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, and `MAIL_DEFAULT_SENDER`. Use an SMTP app password/API credential, not your normal mailbox password. Never commit `.env`.

## Scheduling
Campaign scheduling stores a future date/time. Run the scheduler periodically at that time:
```powershell
flask --app app run-scheduler
```
The same command processes due sequence enrollments. Configure Windows Task Scheduler for recurring execution in a real deployment.

## Tests
```powershell
pytest -q
```
Tests use isolated in-memory SQLite and console email mode.

## Important limitations / production checklist
- Open tracking is approximate: image proxying/blocking and privacy features affect counts.
- Click tracking rewrites absolute HTTP/HTTPS links only.
- Sequence scheduler is a simple one-message-per-sequence implementation, not a multi-step visual workflow engine.
- Signed unsubscribe links are generated per recipient and deactivate that subscriber. Add a production suppression-list workflow and compliance review before bulk commercial use.
- This is an internship/demo app, not a production ESP. Before real marketing use, implement CSRF protection, verified consent provenance, robust unsubscribe/suppression, bounce/complaint handling, rate limiting, deliverability controls, background queue/retries, tenant isolation and compliance review.
- A/B testing and conversion attribution are not implemented in this version.
