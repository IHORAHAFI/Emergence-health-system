"""
Notification delivery.

Every notification is stored in the database first (in-app history), then delivered
through the requested channel. Delivery status is always honest:

  sent            - a real provider accepted the message
  simulated       - SMS_PROVIDER=simulate: logged only, NOT sent to a phone
  not_configured  - a real provider was selected but credentials are missing
  failed          - the provider returned an error (see `error`)

Configure SMS with environment variables (see .env.example):
  SMS_PROVIDER=simulate | africastalking | twilio | generic
"""
import logging

import requests
from django.conf import settings
from django.db.models import Q

from .models import Notification

log = logging.getLogger(__name__)
D = Notification.Delivery


# ----------------------------------------------------------------- SMS providers
def _sms_africastalking(phone, text):
    if not (settings.AT_USERNAME and settings.AT_API_KEY):
        return D.NOT_CONFIGURED, "AT_USERNAME / AT_API_KEY not set"
    host = "api.sandbox.africastalking.com" if settings.AT_USERNAME == "sandbox" else "api.africastalking.com"
    data = {"username": settings.AT_USERNAME, "to": phone, "message": text}
    if settings.SMS_SENDER_ID:
        data["from"] = settings.SMS_SENDER_ID
    r = requests.post(f"https://{host}/version1/messaging", data=data, timeout=10,
                      headers={"apiKey": settings.AT_API_KEY, "Accept": "application/json"})
    if not r.ok:
        return D.FAILED, f"HTTP {r.status_code}: {r.text[:300]}"
    recipients = r.json().get("SMSMessageData", {}).get("Recipients", [])
    if recipients and recipients[0].get("status") == "Success":
        return D.SENT, ""
    return D.FAILED, str(recipients or r.text)[:300]


def _sms_twilio(phone, text):
    sid, token, sender = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_FROM_NUMBER
    if not (sid and token and sender):
        return D.NOT_CONFIGURED, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER not set"
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                      auth=(sid, token), data={"From": sender, "To": phone, "Body": text}, timeout=10)
    return (D.SENT, "") if r.ok else (D.FAILED, f"HTTP {r.status_code}: {r.text[:300]}")


def _sms_generic(phone, text):
    if not settings.SMS_API_URL:
        return D.NOT_CONFIGURED, "SMS_API_URL not set"
    r = requests.post(settings.SMS_API_URL, timeout=10, json={"to": phone, "message": text},
                      headers={"Authorization": f"Bearer {settings.SMS_API_KEY}"})
    return (D.SENT, "") if r.ok else (D.FAILED, f"HTTP {r.status_code}: {r.text[:300]}")


PROVIDERS = {"africastalking": _sms_africastalking, "twilio": _sms_twilio, "generic": _sms_generic}


def send_sms(phone, text):
    """Returns (delivery_status, provider, error). Never raises."""
    provider = settings.SMS_PROVIDER
    if not phone:
        return D.FAILED, provider, "no phone number"
    if provider == "simulate" or provider not in PROVIDERS:
        log.info("[SIMULATED SMS - not sent] to %s: %s", phone, text)
        return D.SIMULATED, "simulate", ""
    try:
        status, err = PROVIDERS[provider](phone, text)
    except requests.RequestException as e:
        status, err = D.FAILED, f"network error: {e}"[:500]
    if status != D.SENT:
        log.warning("SMS to %s via %s: %s %s", phone, provider, status, err)
    return status, provider, err


def send_push(token, title, body, data=None):
    """Push via FCM legacy HTTP API. Returns (delivery_status, error)."""
    if not token:
        return D.FAILED, "device has no push token"
    if settings.NOTIFICATIONS_SIMULATE or not settings.FCM_SERVER_KEY:
        log.info("[SIMULATED PUSH - not sent] %s: %s", title, body)
        return D.SIMULATED, ""
    try:
        r = requests.post("https://fcm.googleapis.com/fcm/send", timeout=10,
                          headers={"Authorization": f"key={settings.FCM_SERVER_KEY}"},
                          json={"to": token, "notification": {"title": title, "body": body}, "data": data or {}})
        return (D.SENT, "") if r.ok else (D.FAILED, f"HTTP {r.status_code}")
    except requests.RequestException as e:
        return D.FAILED, str(e)[:500]


# ----------------------------------------------------------------- high-level API
def _in_app(title, message, emergency=None, recipient=None, hospital=None):
    return Notification.objects.create(recipient=recipient, hospital=hospital, emergency=emergency,
                                       title=title, message=message, channel=Notification.Channel.IN_APP,
                                       delivered=True, delivery_status=D.SENT, provider="in_app")


def sms(phone, title, message, emergency=None, recipient=None, hospital=None):
    status, provider, err = send_sms(phone, f"{title}: {message}")
    return Notification.objects.create(recipient=recipient, hospital=hospital, emergency=emergency,
                                       title=title, message=message, channel=Notification.Channel.SMS,
                                       destination=phone, provider=provider, error=err,
                                       delivery_status=status, delivered=(status == D.SENT))


def notify_user(user, title, message, emergency=None, sms_too=False):
    """In-app record (always) + optional push to the mobile app + optional SMS."""
    n = _in_app(title, message, emergency, recipient=user)
    if user.fcm_token:
        status, err = send_push(user.fcm_token, title, message,
                                {"emergency_id": str(getattr(emergency, "id", ""))})
        Notification.objects.create(recipient=user, emergency=emergency, title=title, message=message,
                                    channel=Notification.Channel.PUSH, destination="fcm", provider="fcm",
                                    delivery_status=status, delivered=(status == D.SENT), error=err)
    if sms_too and user.phone:
        sms(user.phone, title, message, emergency, recipient=user)
    return n


def notify_hospital(hospital, title, message, emergency=None):
    """Hospital-level alert + every approved healthcare professional of that hospital."""
    n = _in_app(title, message, emergency, hospital=hospital)
    for staff in hospital.staff.filter(is_active=True, is_approved=True):
        notify_user(staff, title, message, emergency)
    if hospital.phone:
        sms(hospital.phone, title, message, emergency, hospital=hospital)
    return n


def notify_responders(title, message, emergency=None):
    """Emergency operators and administrators (dispatch centre)."""
    from accounts.models import User
    for u in User.objects.filter(Q(role__in=["operator", "admin"]) | Q(is_superuser=True), is_active=True):
        notify_user(u, title, message, emergency)


def emergency_summary(em):
    """Standard alert body: reference, patient info, location, hospital, ambulance, status."""
    amb = em.assigned_ambulance
    parts = [
        f"Ref {em.reference}", f"{em.get_emergency_type_display()} - priority {em.priority.upper()}",
        f"Patient: {em.patient.get_full_name() or em.patient.username}"
        + (f", age {em.age}" if em.age else "") + f", conscious: {'yes' if em.conscious else 'NO'}",
        f"Location: {em.latitude:.5f}, {em.longitude:.5f}"
        + (f" ({em.location_description})" if em.location_description else ""),
        f"Hospital: {em.assigned_hospital.name if em.assigned_hospital else 'not yet chosen'}",
        f"Ambulance: {amb.plate_number + ' ETA ~%.0f min' % (em.eta_minutes or 0) if amb else 'none available yet'}",
        f"Status: {em.get_status_display()}",
    ]
    text = ". ".join(parts)
    try:  # include IoT vital signs (with measurement time) when available
        from patient_monitoring.services import latest_vitals_summary
        text += "." + latest_vitals_summary(em)
    except Exception:
        log.exception("could not add vitals to summary")
    return text
