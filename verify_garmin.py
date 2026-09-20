"""
Garmin Connect Health Biometrics Extraction Test
Designed for HS (Zurplox) - Personal Bio-Intelligence Pipeline
"""

import sys
import os
import json
import getpass
from datetime import date, timedelta
from pathlib import Path

try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    print("❌ Error: garminconnect library not installed.")
    print("Please run: pip install garminconnect")
    sys.exit(1)

TOKEN_STORE = os.path.expanduser("~/.garminconnect")


def get_mfa():
    """Prompt for MFA if Garmin account has 2-factor authentication enabled."""
    print("\n🔐 Garmin Multi-Factor Authentication (2FA) required.")
    code = input("👉 Enter the 6-digit verification code sent to your email/SMS: ").strip()
    return code


def authenticate():
    garmin = None
    token_file = Path(TOKEN_STORE) / "garmin_tokens.json"

    # Step 1: Try cached session tokens first
    if token_file.exists():
        print(f"🔑 Found existing Garmin session tokens at: {token_file}")
        print("⚡ Attempting token-based authentication (no password needed)...")
        try:
            garmin = Garmin()
            garmin.login(tokenstore=TOKEN_STORE)
            print("✅ Successfully authenticated using cached tokens!\n")
            return garmin
        except Exception as e:
            print(f"⚠️ Cached tokens invalid or expired: {e}")
            print("🔄 Falling back to interactive credential login...\n")

    # Step 2: Interactive login
    print("=" * 60)
    print("          GARMIN CONNECT AUTHENTICATION TEST")
    print("=" * 60)
    print("Note: Credentials are used only in memory to generate session")
    print(f"tokens saved securely to your local profile at {token_file}.")
    print("=" * 60)

    import argparse
    parser = argparse.ArgumentParser(description="Test Garmin Connect Authentication and Biometrics Extraction")
    parser.add_argument("--email", help="Garmin account email")
    parser.add_argument("--password", help="Garmin account password")
    args, _ = parser.parse_known_args()

    email = args.email or os.environ.get("GARMIN_EMAIL")
    if not email:
        try:
            email = input("👤 Garmin Account Email: ").strip()
        except EOFError:
            print("❌ No input detected. Run interactively or pass --email / --password.")
            sys.exit(1)

    password = args.password or os.environ.get("GARMIN_PASSWORD")
    if not password:
        try:
            password = getpass.getpass("🔑 Garmin Account Password (hidden): ").strip()
        except EOFError:
            print("❌ No input detected. Run interactively or pass --email / --password.")
            sys.exit(1)

    if not email or not password:
        print("❌ Email and password cannot be empty.")
        sys.exit(1)

    print("\n⏳ Contacting Garmin SSO service...")
    try:
        garmin = Garmin(email=email, password=password, prompt_mfa=get_mfa)
        garmin.login(tokenstore=TOKEN_STORE)
        print("✅ Authentication successful! Session tokens saved locally.\n")
        return garmin
    except GarminConnectAuthenticationError as e:
        print(f"❌ Authentication failed: {e}")
        print("Please check your email and password, or check if your account is locked.")
        sys.exit(1)
    except GarminConnectTooManyRequestsError:
        print("❌ Rate limited by Garmin (429). Please wait a few minutes before trying again.")
        sys.exit(1)
    except GarminConnectConnectionError as e:
        print(f"❌ Connection error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error during login: {e}")
        sys.exit(1)


def format_seconds(seconds):
    if not seconds:
        return "0h 0m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes:02d}m"


def extract_and_display(garmin):
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    print("=" * 60)
    print(f"📊 EXTRACTING BIOMETRICS ({today})")
    print("=" * 60)

    # 1. Profile confirmation
    name = garmin.full_name or garmin.display_name or "Athlete"
    print(f"👤 User: {name} (Display: {garmin.display_name})")

    extracted_data = {
        "date": today,
        "user": garmin.display_name,
    }

    # 2. Sleep Data (Check today first, then yesterday if today's sleep isn't posted yet)
    print("⏳ Fetching sleep data...")
    sleep_target_date = today
    sleep_data = None
    try:
        sleep_data = garmin.get_sleep_data(today)
        # If sleep data for today has no duration, try yesterday
        daily_sleep = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
        if not daily_sleep.get("sleepTimeSeconds"):
            sleep_target_date = yesterday
            sleep_data = garmin.get_sleep_data(yesterday)
    except Exception as e:
        print(f"⚠️ Could not retrieve sleep data for today: {e}")
        try:
            sleep_target_date = yesterday
            sleep_data = garmin.get_sleep_data(yesterday)
        except Exception as e2:
            print(f"⚠️ Sleep data retrieval fallback failed: {e2}")

    if sleep_data:
        extracted_data["sleep"] = sleep_data
        dto = sleep_data.get("dailySleepDTO", {})
        sleep_scores = dto.get("sleepScores", {})
        overall_score = sleep_scores.get("overall", {}).get("value", "N/A")
        qualifier = sleep_scores.get("overall", {}).get("qualifierKey", "N/A")
        total_sleep_sec = dto.get("sleepTimeSeconds", 0)
        deep_sec = dto.get("deepSleepSeconds", 0)
        rem_sec = dto.get("remSleepSeconds", 0)
        light_sec = dto.get("lightSleepSeconds", 0)
        awake_sec = dto.get("awakeSleepSeconds", 0)

        print(f"\n🛌 SLEEP ANALYSIS ({sleep_target_date}):")
        print(f"   • Sleep Score: {overall_score} ({qualifier})")
        print(f"   • Total Sleep: {format_seconds(total_sleep_sec)}")
        print(f"   • Deep Sleep:  {format_seconds(deep_sec)} ({(deep_sec / total_sleep_sec * 100 if total_sleep_sec else 0):.1f}%)")
        print(f"   • REM Sleep:   {format_seconds(rem_sec)} ({(rem_sec / total_sleep_sec * 100 if total_sleep_sec else 0):.1f}%)")
        print(f"   • Light Sleep: {format_seconds(light_sec)}")
        print(f"   • Awake Time:  {format_seconds(awake_sec)}")

    # 3. Overnight HRV Data
    print("\n⏳ Fetching HRV (Heart Rate Variability)...")
    try:
        hrv_data = garmin.get_hrv_data(today)
        if not hrv_data or not hrv_data.get("hrvSummary"):
            hrv_data = garmin.get_hrv_data(yesterday)
        if hrv_data:
            extracted_data["hrv"] = hrv_data
            summary = hrv_data.get("hrvSummary", {})
            last_night_avg = summary.get("lastNightAvg", "N/A")
            weekly_avg = summary.get("weeklyAvg", "N/A")
            status = summary.get("status", "N/A")
            baseline = summary.get("baseline", {})
            low_b = baseline.get("lowUpper", "N/A")
            balanced_low = baseline.get("balancedLow", "N/A")
            balanced_upper = baseline.get("balancedUpper", "N/A")

            print(f"❤️ HEART RATE VARIABILITY (HRV):")
            print(f"   • Last Night Avg: {last_night_avg} ms")
            print(f"   • 7-Day Baseline: {weekly_avg} ms (Status: {status})")
            print(f"   • Normal Range:   {balanced_low} - {balanced_upper} ms")
    except Exception as e:
        print(f"⚠️ HRV data unavailable: {e}")

    # 4. Body Battery
    print("\n⏳ Fetching Body Battery...")
    try:
        bb_data = garmin.get_body_battery(today)
        if bb_data:
            extracted_data["body_battery"] = bb_data
            # bb_data is typically a list of day entries
            today_bb = bb_data[-1] if isinstance(bb_data, list) else bb_data
            charged = today_bb.get("charged", "N/A")
            drained = today_bb.get("drained", "N/A")
            print(f"🔋 BODY BATTERY:")
            print(f"   • Charged Today: +{charged}")
            print(f"   • Drained Today: -{drained}")
    except Exception as e:
        print(f"⚠️ Body Battery data unavailable: {e}")

    # 5. Daily Summary / RHR & Stress
    print("\n⏳ Fetching Daily User Summary & Stress...")
    try:
        summary_data = garmin.get_user_summary(today)
        if summary_data:
            extracted_data["summary"] = summary_data
            rhr = summary_data.get("restingHeartRate", "N/A")
            avg_stress = summary_data.get("averageStressLevel", "N/A")
            max_stress = summary_data.get("maxStressLevel", "N/A")
            steps = summary_data.get("totalSteps", 0)
            cal = summary_data.get("totalKilocalories", 0)

            print(f"⚡ VITAL METRICS & RECOVERY:")
            print(f"   • Resting Heart Rate (RHR): {rhr} bpm")
            print(f"   • Average Stress Level:     {avg_stress} / 100")
            print(f"   • Max Stress Level:         {max_stress} / 100")
            print(f"   • Steps / Energy Burned:    {steps:,} steps | {cal:,} kcal")
    except Exception as e:
        print(f"⚠️ User summary unavailable: {e}")

    # 6. Training Readiness (if supported by watch model, e.g. Forerunner 265/965/Fenix/Epix)
    try:
        tr_data = garmin.get_training_readiness(today)
        if tr_data:
            extracted_data["training_readiness"] = tr_data
            score = tr_data.get("score", "N/A")
            level = tr_data.get("level", "N/A")
            print(f"\n🏃 TRAINING READINESS:")
            print(f"   • Readiness Score: {score} ({level})")
    except Exception:
        # Not all watches support Training Readiness
        pass

    # Save sample JSON
    out_file = Path("sample_garmin_data.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(extracted_data, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"🎉 SUCCESS! Raw biometrics snapshot saved to: {out_file.resolve()}")
    print("=" * 60)
    print("\n💡 NEXT STEP:")
    print("Since your tokens are now cached at ~/.garminconnect, any future script")
    print("or GitHub Action can run 100% headless without passwords or MFA prompts.")


if __name__ == "__main__":
    client = authenticate()
    if client:
        extract_and_display(client)
