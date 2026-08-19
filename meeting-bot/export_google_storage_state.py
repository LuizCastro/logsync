#!/usr/bin/env python3
"""Generate a Playwright Google storage_state JSON for headless Meet login reuse."""

from pathlib import Path
import argparse
import tempfile

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a Google Playwright storage_state file after manual login.",
    )
    parser.add_argument(
        "--output",
        default="google-storage-state.json",
        help="Output file path for storage state JSON.",
    )
    parser.add_argument(
        "--channel",
        default="chrome",
        choices=["chrome", "msedge", "chromium"],
        help="Browser channel to use. Prefer chrome for Google login.",
    )
    parser.add_argument(
        "--user-data-dir",
        default="",
        help="Persistent browser profile directory. Default: temporary directory.",
    )
    args = parser.parse_args()

    out_path = Path(args.output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if args.user_data_dir:
            profile_dir = Path(args.user_data_dir).expanduser().resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
        else:
            profile_dir = Path(tempfile.mkdtemp(prefix="logsync-google-profile-"))

        launch_kwargs = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--start-maximized",
            ],
            "viewport": {"width": 1366, "height": 900},
            "locale": "pt-BR",
            "timezone_id": "America/Sao_Paulo",
        }

        # Prefer real Chrome/Edge channels for lower automation detection.
        if args.channel in {"chrome", "msedge"}:
            launch_kwargs["channel"] = args.channel

        try:
            context = p.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
        except Exception as err:
            if args.channel != "chromium":
                print(f"Could not launch channel '{args.channel}' ({err}). Falling back to bundled chromium...")
                launch_kwargs.pop("channel", None)
                context = p.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
            else:
                raise

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()

        page.goto("https://accounts.google.com/", wait_until="domcontentloaded", timeout=60000)
        print("\nComplete login in the opened browser window.")
        print("If prompted, complete 2FA/challenge and open https://meet.google.com/.\n")
        input("Press ENTER here after login is complete and Meet opens successfully...")

        context.storage_state(path=str(out_path))
        context.close()

    print(f"\nSaved storage state to: {out_path}")
    print(f"Profile used: {profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
