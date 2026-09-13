import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, messaging

# Initialize Flask App for Dedicated Push Notification Dispatcher
app = Flask(__name__)
CORS(app)

# High-concurrency async thread pool for zero-delay WhatsApp-speed background dispatching
executor = ThreadPoolExecutor(max_workers=30)

_initialized_projects = []

def _init_firebase_apps():
    global _initialized_projects
    _initialized_projects = []
    
    # 1. Primary Service Account (e.g., iare-2204f core)
    sa_primary = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if sa_primary:
        try:
            dict_1 = json.loads(sa_primary)
            proj_1 = dict_1.get("project_id", "primary")
            if "default" not in firebase_admin._apps:
                cred1 = credentials.Certificate(dict_1)
                firebase_admin.initialize_app(cred1, name="[DEFAULT]")
                _initialized_projects.append(proj_1)
                print(f"✅ [Server 2] Initialized Primary Firebase project: {proj_1}")
        except Exception as e:
            print(f"⚠️ [Server 2] Error initializing Primary Firebase: {e}")

    # 2. Secondary Service Account (e.g., samvidha-iare community)
    sa_secondary = os.environ.get("FIREBASE_SERVICE_ACCOUNT_2") or os.environ.get("FIREBASE_SERVICE_ACCOUNT_COMMUNITY")
    if sa_secondary:
        try:
            dict_2 = json.loads(sa_secondary)
            proj_2 = dict_2.get("project_id", "secondary")
            if "secondary" not in firebase_admin._apps and proj_2 not in _initialized_projects:
                cred2 = credentials.Certificate(dict_2)
                firebase_admin.initialize_app(cred2, name="secondary")
                _initialized_projects.append(proj_2)
                print(f"✅ [Server 2] Initialized Secondary Firebase project: {proj_2}")
        except Exception as e:
            print(f"⚠️ [Server 2] Error initializing Secondary Firebase: {e}")

    # 3. Fallback to local serviceAccountKey.json if running locally
    if not _initialized_projects and os.path.exists("serviceAccountKey.json"):
        try:
            with open("serviceAccountKey.json") as f:
                dict_local = json.load(f)
                proj_local = dict_local.get("project_id", "local")
            cred_local = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred_local)
            _initialized_projects.append(proj_local)
            print(f"✅ [Server 2] Initialized Local Firebase project: {proj_local}")
        except Exception as e:
            print(f"⚠️ [Server 2] Local Firebase init error: {e}")

_init_firebase_apps()

def _dispatch_fcm_async(push_msg):
    """Broadcasts FCM push notifications to all configured Firebase projects concurrently"""
    try:
        apps = list(firebase_admin._apps.values())
        if not apps:
            return None
        for fb_app in apps:
            try:
                messaging.send(push_msg, app=fb_app)
            except Exception as app_err:
                print(f"⚠️ [FCM Send Error on {fb_app.name}]: {app_err}")
    except Exception as e:
        print(f"⚠️ [FCM Dispatch Error]: {e}")

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Samvidha Notification Dispatcher (Server 2)",
        "fcm_ready": bool(firebase_admin._apps),
        "active_firebase_projects": _initialized_projects or ["not_configured"],
        "engine": "WhatsApp-Speed Dual-Project High Priority Async Dispatcher"
    })

@app.route("/api/notify_anon_chat", methods=["POST"])
def notify_anon_chat():
    try:
        data = request.json or {}
        sender = data.get("sender", "Someone")
        message = data.get("message", "New message")
        
        if not message:
            return jsonify({"success": False, "error": "No message provided"})

        if len(message) > 60:
            message = message[:57] + "..."

        topic = "anon_chat"
        title = "Anonymous Chat Active"
        body_text = f"{sender}: {message}"
        if len(body_text) > 60:
            body_text = body_text[:57] + "..."
        
        push_msg = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body_text,
            ),
            data={
                "route": "/anonymous_chat",
                "title": title,
                "body": body_text,
                "click_action": "FLUTTER_NOTIFICATION_CLICK"
            },
            android=messaging.AndroidConfig(
                priority="high",
                ttl=3600,
                collapse_key="anon_chat",
                notification=messaging.AndroidNotification(
                    channel_id="samvidha_alerts_high",
                    priority="high",
                    default_sound=True,
                    default_vibrate_timings=True,
                    visibility="public"
                )
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10", "apns-push-type": "alert"}
            ),
            topic=topic
        )
        
        # Dispatch in background worker thread instantly
        executor.submit(_dispatch_fcm_async, push_msg)
        return jsonify({"success": True, "status": "dispatched_async"})
    except Exception as e:
        print(f"FCM Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notify_messenger", methods=["POST"])
def notify_messenger():
    try:
        data = request.json or {}
        sender = data.get("sender", "Someone")
        recipient = data.get("recipient", "")
        message = data.get("message", "")
        custom_title = data.get("title")
        custom_route = data.get("route", "/messenger_chat")
        
        if not recipient:
            return jsonify({"success": False, "error": "No recipient specified"}), 400
            
        topic = f"dm_{recipient.upper().strip()}"
        
        title = custom_title or f"💬 {sender}"
        if len(title) > 40:
            title = title[:37] + "..."
            
        if len(message) > 80:
            message = message[:77] + "..."
        
        collapse_key_val = f"dm_{sender.replace(' ', '_')}"
        
        push_msg = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=message,
            ),
            android=messaging.AndroidConfig(
                priority="high",
                ttl=3600,
                collapse_key=collapse_key_val,
                notification=messaging.AndroidNotification(
                    channel_id="samvidha_alerts_high",
                    tag=collapse_key_val,
                    priority="high",
                    default_sound=True,
                    default_vibrate_timings=True,
                    visibility="public",
                    click_action="FLUTTER_NOTIFICATION_CLICK"
                )
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10", "apns-push-type": "alert", "apns-collapse-id": collapse_key_val}
            ),
            topic=topic,
            data={
                "title": title,
                "body": message,
                "route": custom_route,
                "sender": sender,
                "click_action": "FLUTTER_NOTIFICATION_CLICK"
            }
        )
        
        executor.submit(_dispatch_fcm_async, push_msg)
        return jsonify({"success": True, "status": "dispatched_async"})
    except Exception as e:
        print(f"FCM Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notify_batch", methods=["POST"])
def notify_batch():
    """Ultra-fast parallel batch notification for multiple mentioned users"""
    try:
        data = request.json or {}
        recipients = data.get("recipients", [])
        sender = data.get("sender", "Someone")
        message = data.get("message", "")
        
        if not recipients:
            return jsonify({"success": False, "error": "No recipients provided"}), 400
            
        for roll in recipients:
            roll_clean = roll.upper().strip()
            if not roll_clean:
                continue
            topic = f"dm_{roll_clean}"
            title = f"🗣️ Mentioned in Anon Chat"
            push_msg = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=f"{sender}: {message}"[:80],
                ),
                android=messaging.AndroidConfig(
                    priority="high",
                    ttl=3600,
                    notification=messaging.AndroidNotification(
                        channel_id="samvidha_alerts_high",
                        priority="high",
                        default_sound=True,
                        default_vibrate_timings=True,
                        visibility="public"
                    )
                ),
                apns=messaging.APNSConfig(
                    headers={"apns-priority": "10", "apns-push-type": "alert"}
                ),
                topic=topic,
                data={
                    "title": title,
                    "body": message[:80],
                    "route": "/anonymous_chat",
                    "sender": sender
                }
            )
            executor.submit(_dispatch_fcm_async, push_msg)
            
        return jsonify({"success": True, "dispatched_count": len(recipients)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notify_admin_alert", methods=["POST"])
def notify_admin_alert():
    try:
        data = request.json or {}
        title = data.get("title", "Important Update")
        message = data.get("message", "")
        topic = data.get("topic", "admin_alerts")
        route = data.get("route", "/dashboard")
        
        push_msg = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=message[:100],
            ),
            data={
                "title": title,
                "message": message,
                "route": route,
                "click_action": "FLUTTER_NOTIFICATION_CLICK"
            },
            android=messaging.AndroidConfig(
                priority="high",
                ttl=86400,
                notification=messaging.AndroidNotification(
                    channel_id="samvidha_alerts_high",
                    priority="high",
                    default_sound=True,
                    default_vibrate_timings=True,
                    visibility="public"
                )
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10", "apns-push-type": "alert"}
            ),
            topic=topic,
        )
        
        executor.submit(_dispatch_fcm_async, push_msg)
        return jsonify({"success": True, "status": "dispatched_async"})
    except Exception as e:
        print(f"FCM Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"🚀 Starting Notification Server on port {port}...")
    app.run(host="0.0.0.0", port=port)
