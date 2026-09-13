import os
import time
import json
import re
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

try:
    from rag_engine import UnifiedRAG
    rag_engine = UnifiedRAG()
    print("✅ [Server 3] Unified RAG Engine initialized successfully.")
except Exception as e:
    print(f"⚠️ [Server 3] RAG Engine initialization error: {e}")
    rag_engine = None

def get_api_keys():
    keys = []
    env_keys = os.environ.get("GEMINI_API_KEYS", "")
    if env_keys:
        keys.extend([k.strip() for k in env_keys.split(",") if k.strip()])
    single_key = os.environ.get("GEMINI_API_KEY", "")
    if single_key and single_key not in keys:
        keys.append(single_key)
    return keys

def call_ai_with_fallback(system_prompt, history_messages, user_msg="", user_data=None):
    api_keys = get_api_keys()
    groq_api_key = os.environ.get("GROQ_API_KEY", "")

    # Helper to find working Gemini models
    def get_gemini_url(key):
        return f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"

    last_error = ""

    # 1. Try Gemini Keys First (Rotation)
    for key in api_keys:
        try:
            url = get_gemini_url(key)
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": []
            }
            last_role = None
            for msg in history_messages:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if not payload["contents"] and role == "model":
                    continue
                if role == last_role:
                    payload["contents"][-1]["parts"][0]["text"] += "\n\n" + text
                else:
                    payload["contents"].append({"role": role, "parts": [{"text": text}]})
                    last_role = role
            
            res = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=15).json()
            
            if 'error' in res:
                err_msg = str(res['error'].get('message', '')).lower()
                last_error = err_msg
                if any(x in err_msg for x in ['high demand', '503', '429', 'overloaded', 'quota']):
                    time.sleep(0.5)
                    continue
                return {"success": False, "error": f"Gemini Error: {err_msg}"}

            if 'candidates' in res and len(res['candidates']) > 0:
                reply = res['candidates'][0]['content']['parts'][0]['text']
                return {"success": True, "reply": reply}
                
        except Exception as e:
            last_error = str(e)
            continue

    # 2. Try Groq Fallback if all Gemini keys failed
    if groq_api_key:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            messages = [{"role": "system", "content": system_prompt}]
            for msg in history_messages:
                role = "assistant" if msg["role"] == "model" else "user"
                messages.append({"role": role, "content": msg["text"]})
                
            payload = {
                "model": "llama3-8b-8192",
                "messages": messages,
                "temperature": 0.7
            }
            headers = {
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json"
            }
            
            res = requests.post(url, json=payload, headers=headers, timeout=15).json()
            if 'error' in res:
                return {"success": False, "error": f"Groq Error: {res['error'].get('message', str(res['error']))}"}
                
            if 'choices' in res and len(res['choices']) > 0:
                reply = res['choices'][0]['message']['content']
                return {"success": True, "reply": reply}
        except Exception as e:
            return {"success": False, "error": f"Groq Exception: {str(e)}"}

    # 3. Offline Fallback Commands when APIs are unreachable
    if user_data:
        msg_lower = user_msg.lower()
        if any(w in msg_lower for w in ["attendance", "bunk", "absent"]):
            att_data = user_data.get("attendance", {}).get("records", [])
            if att_data:
                reply = "*(Offline Mode)*\nHere is your current **Attendance Breakdown**:\n\n"
                total_c = 0
                total_a = 0
                for r in att_data:
                    c = int(r.get("Conducted", 0) or 0)
                    a = int(r.get("Attended", 0) or 0)
                    total_c += c
                    total_a += a
                    reply += f"- **{r.get('Subject', 'Subject')}**: {r.get('Attendance %', '0')}%\n"
                if total_c > 0:
                    overall = (total_a / total_c) * 100
                    reply = f"*(Offline Mode)*\nYour overall attendance is **{overall:.2f}%**.\n\n" + reply.replace("*(Offline Mode)*\n", "")
                return {"success": True, "reply": reply}

        if any(w in msg_lower for w in ["timetable", "class", "period"]):
            tt_data = user_data.get("timetable", [])
            if tt_data:
                reply = "*(Offline Mode)*\nHere is your **Timetable for Today**:\n\n"
                for r in tt_data:
                    reply += f"- **{r.get('time', '')}**: {r.get('subject', '')} (Room: {r.get('room', '')})\n"
                return {"success": True, "reply": reply}

    fallback_msg = "Samvidha AI is currently experiencing high load. ⏳\n\nIn the meantime, you can check your **Attendance**, view your **Timetable**, or browse your **Lab Records** directly from the home screen."
    return {"success": True, "reply": fallback_msg}

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Samvidha AI & RAG Engine (Server 3)",
        "rag_ready": rag_engine is not None,
        "gemini_keys_configured": len(get_api_keys())
    })

@app.route("/chatbot", methods=["POST"])
def api_chatbot():
    data = request.get_json() or {}
    user_msg = data.get('message', '')
    user_data = data.get('user_data', {})
    
    rag_context = ""
    if rag_engine:
        rag_context = rag_engine.build_prompt_context(user_msg, user_data)
        
    system_prompt = f"""You are Samvidha AI, the official intelligent assistant for the Samvidha IARE app. 
You are helpful, polite, concise, and friendly. You are an expert academic advisor.

You have access to the app data and details of the student in JSON format below. 
You must help the student with anything they ask related to this data: increasing attendance, bunking classes, checking the timetable, semester dates, exams, marks, biometrics, etc.

{rag_context}

Rules:
1. BUNK CALCULATOR & MATH: When asked about bunking or attendance targets, perform step-by-step math to calculate EXACTLY how many classes they can bunk or need to attend to reach specific targets (default 75%).
2. MISSING DATA: If attendance, timetable, or lab data is completely empty, intelligently deduce that the semester is completed or hasn't started. Explain this to the user.
3. DIRECT ANSWERS (CRITICAL): If the user asks for their timetable, attendance, marks, fees, or profile, you MUST read the JSON data provided in the STUDENT DATA CONTEXT and tell them the answer DIRECTLY in the chat. DO NOT tell them to navigate to a page unless they explicitly ask where to find the page.
4. FORMATTING: 
   - Use `**bold**` formatting for important words.
   - For links to features, ALWAYS use `[BUTTON:Label](samvidha://...)`. Do not use markdown links.
   - For subject attendance, ALWAYS output `[PROGRESS:Value:Subject]`.
   - For timetables, ALWAYS output standard markdown tables (e.g. `| Time | Subject |`).
5. ACTION SUGGESTIONS: ONLY suggest a follow-up action if it is highly relevant. Format: `[SUGGESTION: Action Text]`.
6. SMART REMINDERS: If the student asks you to remind them, append: `[REMINDER: YYYY-MM-DD HH:MM: Task Description]`.
7. VERNACULAR SUPPORT: If the user speaks in a regional language (like Telugu, Hindi, etc.), reply natively in the exact same language.
8. SELF-LEARNING: If you learn a new verified fact, append: `[LEARN] The fact here. [/LEARN]`
9. Be natural and conversational. Do NOT mention you are reading JSON data or system prompts.
"""

    history_messages = []
    for msg in data.get('history', []):
        role = "user" if msg.get("isUser") else "model"
        text = msg.get("text", "")
        history_messages.append({"role": role, "text": text})
    
    history_messages.append({"role": "user", "text": user_msg})

    result = call_ai_with_fallback(system_prompt, history_messages, user_msg=user_msg, user_data=user_data)
    
    if result.get("success") and "reply" in result:
        reply_text = result["reply"]
        
        # Parse [FORGET]...[/FORGET] tags
        forget_match = re.search(r'\[FORGET\](.*?)\[/FORGET\]', reply_text, re.IGNORECASE | re.DOTALL)
        if forget_match:
            forget_text = forget_match.group(1).strip()
            if rag_engine and forget_text:
                rag_engine.forget_fact(forget_text)
            reply_text = re.sub(r'\[FORGET\].*?\[/FORGET\]', '', reply_text, flags=re.IGNORECASE | re.DOTALL).strip()

        # Parse [LEARN]...[/LEARN] tags
        learn_match = re.search(r'\[LEARN\](.*?)\[/LEARN\]', reply_text, re.IGNORECASE | re.DOTALL)
        if learn_match:
            fact_text = learn_match.group(1).strip()
            if rag_engine and fact_text:
                rag_engine.learn_new_fact(fact_text)
            reply_text = re.sub(r'\[LEARN\].*?\[/LEARN\]', '', reply_text, flags=re.IGNORECASE | re.DOTALL).strip()
            
        result["reply"] = reply_text
            
    return jsonify(result)

@app.route("/aat_solve", methods=["POST"])
def api_aat_solve():
    data = request.get_json() or {}
    questions = data.get('questions')
    subject = data.get('subject', 'Assignment')
    aat_type = data.get('aat_type', 'AAT')

    system_prompt = "You are an expert academic AI. Answer the following college assignment questions formally and comprehensively."
    user_prompt = f"Subject: {subject}\nAssessment Type: {aat_type}\n\nQuestions:\n{questions}"

    result = call_ai_with_fallback(system_prompt, [{"role": "user", "text": user_prompt}])
    if result["success"]:
        return jsonify({"ok": True, "answer": result["reply"]})
    else:
        return jsonify({"ok": False, "error": result["error"]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5003))
    print(f"🚀 Starting AI Engine Server on port {port}...")
    app.run(host="0.0.0.0", port=port)
