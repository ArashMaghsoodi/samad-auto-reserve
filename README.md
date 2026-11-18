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

6. Start the bot with `samad-auto-reserve` (or `python -m samad_reserver.main`).

Keep `.env` and the encryption key private. Back up the key separately from the SQLite database; losing it makes saved passwords unrecoverable. Do not commit credentials or database files.

The bot collects Samad passwords through Telegram, which is not end-to-end encrypted. The application will warn users, accept credentials only in private chats, and make a best-effort attempt to delete the incoming password message.

## Configuration

See `.env.example` for supported environment variables. User-specific university, account, meal preferences, and weekly run time are managed through the Telegram bot. The default university is `دانشگاه شهید رجایی`.

## Telegram commands

- `/start` and `/help` show setup guidance and commands.
- `/credentials` starts username/password entry. The bot warns that Telegram chats are not end-to-end encrypted and attempts to delete the password message.
- `/university <name>` sets the university used for the Samad login.
- `/setmeal <weekday> <meal> <exact dining hall name>` configures one meal; repeat for each day and meal.
- `/clearmeal <weekday> <meal>` and `/preferences` update or review the weekly selections.
- `/balance` checks the wallet; `/run` manually runs the configured reservations.
- `/schedule HH:MM` configures the weekly Saturday run in Tehran time; `/schedule off` disables it.
- `/forget` removes saved credentials and disables scheduling.

## Reservation behavior

Preferences are configured per Saturday-Friday weekday and meal, with exactly one dining hall selected for each configured meal. The system skips existing bookings, checks the wallet against pending reservation prices, and does not use another dining hall as a fallback. A manual run is available in addition to the user-configured weekly schedule.

Use only with an account you are authorized to access. Samad page changes, unclear prices/balance, or login errors must stop the affected run and be reported rather than silently treated as success.

The public login page has shown disabled login controls and an unavailable verification resource during development. The automation does not bypass captchas or other verification. Validate the current login, pricing, and reservation UI with an authorized account before enabling unattended runs.

## Tests

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```
