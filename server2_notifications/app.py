import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, messaging

# Initialize Flask App for Dedicated Push Notification Dispatcher
app = Flask(__name__)
CORS(app)

# Initialize Firebase Admin SDK
try:
    if not firebase_admin._apps:
        firebase_sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if firebase_sa:
            cred = credentials.Certificate(json.loads(firebase_sa))
        elif os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
        else:
            cred = None
            
        if cred:
            firebase_admin.initialize_app(cred)
            print("✅ [Server 2] Firebase Admin SDK initialized successfully.")
        else:
            print("⚠️ [Server 2] No service account provided. Running in standalone mode.")
except Exception as e:
    print(f"⚠️ [Server 2] Firebase Admin initialization error: {e}")

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Samvidha Notification Dispatcher (Server 2)",
        "fcm_ready": bool(firebase_admin._apps)
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
        message = f"{sender}: {message}"
        if len(message) > 60:
            message = message[:57] + "..."
        
        push_msg = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=message,
            ),
            data={
                "route": "/anonymous_chat"
            },
            android=messaging.AndroidConfig(
                priority="high",
                collapse_key="anon_chat"
            ),
            topic=topic
        )
        
        response = messaging.send(push_msg)
        return jsonify({"success": True, "message_id": response})
    except Exception as e:
        print(f"FCM Error: {e}")
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
            data={
                "title": title,
                "message": message,
                "route": route
            },
            android=messaging.AndroidConfig(
                priority="high",
            ),
            topic=topic,
        )
        
        response = messaging.send(push_msg)
        return jsonify({"success": True, "message_id": response})
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
        
        if not recipient:
            return jsonify({"success": False, "error": "No recipient specified"}), 400
            
        topic = f"dm_{recipient.upper()}"
        
        title = f"💬 {sender}"
        if len(title) > 35:
            title = title[:32] + "..."
            
        if len(message) > 60:
            message = message[:57] + "..."
        
        collapse_key_val = f"dm_{sender}"
        
        push_msg = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=message,
            ),
            android=messaging.AndroidConfig(
                priority="high",
                collapse_key=collapse_key_val,
                notification=messaging.AndroidNotification(
                    channel_id="samvidha_alerts_high",
                    tag=collapse_key_val,
                    default_sound=True,
                )
            ),
            apns=messaging.APNSConfig(
                headers={"apns-collapse-id": collapse_key_val}
            ),
            topic=topic,
            data={
                "title": title,
                "body": message,
                "route": "/messenger_chat",
                "sender": sender
            }
        )
        
        response = messaging.send(push_msg)
        return jsonify({"success": True, "message_id": response})
    except Exception as e:
        print(f"FCM Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"🚀 Starting Notification Server on port {port}...")
    app.run(host="0.0.0.0", port=port)
