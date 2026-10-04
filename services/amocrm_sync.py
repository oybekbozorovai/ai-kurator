"""amoCRM → allowed_contacts sync.

WON statusdagi leads'lardan kontakt telefon raqamlarini olib,
allowed_contacts jadvaliga qo'shadi (mavjud bo'lmasa).
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from config import AMOCRM_ACCESS_TOKEN, AMOCRM_SUBDOMAIN, AMOCRM_WON_STATUS_IDS
from services import supabase_db as sb
from services.auth import normalize_phone

logger = logging.getLogger(__name__)


def _amo_get(path: str, params: dict) -> dict:
    url = f"https://{AMOCRM_SUBDOMAIN}.amocrm.ru/api/v4{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AMOCRM_ACCESS_TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _get_won_contact_ids() -> list[int]:
    """WON statusdagi barcha leads'lardan contact_id'larni yig'adi."""
    contact_ids: list[int] = []
    page = 1
    while True:
        params: dict = {"page": page, "limit": 250, "with": "contacts"}
        for i, sid in enumerate(AMOCRM_WON_STATUS_IDS):
            params[f"filter[statuses][{i}][status_id]"] = sid
        try:
            data = _amo_get("/leads", params)
        except urllib.error.HTTPError as e:
            if e.code == 204:
                break
            raise
        leads = data.get("_embedded", {}).get("leads", [])
        if not leads:
            break
        for lead in leads:
            for c in lead.get("_embedded", {}).get("contacts", []):
                cid = c.get("id")
                if cid and cid not in contact_ids:
                    contact_ids.append(int(cid))
        if len(leads) < 250:
            break
        page += 1
    return contact_ids


def _get_phones_from_contacts(contact_ids: list[int]) -> list[dict]:
    """Kontakt ID'laridan telefon raqamlarini oladi (50 tadan batch)."""
    results: list[dict] = []
    for i in range(0, len(contact_ids), 50):
        batch = contact_ids[i : i + 50]
        params: dict = {"limit": 50}
        for j, cid in enumerate(batch):
            params[f"filter[id][{j}]"] = cid
        try:
            data = _amo_get("/contacts", params)
        except Exception as e:
            logger.error("amoCRM contacts batch xato: %s", e)
            continue
        for c in data.get("_embedded", {}).get("contacts", []):
            for field in c.get("custom_fields_values") or []:
                if field.get("field_code") == "PHONE":
                    for val in field.get("values", []):
                        phone = normalize_phone(str(val.get("value", "")))
                        if phone:
                            results.append({
                                "phone": phone,
                                "first_name": c.get("first_name", "") or "",
                                "last_name": c.get("last_name", "") or "",
                            })
    return results


def sync_won_contacts(cohort_id: Optional[str] = None) -> dict:
    """amoCRM WON leads'lardan telefon raqamlarini olib allowed_contacts'ga qo'shadi.

    Returns: {"added": int, "skipped": int, "errors": int}
    """
    if not AMOCRM_SUBDOMAIN or not AMOCRM_ACCESS_TOKEN:
        return {"added": 0, "skipped": 0, "errors": 1, "message": "AMOCRM_SUBDOMAIN yoki AMOCRM_ACCESS_TOKEN set qilinmagan"}

    existing = {r["phone"] for r in sb.select("allowed_contacts", "select=phone")}

    contact_ids = _get_won_contact_ids()
    phones = _get_phones_from_contacts(contact_ids)

    seen: set[str] = set()
    added = skipped = errors = 0

    for p in phones:
        phone = p["phone"]
        if phone in existing or phone in seen:
            skipped += 1
            continue
        seen.add(phone)
        try:
            sb.insert(
                "allowed_contacts",
                {
                    "phone": phone,
                    "first_name": p["first_name"] or None,
                    "last_name": p["last_name"] or None,
                    "role": "student",
                    "cohort_id": cohort_id,
                    "notes": "amoCRM auto-sync",
                },
                prefer="return=minimal",
            )
            added += 1
        except sb.SupabaseError as e:
            logger.error("add phone xato %s: %s", phone, e)
            errors += 1

    return {"added": added, "skipped": skipped, "errors": errors}
