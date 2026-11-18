# Samad Auto Reserve

A Telegram-configured assistant for reserving university meals through the Samad website.

## Status

The project is under active development. Do not use unattended reservations until the live Samad UI has been verified with an authorized account and the bot reports that setup is complete.

## Development setup

Requirements: Python 3.11+, a Telegram bot token, and network access to `samad.app`.

1. Create and activate a virtual environment.
2. Install the project: `python -m pip install -e .`
3. Install Playwright's Chromium browser: `python -m playwright install chromium`
4. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`.
5. Generate a Fernet key and set it as `CREDENTIAL_ENCRYPTION_KEY`:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

Keep `.env` and the encryption key private. Back up the key separately from the SQLite database; losing it makes saved passwords unrecoverable. Do not commit credentials or database files.

The bot collects Samad passwords through Telegram, which is not end-to-end encrypted. The application will warn users, accept credentials only in private chats, and make a best-effort attempt to delete the incoming password message.

## Configuration

See `.env.example` for supported environment variables. User-specific university, account, meal preferences, and weekly run time are managed through the Telegram bot. The default university is `دانشگاه شهید رجایی`.

## Reservation behavior

Preferences are configured per Saturday-Friday weekday and meal, with exactly one dining hall selected for each configured meal. The system skips existing bookings, checks the wallet against pending reservation prices, and does not use another dining hall as a fallback. A manual run is available in addition to the user-configured weekly schedule.

Use only with an account you are authorized to access. Samad page changes, unclear prices/balance, or login errors must stop the affected run and be reported rather than silently treated as success.

## Tests

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```
