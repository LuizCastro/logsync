#!/usr/bin/env python3
"""Generate a Playwright Google storage_state JSON for headless Meet login reuse."""

from pathlib import Path
import argparse

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
    args = parser.parse_args()

    out_path = Path(args.output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
        print("\nComplete login in the opened browser window.")
        print("If prompted, complete 2FA/challenge and open https://meet.google.com/.\n")
        input("Press ENTER here after login is complete and Meet opens successfully...")

        context.storage_state(path=str(out_path))
        browser.close()

    print(f"\nSaved storage state to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
