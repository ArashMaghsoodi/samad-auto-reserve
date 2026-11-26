# Samad Auto Reserve

A Telegram-configured assistant for reserving university meals through Shahid Rajaee University's legacy Samad portal.

## Status

The project is under active development. Do not use unattended reservations until the legacy portal's login, meal table, pricing, and confirmation behavior have been verified with an authorized account.

## Development setup

Requirements: Python 3.11+, a Telegram bot token, and network access to `https://food.sru.ac.ir`.

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

See `.env.example` for supported environment variables. Account credentials, meal preferences, and weekly run time are managed through the Telegram bot. The portal is dedicated to Shahid Rajaee University, so university selection is not required.

Set `VERBOSE_PIN` to a four-digit ASCII number to enable `/verbose`. The bot asks for this PIN in a private chat before toggling detailed diagnostics. Five incorrect attempts lock the PIN prompt for that Telegram user until the bot restarts. PIN input is deleted on a best-effort basis.

## Telegram commands

- `/start` and `/help` show setup guidance and commands.
- `/credentials` starts username/password entry. The bot warns that Telegram chats are not end-to-end encrypted and attempts to delete the password message.
- `/verbose` asks for `VERBOSE_PIN` and toggles detailed browser diagnostics on or off.
- `/university` explains that the legacy portal is fixed to Shahid Rajaee University.
- `/setmeal <weekday> <meal> <exact dining hall name>` configures one meal; repeat for each day and meal.
- `/clearmeal <weekday> <meal>` and `/preferences` update or review the weekly selections.
- `/balance` checks the wallet; `/run` manually runs the configured reservations.
- `/schedule HH:MM` configures the weekly Saturday run in Tehran time; `/schedule off` disables it.
- `/forget` removes saved credentials and disables scheduling.

## Reservation behavior

Preferences are configured per Saturday-Friday weekday and meal, with exactly one dining hall selected for each configured meal. Persian weekday names with or without a space are supported, for example `/setmeal سه شنبه ناهار سلف مرکزی برادران`. The system skips existing bookings, checks the wallet against pending reservation prices, and does not use another dining hall as a fallback. A manual run is available in addition to the user-configured weekly schedule.

Use only with an account you are authorized to access. The automation opens the old-version interface, selects the configured dining hall, and uses the weekday/meal table. Page changes, unclear prices/balance, or login errors must stop the affected run and be reported rather than silently treated as success.

The automation uses the secure `https://food.sru.ac.ir/index.rose` entry point and upgrades same-host HTTP navigation and form requests to HTTPS before sending them. It blocks unsupported login actions and prevents secure portal POST redirects from reaching HTTP or off-host destinations. It never bypasses Chromium's insecure-form warning with "Send anyway," and it does not bypass captchas or other verification. Validate the current login, meal table, prices, and post-confirmation state with an authorized account before enabling unattended runs. Existing `.env` files should set `SAMAD_BASE_URL=https://food.sru.ac.ir` if they still point at `samad.app`.

Verbose diagnostics include browser request methods, response statuses, safe page paths, and navigation milestones. They omit credentials, request and response bodies, URL query strings, and browser console/error message contents. Logs are written to the configured process output; disable diagnostics with `/verbose` when finished.

## Tests

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```
