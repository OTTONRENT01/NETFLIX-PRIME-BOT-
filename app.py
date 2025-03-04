import requests
from datetime import datetime, timedelta
import pytz
from flask import Flask, request, jsonify

app = Flask(__name__)

# --------------------- DB CONFIG ---------------------
# Replace with your actual Firebase Realtime Database URL (include trailing slash)
REAL_DB_URL = "https://get-accounts-netflix-prime-default-rtdb.firebaseio.com/"

ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str):
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

def is_credential(node):
    if not isinstance(node, dict):
        return False
    required = [
        "email", "password", "expiry_date",
        "locked", "usage_count", "max_usage",
        "belongs_to_slot"
    ]
    return all(r in node for r in required)

# --------------------------------------------------------------------
# Update all slots' start/end if 24h have passed since last_update
# --------------------------------------------------------------------
def update_slot_times_multi():
    now_ist = datetime.now(ist)
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code != 200 or not settings_resp.json():
        print("No settings found or request error.")
        return
    settings_data = settings_resp.json()
    all_slots = settings_data.get("slots")
    if not isinstance(all_slots, dict):
        print("No multi-slot node => fallback single-slot or skip.")
        return
    any_slot_shifted = False
    for slot_id, slot_info in all_slots.items():
        if not isinstance(slot_info, dict):
            continue
        if not slot_info.get("enabled", False):
            continue
        last_update_str = slot_info.get("last_update", "")
        if last_update_str:
            try:
                last_update_dt = parse_ist(last_update_str)
            except ValueError:
                last_update_dt = now_ist
        else:
            last_update_dt = now_ist
        delta = now_ist - last_update_dt
        if delta < timedelta(hours=24):
            print(f"[{slot_id}] Only {delta} since last update => skip SHIFT.")
            continue
        print(f"[{slot_id}] SHIFT: 24h+ since last update => shifting times.")
        slot_start_str = slot_info.get("slot_start", "9999-12-31 09:00:00")
        slot_end_str   = slot_info.get("slot_end", "9999-12-31 09:00:00")
        try:
            slot_start_dt = parse_ist(slot_start_str)
        except ValueError:
            slot_start_dt = now_ist.replace(hour=9, minute=0, second=0, microsecond=0)
        try:
            slot_end_dt = parse_ist(slot_end_str)
        except ValueError:
            slot_end_dt = slot_start_dt + timedelta(days=1)
        freq = slot_info.get("frequency", "daily").lower()
        shift_delta = timedelta(days=3) if freq == "3day" else timedelta(days=1)
        new_start = slot_start_dt + shift_delta
        new_end   = slot_end_dt   + shift_delta
        slot_info["slot_start"]  = format_ist(new_start)
        slot_info["slot_end"]    = format_ist(new_end)
        slot_info["last_update"] = format_ist(now_ist)
        any_slot_shifted = True
    if any_slot_shifted:
        patch_resp = requests.patch(REAL_DB_URL + "settings.json", json={"slots": all_slots})
        if patch_resp.status_code == 200:
            print("Multi-slot SHIFT success => now lock if needed.")
            lock_by_slot()
        else:
            print("Failed to patch updated slots =>", patch_resp.text)
    else:
        print("No slot was shifted => no changes made.")

# --------------------------------------------------------------------
# Lock credentials for slots whose end is within 2 minutes
# --------------------------------------------------------------------
def lock_by_slot():
    now_ist = datetime.now(ist)
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code != 200 or not settings_resp.json():
        print("No settings => skip lock.")
        return
    settings_data = settings_resp.json()
    all_slots = settings_data.get("slots", {})
    db_resp = requests.get(REAL_DB_URL + ".json")
    if db_resp.status_code != 200 or not db_resp.json():
        print("No DB data => skip lock.")
        return
    db_data = db_resp.json()
    margin = timedelta(minutes=2)
    locked_count_total = 0
    for slot_id, slot_info in all_slots.items():
        if not isinstance(slot_info, dict):
            continue
        if not slot_info.get("enabled", False):
            continue
        slot_end_str = slot_info.get("slot_end", "9999-12-31 09:00:00")
        try:
            slot_end_dt = parse_ist(slot_end_str)
        except ValueError:
            continue
        if now_ist >= (slot_end_dt - margin):
            for cred_key, cred_data in db_data.items():
                if not is_credential(cred_data):
                    continue
                if cred_data.get("belongs_to_slot", "") != slot_id:
                    continue
                locked_val = int(cred_data.get("locked", 0))
                if locked_val == 0:
                    patch_url  = REAL_DB_URL + f"/{cred_key}.json"
                    patch_data = {"locked": 1}
                    p = requests.patch(patch_url, json=patch_data)
                    if p.status_code == 200:
                        locked_count_total += 1
    print(f"Locked {locked_count_total} credentials in total.")

# -------------------------------------------------------
# Endpoints to trigger SHIFT or LOCK
# -------------------------------------------------------
@app.route("/update_slot")
def update_slot():
    update_slot_times_multi()
    return "Slot times updated!\n", 200

@app.route("/lock_check")
def lock_check():
    lock_by_slot()
    return "Lock check done.\n", 200

if __name__ == "__main__":    
    port = int(os.environ.get("PORT", 5000))  # Render sets the PORT env variable
    app.run(host="0.0.0.0", port=port)
