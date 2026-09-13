# Samvidha Advanced Distributed Backend

A high-performance, distributed microservices architecture for the Samvidha Hub application.

---

## 🏗️ Architecture

| Service | Directory | Port (Dev) | Tech Stack | Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **Server 1: Portal & Scraper** | `server1_portal/` | `5000` | Flask + BeautifulSoup4 + Requests (Pooled) | High-speed authentication, session management & academic scraping |
| **Server 2: Push Notifications** | `server2_notifications/` | `5002` | Flask + Firebase Admin SDK | FCM push notification dispatch & background alerts |
| **Server 3: AI Engine & AAT** | `server3_ai_engine/` | `5003` | Flask + Google Gemini AI + PyMuPDF | Samvidha Bot RAG chatbot & AAT assignment engine |

---

## 🚀 Deployment on Render.com

### Server 1: Portal & Scraper (Deploy 1 or 2 Instances)
* **Environment**: `Python 3`
* **Root Directory**: `server1_portal`
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `gunicorn app:app --workers 2 --threads 4 --timeout 60`

### Server 2: Push Notifications
* **Environment**: `Python 3`
* **Root Directory**: `server2_notifications`
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `gunicorn app:app --workers 2 --threads 4 --timeout 60`

### Server 3: AI Engine & AAT
* **Environment**: `Python 3`
* **Root Directory**: `server3_ai_engine`
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `gunicorn app:app --workers 2 --threads 4 --timeout 60`
* **Environment Variables**:
  * `GEMINI_API_KEY`: Your Gemini API Key
