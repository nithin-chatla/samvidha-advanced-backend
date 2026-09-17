import requests
import os
import concurrent.futures
from flask import Flask, request, jsonify, abort, g
from flask_cors import CORS
from bs4 import BeautifulSoup
import secrets
import time
import base64
import re
import io
import json
import threading
import asyncio
import aiohttp
from urllib.parse import urljoin
# Academic & Portal Server


app = Flask(__name__)
CORS(app)

import gc
from requests.adapters import HTTPAdapter

# High-Concurrency Async & Scraping Worker Pool
MAX_CONCURRENT_SCRAPES = 24
SCRAPE_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_SCRAPES)

# Connection pool adapter for high concurrency socket reuse
HTTP_ADAPTER = HTTPAdapter(pool_connections=50, pool_maxsize=100, max_retries=3)

# Global Thread Pool
scraping_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SCRAPES)

BASE = "https://samvidha.iare.ac.in"
LOGIN_URL = BASE + "/pages/login/checkUser.php"

# ─── 24/7 Anti-Sleep Keep-Alive Engine ───────────────────────────
def _keep_alive_pinger():
    targets = [
        "https://samvidha-notify-api.onrender.com/health",
        "https://samvidha-portal-1.onrender.com/health",
        "https://samvidha-portal-2.onrender.com/health",
        "https://samvidha-ai-api.onrender.com/health",
    ]
    time.sleep(30)
    while True:
        try:
            for url in targets:
                try:
                    requests.get(url, timeout=10)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(540) # Every 9 minutes

threading.Thread(target=_keep_alive_pinger, daemon=True).start()

class SessionProxy:
    def __getitem__(self, key): return g.session
    def __contains__(self, key): return True
    def __setitem__(self, key, value): pass
    def __delitem__(self, key): pass

class TokenProxy:
    def __getitem__(self, key): return {"username": getattr(g, "username", "")}
    def __contains__(self, key): return True
    def __setitem__(self, key, value): pass
    def __delitem__(self, key): pass

TOKENS = TokenProxy()

# ==========================================
# IN-MEMORY 5-MINUTE TTL CACHE & MEMORY CLEANER
# ==========================================
CACHE = {}
CACHE_TTL = 300 # 5 minutes

def get_cached(key):
    if key in CACHE:
        data, ts = CACHE[key]
        if time.time() - ts < CACHE_TTL:
            return data
        else:
            del CACHE[key]
    return None

def set_cached(key, data):
    # If cache size grows over 1000 items, purge expired ones
    if len(CACHE) > 1000:
        now = time.time()
        expired = [k for k, (_, ts) in CACHE.items() if now - ts >= CACHE_TTL]
        for k in expired:
            CACHE.pop(k, None)
        gc.collect()
    CACHE[key] = (data, time.time())

SESSIONS = SessionProxy()

# ==========================================
# FACULTY AUTO-SCRAPER (24-HOUR DAEMON)
# ==========================================

DEPARTMENTS = {
    "AERO": "https://www.iare.ac.in/?q=departmentlist/26",
    "IT": "https://www.iare.ac.in/?q=departmentlist/27",
    "CSE": "https://www.iare.ac.in/?q=departmentlist/28",
    "ECE": "https://www.iare.ac.in/?q=departmentlist/29",
    "EEE": "https://www.iare.ac.in/?q=departmentlist/30",
    "MECH": "https://www.iare.ac.in/?q=departmentlist/31",
    "CIVIL": "https://www.iare.ac.in/?q=departmentlist/32",
    "CSE(AIML)": "https://www.iare.ac.in/?q=departmentlist/113",
    "DS": "https://www.iare.ac.in/?q=departmentlist/116"
}

async def fetch_html_async(session, url, retries=3):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=20) as response:
                if response.status == 200:
                    return await response.text()
                elif response.status in [403, 404, 500, 502, 503, 504]:
                    return None
        except Exception:
            if attempt == retries - 1:
                return None
            await asyncio.sleep(1)
    return None

async def scrape_department_async(session, dept_name, url):
    html = await fetch_html_async(session, url)
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    profile_links = []
    for a_tag in soup.find_all("a", string=lambda text: text and "View Profile" in text):
        href = a_tag.get("href")
        if href:
            full_link = urljoin("https://www.iare.ac.in/", href)
            if full_link not in profile_links:
                profile_links.append(full_link)
    tasks = [scrape_profile_async(session, link, dept_name) for link in profile_links]
    faculty_data = await asyncio.gather(*tasks)
    return [data for data in faculty_data if data]

async def scrape_profile_async(session, profile_url, dept_name):
    html = await fetch_html_async(session, profile_url)
    if not html: return None
    soup = BeautifulSoup(html, "lxml")
    faculty_info = {
        "department_short": dept_name, "profile_url": profile_url, "name": "", "faculty_id": "",
        "designation": "", "department_full": "", "total_experience": "", "experience_iare": "",
        "dob": "", "email": "", "employment_status": "", "jntuh_id": "", "aicte_id": "",
        "ug_degree": "", "pg_degree": "", "phd_degree": "", "specialization": "", "image_url": "", "search_index": []
    }
    title_tag = soup.find("h1") or soup.find("h2")
    if title_tag: faculty_info["name"] = title_tag.text.strip()
    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        for row in rows:
            cols = row.find_all(["td", "th"])
            if len(cols) >= 2:
                key = cols[0].text.strip().lower()
                val = cols[1].text.strip()
                if "name of the faculty" in key: faculty_info["name"] = val
                elif "aicte faculty id" in key: faculty_info["aicte_id"] = val
                elif "faculty id" in key: faculty_info["faculty_id"] = val
                elif "designation" in key: faculty_info["designation"] = val
                elif "department" in key: faculty_info["department_full"] = val
                elif "total experience" in key: faculty_info["total_experience"] = val
                elif "experience at iare" in key: faculty_info["experience_iare"] = val
                elif "date of birth" in key: faculty_info["dob"] = val
                elif "email id" in key: faculty_info["email"] = val
                elif "employment status" in key: faculty_info["employment_status"] = val
                elif "jntuh id" in key: faculty_info["jntuh_id"] = val
                elif "undergraduate" in key: faculty_info["ug_degree"] = val
                elif "postgraduate" in key: faculty_info["pg_degree"] = val
                elif "ph.d" in key: faculty_info["phd_degree"] = val
                elif "specialization" in key: faculty_info["specialization"] = val
    img_tag = soup.select_one("td img") or soup.select_one(".content img")
    if img_tag and img_tag.get("src"):
        faculty_info["image_url"] = urljoin("https://www.iare.ac.in/", img_tag.get("src"))
    search_string = f"{faculty_info['name']} {faculty_info['department_short']} {faculty_info['designation']} {faculty_info['specialization']}".lower()
    clean_words = re.findall(r'\w+', search_string)
    faculty_info['search_index'] = list(set(clean_words))
    return faculty_info

async def run_async_scraper():
    print("[BACKGROUND SCRAPER] Starting daily faculty extraction...")
    all_faculty = []
    connector = aiohttp.TCPConnector(limit=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [scrape_department_async(session, dept, url) for dept, url in DEPARTMENTS.items()]
        results = await asyncio.gather(*tasks)
        for dept_faculty in results:
            all_faculty.extend(dept_faculty)
    
    if all_faculty:
        existing_faculty = []
        try:
            with open("faculty_data.json", "r", encoding="utf-8") as f:
                existing_faculty = json.load(f)
        except:
            pass

        def get_key(fac):
            return fac.get("name", "").strip().lower() + "|" + fac.get("department_short", "").strip().lower()
            
        existing_dict = {get_key(f): f for f in existing_faculty}
        new_dict = {get_key(f): f for f in all_faculty}

        final_faculty = []

        # 1. Process existing (Updates and Removals)
        for key, old_f in existing_dict.items():
            if key not in new_dict:
                old_f["status"] = "removed"
                final_faculty.append(old_f)
            else:
                new_f = new_dict[key]
                if old_f.get("designation") != new_f.get("designation"):
                    new_f["previous_role"] = old_f.get("designation")
                final_faculty.append(new_f)

        # 2. Add brand new faculty
        for key, new_f in new_dict.items():
            if key not in existing_dict:
                final_faculty.append(new_f)

        with open("faculty_data.json", "w", encoding="utf-8") as f:
            json.dump(final_faculty, f, indent=4, ensure_ascii=False)
        print(f"[BACKGROUND SCRAPER] Successfully updated faculty_data.json with {len(final_faculty)} profiles.")
        
        # Instantly reload the AI's search index
        try:
            if rag_engine:
                rag_engine.reload_faculty_index()
        except NameError:
            pass

def schedule_daily_scrape():
    """Runs the scraper immediately on boot (optional) and then every 3 days."""
    while True:
        # Sleep for 3 days (259200 seconds) before running the first automated scrape
        time.sleep(259200)
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run_async_scraper())
            loop.close()
        except Exception as e:
            print(f"[BACKGROUND SCRAPER] Error: {e}")

# Start the background daemon thread when the app initializes
scraper_thread = threading.Thread(target=schedule_daily_scrape, daemon=True)
scraper_thread.start()

# ==========================================
# SAMVIDHA PORTAL APIs
# ==========================================

class SessionExpiredError(Exception): pass

@app.errorhandler(SessionExpiredError)
def handle_session_expired(e):
    return jsonify({"ok": False, "error": "session_expired"}), 401

def check_auth(r):
    url = r.url.lower()
    if "login" in url or "index.php" in url or "checkuser" in url:
        raise SessionExpiredError("Session expired")
    if '<input type="password"' in r.text.lower() or 'name="password"' in r.text.lower():
        raise SessionExpiredError("Session expired")

def login_session(username, password):
    session = requests.Session()
    session.mount("https://", HTTP_ADAPTER)
    session.mount("http://", HTTP_ADAPTER)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    })
    try:
        # Step 1: GET the login page to obtain CSRF token and fresh PHPSESSID cookie
        login_page = session.get(BASE + "/", timeout=20)
        csrf_token = ""
        soup = BeautifulSoup(login_page.text, "lxml")
        
        # The college site stores CSRF in: <meta name="csrf-token" content="...">
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        if csrf_meta:
            csrf_token = csrf_meta.get("content", "")
        
        # Fallback: try hidden input (currently commented out on their site, but may come back)
        if not csrf_token:
            csrf_input = soup.find("input", {"name": re.compile(r"csrf", re.I)})
            if csrf_input:
                csrf_token = csrf_input.get("value", "")

        # Step 2: POST login with CSRF token as HTTP header (X-CSRF-TOKEN)
        headers = {
            "Host": "samvidha.iare.ac.in",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE,
            "Referer": BASE + "/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if csrf_token:
            headers["X-CSRF-TOKEN"] = csrf_token
        
        payload = {"username": username, "password": password}
        res = session.post(LOGIN_URL, data=payload, headers=headers, timeout=20)
        
        if res.status_code != 200:
            return None, f"server_error_{res.status_code}: {res.text[:50]}"
            
        try:
            j = res.json()
        except Exception as e:
            return None, f"json_error: {res.text[:50]}"
            
        if j.get("status") == "1":
            if csrf_token:
                session.headers.update({"X-CSRF-TOKEN": csrf_token})
            return session, None
        return None, "invalid_credentials"
    except Exception as e:
        print(f"Login error: {str(e)}")
        return None, f"exception: {str(e)}"

def scrape_attendance(session):
    try:
        r = session.get(BASE + "/home?action=stud_att_STD", timeout=15)
        check_auth(r)
        
        soup = BeautifulSoup(r.text, "lxml")
        attendance_data = []
        last_date = ""

        for td in soup.find_all(["td", "th"]):
            if "last date of semester" in td.get_text(strip=True).lower():
                nxt = td.find_next_sibling("td")
                if nxt:
                    last_date = nxt.get_text(strip=True)
                break

        for table in soup.find_all("table"):
            if "Course Name" in table.get_text() and "Attendance %" in table.get_text():
                for tr in table.find_all("tr"):
                    cols = tr.find_all("td")
                    if len(cols) >= 7 and cols[0].get_text(strip=True).isdigit():
                        attendance_data.append({
                            "Subject": cols[2].get_text(strip=True), 
                            "Course Code": cols[1].get_text(strip=True),
                            "Conducted": cols[-4].get_text(strip=True),
                            "Attended": cols[-3].get_text(strip=True),
                            "Attendance %": cols[-2].get_text(strip=True),
                            "Status": cols[-1].get_text(strip=True) 
                        })
                break
        return {"records": attendance_data, "last_date": last_date}
    except SessionExpiredError:
        raise
    except Exception:
        return {"records": [], "last_date": ""}


def scrape_course_content(session):
    try:
        r = session.get(BASE + "/home", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        course_url = None
        for a_tag in soup.find_all("a", href=True):
            txt = a_tag.get_text(strip=True).lower()
            if "course content" in txt or "content delivery" in txt:
                course_url = a_tag["href"]
                break

        if not course_url:
            course_url = BASE + "/home?action=course_content_delivery"
        elif not course_url.lower().startswith("http"):
            course_url = urljoin(BASE, course_url)

        r2 = session.get(course_url, timeout=20)
        check_auth(r2)
        soup2 = BeautifulSoup(r2.text, "lxml")

        table = None
        for candidate in soup2.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in candidate.find_all("th")]
            headers_txt = " ".join(headers)
            if "date" in headers_txt and "period" in headers_txt:
                table = candidate
                break

        if not table:
            return {"records": []}

        course_rows = []
        current_subject = ""

        for tr in table.find_all("tr"):
            cols = tr.find_all(["td", "th"])
            if not cols:
                continue

            # Detect subject header row (colspan or first cell with non-data text).
            first_cell = cols[0].get_text(strip=True)
            row_texts = [c.get_text(strip=True) for c in cols]
            non_empty_cells = [x for x in row_texts if x != ""]

            if len(cols) == 1 and first_cell:
                current_subject = first_cell
                continue

            # If first cell has strong subject identifier and no date/period structure, treat as subject header.
            if first_cell and not first_cell.isdigit() and ("acad" in first_cell.lower() or "subject" in first_cell.lower() or re.search(r"[A-Za-z]{2,}\d", first_cell, re.I)):
                current_subject = first_cell
                continue

            # If row contains only one non-empty cell out of many, it is likely a subject break row.
            if len(non_empty_cells) == 1 and non_empty_cells[0] and not non_empty_cells[0].isdigit():
                current_subject = non_empty_cells[0]
                continue

            # If any cell appears to include subject label and row seems otherwise empty of data, treat as subject row.
            subject_candidates = [x for x in non_empty_cells if re.search(r"(acad|subject|course|[A-Z]{2,}\d)", x, re.I)]
            if subject_candidates and len(non_empty_cells) <= 3:
                current_subject = subject_candidates[0]
                continue

            # Skip table header line
            if first_cell.lower() in ["s.no", "sno", "#", "sr.no", "sr no", "sl.no", "sr"]:
                continue

            # Some rows might have non-numeric row index; if it has date/period-like values then process further.
            s_no = first_cell
            if first_cell.lower() in ["s.no", "sno", "#", "sr.no", "sr no", "sl.no", "sr"]:
                continue

            # Some rows might have non-numeric row index; treat as potential subject marker
            # If 2 cols only, might be header/subtitle; interpret as subject and continue
            if len(cols) == 2 and not first_cell.isdigit():
                subject_candidate = cols[1].get_text(separator=" ", strip=True)
                if subject_candidate:
                    current_subject = subject_candidate
                continue

            # Extract fields
            s_no = first_cell
            date_text = cols[1].get_text(strip=True) if len(cols) > 1 else ""
            period_text = cols[2].get_text(strip=True) if len(cols) > 2 else ""
            topic_text = cols[3].get_text(separator=" ", strip=True) if len(cols) > 3 else ""
            status_text = cols[4].get_text(strip=True) if len(cols) > 4 else ""

            pdf_link = ""
            youtube_link = ""
            extra_links = []

            for link in tr.find_all("a", href=True):
                href = link["href"].strip()
                full_href = href if href.lower().startswith("http") else urljoin(BASE, href)
                low = full_href.lower()
                if ".pdf" in low:
                    pdf_link = full_href
                if "youtu.be" in low or "youtube.com" in low:
                    youtube_link = full_href
                extra_links.append({"text": link.get_text(strip=True), "href": full_href})

            # Also collect direct text in powerpoint column when no link exists
            powerpoint_text = ""
            if len(cols) > 5:
                powerpoint_text = cols[5].get_text(strip=True)

            course_rows.append({
                "subject": current_subject,
                "s_no": s_no,
                "date": date_text,
                "period": period_text,
                "topic": topic_text,
                "status": status_text,
                "pdf": pdf_link,
                "youtube": youtube_link,
                "powerpoint": powerpoint_text,
                "links": extra_links
            })

        return {"records": course_rows}
    except SessionExpiredError:
        raise
    except Exception:
        return {"records": []}


def scrape_biometric(session):
    try:
        r = session.get(BASE + "/home?action=std_bio", timeout=15)
        check_auth(r)
        
        soup = BeautifulSoup(r.text, "lxml")
        bio_data = []
        for table in soup.find_all("table"):
            headers_text = table.get_text(separator=" ", strip=True).lower()
            if "date" in headers_text and "in time" in headers_text and "out time" in headers_text:
                for tr in table.find_all("tr"):
                    cols = tr.find_all("td")
                    if len(cols) >= 6 and cols[0].get_text(strip=True).isdigit():
                        roll_no = cols[1].get_text(strip=True)
                        if not roll_no or roll_no == "-": continue 
                        date = cols[3].get_text(strip=True)
                        in_time = cols[4].get_text(strip=True)
                        out_time = cols[5].get_text(strip=True)
                        status = cols[6].get_text(strip=True) if len(cols) > 6 else "-"
                        
                        if date and date != "-":
                            bio_data.append({"date": date, "in_time": in_time, "out_time": out_time, "status": status})
                break
        return bio_data
    except SessionExpiredError:
        raise
    except Exception as e:
        return []

def scrape_midmarks(session):
    try:
        r = session.get(BASE + "/home?action=cie_marks_ug", timeout=15)
        check_auth(r)
        
        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.find_all("tr")
        theory_data = []
        lab_data = []
        current_mode = None 
        current_sem = "Current Semester"

        for row in rows:
            text = row.get_text(strip=True).lower()
            if "semester -" in text:
                for cell in row.find_all(["th", "td"]):
                    if "semester -" in cell.get_text(strip=True).lower():
                        current_sem = cell.get_text(strip=True)
                        break
                        
            if "continuous internal assessment marks (theory)" in text:
                current_mode = "theory"
                continue
            elif "laboratory marks (practical)" in text or "seminar marks" in text:
                current_mode = "lab"
                continue
                
            cols = row.find_all("td")
            if len(cols) >= 10 and current_mode == "theory" and cols[0].get_text(strip=True).isdigit():
                theory_data.append({
                    "Semester": current_sem, 
                    "Course Code": cols[1].get_text(strip=True), 
                    "Course Name": cols[2].get_text(strip=True),
                    "CIE-I": cols[3].get_text(strip=True), "AAT:I-I": cols[4].get_text(strip=True),
                    "AAT:I-II": cols[5].get_text(strip=True), "CIE-II": cols[6].get_text(strip=True),
                    "AAT:II-I": cols[7].get_text(strip=True), "AAT:II-II": cols[8].get_text(strip=True),
                    "Total Marks": cols[-1].get_text(strip=True)
                })
            elif len(cols) >= 5 and current_mode == "lab" and cols[0].get_text(strip=True).isdigit():
                week_marks = [cols[i].get_text(strip=True) for i in range(3, len(cols) - 2) if cols[i].get_text(strip=True)]
                lab_data.append({
                    "Semester": current_sem, 
                    "Course Code": cols[1].get_text(strip=True), 
                    "Course Name": cols[2].get_text(strip=True),
                    "Weeks": week_marks, "Marks": cols[-1].get_text(strip=True)
                })
        return {"theory": theory_data, "laboratory": lab_data}
    except SessionExpiredError:
        raise
    except Exception:
        return {"theory": [], "laboratory": []}

def scrape_results(session):
    try:
        r = session.get(BASE + "/home?action=credit_register", timeout=15)
        check_auth(r)
        
        if r.status_code == 200 and "SEMESTER" in r.text.upper():
            soup = BeautifulSoup(r.text, "lxml")
            results_data = []
            current_sem_data = None
            overall_cgpa = "N/A"
            
            rows = soup.find_all('tr')
            for row in rows:
                text = row.get_text(separator=" ", strip=True).upper()
                if "SEMESTER" in text and "AVERAGE" not in text and len(text.split()) <= 3:
                    if current_sem_data: results_data.append(current_sem_data)
                    current_sem_data = {"semester": text.strip(), "subjects": [], "sgpa": "N/A", "cgpa": "N/A"}
                    continue
                if not current_sem_data: continue
                if "SEMESTER GRADE POINT AVERAGE" in text:
                    match = re.search(r'SGPA[^\d]*([\d\.]+)', text)
                    if match: current_sem_data["sgpa"] = match.group(1)
                    continue
                if "CUMULATIVE GRADE POINT AVERAGE" in text:
                    match = re.search(r'CGPA[^\d]*([\d\.]+)', text)
                    if match: 
                        current_sem_data["cgpa"] = match.group(1)
                        overall_cgpa = match.group(1) 
                    continue
                cols = row.find_all(['td', 'th'])
                if len(cols) >= 8 and cols[0].get_text(strip=True).isdigit():
                    grade = cols[3].get_text(strip=True)
                    status = cols[5].get_text(strip=True)
                    is_backlog = status == 'F' or grade == 'F' or 'bg-danger' in str(row)
                    current_sem_data["subjects"].append({
                        "code": cols[1].get_text(strip=True), "name": cols[2].get_text(strip=True),
                        "grade": grade, "points": cols[4].get_text(strip=True),
                        "status": status, "credits": cols[6].get_text(strip=True), "is_backlog": is_backlog
                    })
            if current_sem_data: results_data.append(current_sem_data)
            return {"semesters": results_data, "overall_cgpa": overall_cgpa}
    except SessionExpiredError: raise
    except Exception as e: pass
    return {"semesters": [], "overall_cgpa": "N/A"}

def scrape_memos(session, username):
    try:
        r = session.get(BASE + "/home?action=mybox", timeout=15)
        check_auth(r)
        memos = []
        roll = username.upper()
        
        if r.status_code == 200:
            raw_html = r.text
            a_html = ""
            
            ajax_urls = re.findall(r'url\s*:\s*[\'"]([^\'"]+\.php)[\'"]', raw_html, re.IGNORECASE)
            ajax_urls.extend(["pages/student/mybox/ajax/mybox.php", "pages/student/my_box/ajax/mybox.php"])
            test_urls = []
            for url in ajax_urls:
                clean_url = BASE + url if url.startswith("/") else (url if url.startswith("http") else BASE + "/" + url)
                if clean_url not in test_urls: test_urls.append(clean_url)
            
            headers = {'x-requested-with': 'XMLHttpRequest'}
            for url in test_urls:
                for action in ['get_mybox', 'get_mybox_data', 'get_data', '']:
                    try:
                        payload = {'action': action} if action else {}
                        ajax_res = session.post(url, data=payload, headers=headers, timeout=5)
                        if ajax_res.status_code == 200 and (".pdf" in ajax_res.text.lower() or roll in ajax_res.text.upper()):
                            a_html = ajax_res.text
                            break
                    except: pass
                if a_html: break

            for content in [raw_html, a_html]:
                if not content: continue
                soup = BeautifulSoup(content, "lxml")
                
                for tr in soup.find_all("tr"):
                    if tr.find("table"): continue
                    
                    row_html = str(tr)
                    href = None
                    
                    pdf_match = re.search(rf'({roll}_\d+\.pdf)', row_html, re.IGNORECASE)
                    if pdf_match:
                        href = f"https://iare-data.s3.ap-south-1.amazonaws.com/uploads/STUDENTS/{roll}/mybox/{pdf_match.group(1)}"
                    else:
                        s3_match = re.search(r'(https://iare-data\.s3[^\s"\'<>]+\.pdf)', row_html, re.IGNORECASE)
                        if s3_match:
                            href = s3_match.group(1)
                        else:
                            win_match = re.search(r"window\.open\(['\"]([^'\"]+)['\"]", row_html, re.IGNORECASE)
                            if win_match and win_match.group(1).endswith(".pdf"):
                                href = win_match.group(1)
                                if not href.startswith("http"):
                                    pdf_name = href.split('/')[-1]
                                    href = f"https://iare-data.s3.ap-south-1.amazonaws.com/uploads/STUDENTS/{roll}/mybox/{pdf_name}"

                    if not href or any(m['link'] == href for m in memos):
                        continue
                        
                    for junk in tr.find_all(["button", "a", "script", "style", "i", "span"]):
                        junk.decompose()
                        
                    cols = tr.find_all(["td", "th"])
                    name = "Official Grade Memo"
                    date = "Available"
                    
                    for col in cols:
                        txt = col.get_text(separator=" ", strip=True)
                        txt = re.sub(r'(?i)(cancel|print|view|download|close)', '', txt).strip(' -:>')
                        txt = re.sub(r'\s+', ' ', txt).strip()
                        
                        if re.match(r'^\d{2}[-/]\d{2}[-/]\d{2,4}$', txt):
                            date = txt
                        elif any(x in txt.upper() for x in ["B. TECH", "EXAMINATION", "MEMO", "REGULAR", "SUPPLEMENTARY", "RESULTS"]):
                            if 5 < len(txt) < 150: 
                                name = txt
                                
                    memos.append({"name": name, "date": date, "link": href})

            if not memos:
                combined = raw_html + a_html
                pdfs = re.findall(rf'({roll}_\d+\.pdf)', combined, re.IGNORECASE)
                pdfs = list(dict.fromkeys(pdfs))
                
                for idx, pdf in enumerate(pdfs):
                    link = f"https://iare-data.s3.ap-south-1.amazonaws.com/uploads/STUDENTS/{roll}/mybox/{pdf}"
                    if any(m['link'] == link for m in memos): continue
                    
                    match_pos = combined.find(pdf)
                    name = f"Official Grade Memo {idx+1}"
                    date = "Available"
                    
                    if match_pos != -1:
                        context = combined[max(0, match_pos - 600) : match_pos]
                        
                        d_matches = re.findall(r'\d{2}[-/]\d{2}[-/]\d{4}', context)
                        if d_matches: 
                            date = d_matches[-1] 
                            
                        n_matches = re.findall(r'((?:B\.\s*TECH|EXAMINATION)[^"\'<,\\]+)', context, re.IGNORECASE)
                        if n_matches:
                            best_name = n_matches[-1] 
                            clean_name = re.sub(r'(?i)(cancel|print|view|download|close|btn|class|href|javascript|my box|revaluation|s\.no|exam title)', '', best_name).strip(' -:>')
                            clean_name = re.sub(r'\s+', ' ', clean_name).strip()
                            if clean_name: name = clean_name
                            
                    memos.append({"name": name, "date": date, "link": link})

            return memos
    except Exception as e:
        print(f"Memos Error: {e}")
    return []

def scrape_profile_details(session):
    try:
        r = session.get(BASE + "/home?action=profile", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        
        # 1. Basic Info
        basic = {}
        img = soup.find("img", class_="profile-user-img")
        if img:
            src = img.get("src")
            if src:
                if not src.startswith("http"):
                    src = BASE + "/" + src.lstrip("/")
                basic["image_url"] = src
        name = soup.find("h3", class_="profile-username")
        if name: basic["name"] = name.text.strip()
        desc = soup.find("p", class_="text-muted")
        if desc: basic["branch_short"] = desc.text.strip()
        
        # Helper to parse dl rows
        def parse_dl(card_header_text):
            data = {}
            header = soup.find("h3", string=re.compile(card_header_text, re.I))
            if header:
                card = header.find_parent("div", class_="card")
                if card:
                    dl = card.find("dl")
                    if dl:
                        dts = dl.find_all("dt")
                        dds = dl.find_all("dd")
                        for dt, dd in zip(dts, dds):
                            data[dt.text.strip()] = dd.text.strip()
            return data
            
        general = parse_dl("General")
        admin = parse_dl("Administrative Information")
        marks = parse_dl("Marks Details")
        progress = parse_dl("Academic Progress")
        
        # Contacts
        contacts = {}
        c_header = soup.find("h3", string=re.compile("Contacts", re.I))
        if c_header:
            c_card = c_header.find_parent("div", class_="card")
            if c_card:
                c_body = c_card.find("div", class_="card-body")
                if c_body:
                    strongs = c_body.find_all("strong")
                    for s in strongs:
                        key = s.text.strip()
                        p = s.find_next_sibling("p")
                        if p:
                            contacts[key] = p.text.strip()
                            
        # Policies / Certificates
        def parse_table(title):
            rows = []
            th = soup.find("h3", string=re.compile(title, re.I))
            if th:
                card = th.find_parent("div", class_="card")
                if card:
                    table = card.find("table")
                    if table:
                        tr_list = table.find_all("tr")
                        if len(tr_list) > 1:
                            headers = [td.text.strip() for td in tr_list[0].find_all(["th", "td"])]
                            for tr in tr_list[1:]:
                                tds = tr.find_all(["th", "td"])
                                row_data = {}
                                for i, td in enumerate(tds):
                                    if i < len(headers):
                                        a = td.find("a")
                                        if a:
                                            row_data[headers[i]] = a.get("href")
                                        else:
                                            row_data[headers[i]] = td.text.strip()
                                rows.append(row_data)
            return rows
            
        policies = parse_table("Group Personal Accident Policy")
        certificates = parse_table("Certificates List")
        
        # Conduct
        conduct = ""
        c_div = soup.find("h4", string=re.compile("Overall Conduct", re.I))
        if c_div: conduct = c_div.text.strip()

        return {
            "ok": True,
            "basic": basic,
            "general": general,
            "contacts": contacts,
            "administrative": admin,
            "marks": marks,
            "academic_progress": progress,
            "policies": policies,
            "certificates": certificates,
            "conduct": conduct
        }
    except SessionExpiredError:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}

def scrape_fee_payment(session):
    try:
        r = session.get(BASE + "/home?action=fee_payment", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        
        fees = []
        table = soup.find("table")
        if table:
            for tr in table.find_all("tr"):
                cols = tr.find_all(["td", "th"])
                # We need at least 6 cols and first col should be a number (S.No)
                if len(cols) >= 6 and cols[0].get_text(strip=True).isdigit():
                    fee_obj = {
                        "sno": cols[0].get_text(strip=True),
                        "fee_type": cols[1].get_text(separator="\n", strip=True),
                        "track_id": cols[2].get_text(strip=True),
                        "payment_date": cols[3].get_text(strip=True),
                        "amount": cols[4].get_text(strip=True),
                        "action": "Unknown",
                        "action_url": ""
                    }
                    
                    action_col = cols[5]
                    
                    # Check for Pay Now button
                    a_tag = action_col.find("a", href=True)
                    if a_tag:
                        text = a_tag.get_text(strip=True).lower()
                        fee_obj["action"] = a_tag.get_text(strip=True)
                        if "pay" in text or "register" in text:
                            fee_obj["action_url"] = a_tag["href"]
                            if not fee_obj["action_url"].startswith("http"):
                                fee_obj["action_url"] = BASE + "/" + fee_obj["action_url"].lstrip("/")
                                
                    # Check for Print Receipt button
                    input_btn = action_col.find("input", type="button")
                    if input_btn and "print" in input_btn.get("value", "").lower():
                        fee_obj["action"] = "Print Receipt"
                        fee_obj["action_url"] = "" 
                        
                    fees.append(fee_obj)
        return {"ok": True, "records": fees}
    except SessionExpiredError:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e), "records": []}

def scrape_fee_status(session):
    try:
        r = session.get(BASE + "/home?action=fee_payment_status", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        
        table = soup.find("table")
        if not table:
            return {"ok": False, "error": "Table not found"}
            
        categories = []
        current_category = None
        current_rows = []
        
        for tr in table.find_all("tr"):
            th_colspan = tr.find("th", colspan="4")
            if th_colspan and tr.get("class") and any(c.startswith("bg-") for c in tr.get("class", [])):
                if current_category:
                    categories.append({"title": current_category, "rows": current_rows})
                current_category = th_colspan.get_text(strip=True)
                current_rows = []
                continue
                
            if current_category:
                cells = tr.find_all(["th", "td"])
                if not cells: continue
                
                if len(cells) == 1 and "Nil" in cells[0].get_text(strip=True):
                    continue
                
                is_header = bool(tr.find("th"))
                row_data = [c.get_text(separator=" ", strip=True) for c in cells]
                
                if not any(row_data): continue
                
                current_rows.append({"is_header": is_header, "data": row_data})
                
        if current_category:
            categories.append({"title": current_category, "rows": current_rows})
            
        return {"ok": True, "categories": categories}
        
    except SessionExpiredError:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e), "categories": []}

# ==========================================
# ALTERNATIVE ASSESSMENTS (AAI / AAT) SCRAPERS
# ==========================================
def scrape_aat_list(session, aat_type):
    actions = {
        "AAT-1": "upload_aat_cs",
        "AAT-2": "upload_aat_2",
        "Concept Video": "aatfmv_upload",
        "Tech Talk": "aat_upload"
    }
    action = actions.get(aat_type)
    if not action: return {"ok": False, "error": "Invalid AAT type requested"}

    try:
        r = session.get(BASE + f"/home?action={action}", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        
        last_date = "2026-12-31" 
        m_date = re.search(r'(\d{2}-\d{2}-\d{4})', r.text)
        if m_date:
            parts = m_date.group(1).split('-')
            if len(parts) == 3: last_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
            
        subjects = []
        for tr in soup.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) >= 5:
                if aat_type == "Concept Video":
                    sub_code = cols[1].get_text(strip=True)
                    course_name = cols[2].get_text(strip=True)
                    question = cols[3].get_text(strip=True)
                    
                    sem, ay, aat_type_param, dept_id = "", "", "", ""
                    btn = tr.find(["button", "a", "input"])
                    if btn:
                        sem = btn.get("data-sem", "")
                        ay = btn.get("data-ay", "")
                        aat_type_param = btn.get("data-aat_type", "")
                        dept_id = btn.get("data-dept", "")
                    
                    status = "Pending"
                    video_link = ""
                    file_id = ""
                    can_delete = False
                    url_cell_html = str(cols[4]).lower()
                    
                    input_tag = cols[4].find('input', type="text")
                    if input_tag:
                        val = input_tag.get('value', '').strip()
                        if val.startswith('http'): video_link = val
                        
                    for a_tag in cols[4].find_all('a', href=True):
                        href = a_tag['href']
                        if "youtu" in href.lower() or "drive" in href.lower() or href.startswith("http"):
                            if not video_link: video_link = href
                            
                    if len(video_link) > 5 or "already uploaded" in url_cell_html or "view" in url_cell_html or "success" in url_cell_html:
                        status = "Submitted"
                        
                    del_btn = cols[4].find(["button", "a"], class_=lambda c: c and ("del" in c.lower() or "danger" in c.lower()))
                    if del_btn: can_delete = True
                    
                    for inp in cols[4].find_all('input', type='hidden'):
                        if "fileid_" in inp.get("id", "") or "fileid_" in inp.get("name", ""):
                            file_id = inp.get("value", "")

                    subjects.append({
                        "code": sub_code,
                        "name": course_name,
                        "sem": sem,
                        "ay": ay,
                        "aat_type_param": "FMV", 
                        "dept_id": dept_id,
                        "last_date": last_date, 
                        "status": status, 
                        "marks": "-",
                        "question": question,
                        "video_link": video_link,
                        "file_id": file_id,
                        "can_delete": can_delete
                    })
                else:
                    btn = tr.find("button")
                    if btn:
                        sub_code = btn.get("data-sub_code", "")
                        sem = btn.get("data-sem", "")
                        ay = btn.get("data-ay", "")
                        aat_type_param = btn.get("data-aat_type", "")
                        dept_id = btn.get("data-dept", "")
                        course_name = cols[2].get_text(strip=True)
                        
                        subjects.append({
                            "code": sub_code,
                            "name": course_name,
                            "sem": sem,
                            "ay": ay,
                            "aat_type_param": aat_type_param,
                            "dept_id": dept_id,
                            "last_date": last_date, 
                            "status": "Pending", 
                            "marks": "-"
                        })

        import concurrent.futures
        def fetch_status(subj):
            payload = {
                "sub_code": subj["code"],
                "sem": subj["sem"],
                "ay": subj["ay"],
                "aat_type": subj["aat_type_param"],
                "dept_id": subj["dept_id"],
                "last_date": subj["last_date"], 
                "action": "get_aat_question"
            }
            ajax_url = BASE + "/pages/student/ajax/aatupload.php"
            if aat_type == "Concept Video": ajax_url = BASE + "/pages/student/ajax/aatfmvupload.php"
            elif aat_type == "Tech Talk": ajax_url = BASE + "/pages/student/ajax/aatupload_tt.php"

            try:
                res = session.post(ajax_url, data=payload, headers={"x-requested-with": "XMLHttpRequest"}, timeout=5)
                if res.status_code == 200:
                    html = res.text.lower()
                    if "btn-success" in html or "fa-eye" in html or "already uploaded" in html:
                        subj["status"] = "Submitted"
                    if "evaluated" in html:
                        subj["status"] = "Evaluated"
                        soup_res = BeautifulSoup(res.text, "lxml")
                        for td in soup_res.find_all('td'):
                            t = td.get_text(strip=True)
                            if t.isdigit() or t.replace('.','',1).isdigit():
                                subj["marks"] = t
            except: pass
            return subj

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            subjects = list(executor.map(fetch_status, subjects))
            
        return {"ok": True, "subjects": subjects}
    except SessionExpiredError: raise
    except Exception as e: return {"ok": False, "error": str(e)}

def scrape_aat_questions(session, aat_type, subject_data):
    try:
        payload = {
            "sub_code": subject_data.get("code", ""),
            "sem": subject_data.get("sem", ""),
            "ay": subject_data.get("ay", ""),
            "aat_type": subject_data.get("aat_type_param", ""),
            "dept_id": subject_data.get("dept_id", ""),
            "last_date": subject_data.get("last_date", "2026-12-31"), 
            "action": "get_aat_question"
        }
        
        ajax_url = BASE + "/pages/student/ajax/aatupload.php"
        if aat_type == "Tech Talk": ajax_url = BASE + "/pages/student/ajax/aatupload_tt.php"

        res = session.post(ajax_url, data=payload, headers={"x-requested-with": "XMLHttpRequest"}, timeout=10)
        check_auth(res)
        
        soup = BeautifulSoup(res.text, "lxml")
        questions_text = ""
        question_pdf = ""
        answer_pdf = ""
        answer_video = ""
        can_delete = False
        
        tbody = soup.find('tbody')
        if tbody:
            trs = tbody.find_all('tr')
            if trs:
                cols = trs[0].find_all('td')
                if len(cols) >= 2:
                    questions_text = cols[1].get_text(separator="\n", strip=True)
                    for a in cols[1].find_all("a", href=True):
                        if ".pdf" in a['href'].lower():
                            question_pdf = a['href'] if a['href'].startswith("http") else urljoin(BASE, a['href'])
                
                # Check for uploaded content in the last column
                if len(cols) >= 3:
                    for a in cols[2].find_all("a", href=True):
                        href = a['href']
                        title_lower = a.get("title", "").lower()
                        class_lower = " ".join(a.get("class", [])).lower()
                        
                        if "youtube" in href.lower() or "youtu.be" in href.lower() or "drive" in href.lower() or "video" in title_lower:
                            answer_video = href
                        elif "view" in title_lower or "btn-success" in class_lower or "eye" in str(a).lower():
                            answer_pdf = href
                            
                    if cols[2].find("button", class_=lambda c: c and ("del" in c.lower() or "danger" in c.lower())):
                        can_delete = True

        if not questions_text:
            questions_text = soup.get_text(separator="\n", strip=True)
            
        # CRITICAL FIX: Safe extraction of the hidden file_id required for deletion
        file_id = ""
        for inp in soup.find_all("input", type="hidden"):
            if "fileid_" in inp.get("id", "") or "fileid_" in inp.get("name", ""):
                file_id = inp.get("value", "")
                break
            
        return {
            "ok": True, 
            "questions": questions_text, 
            "question_pdf": question_pdf, 
            "answer_pdf": answer_pdf, 
            "answer_video": answer_video, 
            "file_id": file_id,
            "can_delete": can_delete
        }
    except SessionExpiredError: raise
    except Exception as e: return {"ok": False, "error": str(e)}

def upload_aat_logic(session, aat_type, subject_data, file_bytes=None, filename=None, youtube_link=None):
    try:
        ajax_url = BASE + "/pages/student/ajax/aatupload.php"
        
        # Use master endpoint. Tech Talk might be different:
        if aat_type == "Tech Talk": ajax_url = BASE + "/pages/student/ajax/aatupload_tt.php"

        upload_payload = {
            "subcode": (None, subject_data.get("code", "")),
            "sem": (None, subject_data.get("sem", "")),
            "ay": (None, subject_data.get("ay", "")),
            "c_year": (None, subject_data.get("ay", "")),
            "aat_type": (None, subject_data.get("aat_type_param", "")),
            "dept_id": (None, subject_data.get("dept_id", "")),
            "action": (None, "upload_answer")
        }
        
        # Override specifically for Concept Video as per portal JS requirements
        if aat_type == "Concept Video":
            upload_payload["action"] = (None, "Save")
            upload_payload["sub_code"] = (None, subject_data.get("code", ""))

        if youtube_link:
            upload_payload['link_1'] = (None, youtube_link) 
            upload_payload['url'] = (None, youtube_link) 
            upload_payload['video_link'] = (None, youtube_link) 
            upload_payload['file_url'] = (None, youtube_link) # Official FMV param
        
        if file_bytes and filename:
            upload_payload['file_1'] = (filename, io.BytesIO(file_bytes), 'application/pdf') 

        res = session.post(ajax_url, files=upload_payload, timeout=30)
        check_auth(res)
        
        return {"ok": True, "message": "Uploaded successfully to Samvidha!"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def scrape_profile(session, username):
    try:
        r = session.get(BASE + "/home?action=profile", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        profile = {"Header": {"Roll Number": username.upper()}, "Sections": {}, "Documents": {}}
        
        header_card = soup.find("div", class_=lambda c: c and any(x in c.lower() for x in ['profile', 'user-info', 'card-body']))
        if not header_card: header_card = soup
        name = header_card.find(["h3", "h4", "h5", "strong"])
        if name: profile["Header"]["Full Name"] = name.get_text(strip=True)
        branch = header_card.find(["h5", "p"])
        if branch: profile["Header"]["Department"] = branch.get_text(strip=True)

        for table in soup.find_all("table"):
            heading = table.find_previous(["h1", "h2", "h3", "h4", "h5", "h6", "div"], class_=lambda c: c and ('heading' in c.lower() or 'title' in c.lower() or 'panel-title' in c.lower()))
            panel = table.find_parent(["div"], class_=lambda c: c and 'panel' in c.lower())
            if panel and not heading:
                heading = panel.find(["div", "h1", "h2", "h3", "h4", "h5", "h6"], class_=lambda c: c and 'heading' in c.lower())

            section_name = heading.get_text(strip=True) if heading else "Other Details"
            if not section_name or len(section_name) > 40: section_name = "Other Details"
            if section_name not in profile["Sections"]: profile["Sections"][section_name] = {}

            for tr in table.find_all("tr"):
                cols = tr.find_all(["th", "td"])
                if len(cols) >= 2:
                    if cols[0].get_text(strip=True).isdigit() and len(cols) > 2:
                        key = cols[1].get_text(strip=True).replace(":", "")
                        val_elem = cols[-1]
                    else:
                        key = cols[0].get_text(strip=True).replace(":", "")
                        val_elem = cols[1] if len(cols) == 2 else cols[-1]
                    
                    if key.lower() in ["s.no", "sno", "#", "sl.no", ""]: continue
                    
                    a_tag = val_elem.find("a", href=True)
                    if a_tag and "javascript" not in a_tag["href"].lower() and "#" not in a_tag["href"]:
                        href = a_tag["href"]
                        if href.startswith("/"): href = BASE + href
                        elif not href.startswith("http"): href = BASE + "/" + href
                        doc_name = val_elem.get_text(strip=True)
                        if not doc_name or doc_name.lower() in ["view", "download", "-"]: doc_name = key
                        profile["Documents"][doc_name] = href
                        continue
                        
                    val = val_elem.get_text(separator=" ", strip=True)
                    if val and val != "-" and len(key) < 50:
                        profile["Sections"][section_name][key] = val

        for strong in soup.find_all(["strong", "b"]):
            key = strong.get_text(strip=True).replace(":", "")
            if not key or len(key) > 40: continue
            parent = strong.parent
            if parent.name in ["td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "a", "button"]: continue
            text_content = parent.get_text(separator="\n", strip=True)
            val = text_content.replace(strong.get_text(strip=True), "").strip().strip(":\n- ")
            if val and len(val) > 0 and len(val) < 200:
                panel = parent.find_parent(["div"], class_=lambda c: c and 'panel' in c.lower())
                section_name = "Contacts"
                if panel:
                    heading = panel.find(["div", "h1", "h2", "h3", "h4", "h5", "h6"], class_=lambda c: c and 'heading' in c.lower())
                    if heading: section_name = heading.get_text(strip=True)
                if section_name not in profile["Sections"]: profile["Sections"][section_name] = {}
                profile["Sections"][section_name][key] = val

        empty_keys = [k for k, v in profile["Sections"].items() if not v]
        for k in empty_keys: del profile["Sections"][k]
        return profile
    except SessionExpiredError: raise
    except Exception as e:
        return {"Header": {"Roll Number": username.upper()}, "Sections": {}, "Documents": {}}

def scrape_timetable(session, ay=None, section=None):
    try:
        r = session.get(BASE + "/home?action=TT_std", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        
        ays, sections = [], []
        ay_input_name = "ay"
        sec_input_name = "sec"
        
        sel_ay = soup.find('select', id=re.compile(r'ay', re.I)) or soup.find('select', {'name': re.compile(r'ay', re.I)})
        if sel_ay:
            ay_input_name = sel_ay.get('name', 'ay')
            ays = [{'value': o.get('value', ''), 'label': o.get_text(strip=True)} for o in sel_ay.find_all('option') if o.get('value')]
            
        sel_sec = soup.find('select', id=re.compile(r'sec', re.I)) or soup.find('select', {'name': re.compile(r'sec', re.I)})
        if sel_sec:
            sec_input_name = sel_sec.get('name', 'sec')
            sections = [{'value': o.get('value', ''), 'label': o.get_text(strip=True)} for o in sel_sec.find_all('option') if o.get('value')]

        schedule, subjects = [], []
        html_to_parse = r.text
        
        if ay and section:
            try:
                form_data = {
                    ay_input_name: ay,
                    sec_input_name: section,
                    'action': 'TT_std',
                    'submit': 'show',
                    'show': 'show',
                    'btnShow': 'show'
                }
                
                form = soup.find('form')
                if form:
                    for inp in form.find_all('input', type='hidden'):
                        if inp.get('name'):
                            form_data[inp.get('name')] = inp.get('value', '')
                            
                    submit_btn = form.find('button', type='submit') or form.find('input', type='submit')
                    if submit_btn and submit_btn.get('name'):
                        form_data[submit_btn.get('name')] = submit_btn.get('value', '') or 'show'

                res = session.post(BASE + "/home?action=TT_std", data=form_data, timeout=10)
                if res.status_code == 200 and "Period" in res.text:
                    html_to_parse = res.text
            except: pass
            
            if "Period" not in html_to_parse:
                ajax_urls = [
                    BASE + "/pages/student/timetable/ajax/timetable.php",
                    BASE + "/pages/student/time_table/ajax/timetable.php",
                    BASE + "/pages/student/time_table/ajax/day2day.php",
                    BASE + "/pages/student/tt/ajax/tt.php",
                    BASE + "/pages/student/tt/ajax/timetable.php"
                ]
                headers = {'x-requested-with': 'XMLHttpRequest'}
                for url in ajax_urls:
                    for action in ['get_timetable', 'get_tt', 'show_tt', 'get_data', 'get_timetable_data']:
                        try:
                            payload = {'action': action, 'ay': ay, 'section': section, 'sec': section, ay_input_name: ay, sec_input_name: section}
                            res = session.post(url, data=payload, headers=headers, timeout=5)
                            if res.status_code == 200 and ("Period" in res.text or "Staff" in res.text):
                                html_to_parse = res.text
                                break
                        except: pass
                    if "Period" in html_to_parse: break

        data_soup = BeautifulSoup(html_to_parse, "lxml")
        
        for table in data_soup.find_all("table"):
            header_text = table.get_text(separator=" ", strip=True).lower()
            if "period - i" in header_text or "period" in header_text:
                for tr in table.find_all("tr"):
                    cols = tr.find_all(["th", "td"])
                    if not cols: continue
                    day_text = cols[0].get_text(separator=" ", strip=True)
                    
                    if any(d in day_text.lower() for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]):
                        periods = []
                        for col in cols[1:]:
                            p_text = col.get_text(separator="\n", strip=True)
                            if not p_text or p_text == "-":
                                periods.append({"isFree": True, "subject": "-", "room": "", "faculty": ""})
                            else:
                                lines = [line.strip() for line in p_text.split('\n') if line.strip()]
                                subject = lines[0] if len(lines) > 0 else "-"
                                room = ""
                                faculty = ""
                                for line in lines[1:]:
                                    if "Room" in line: room = line.replace("Room", "").replace(":", "").strip()
                                    elif "Faculty" in line or "Staff" in line: faculty = line.replace("Faculty Id", "").replace("Faculty", "").replace(":", "").strip()
                                periods.append({"isFree": False, "subject": subject, "room": room, "faculty": faculty})

                        if periods: schedule.append({"day": day_text, "periods": periods})
                            
            elif "staff name" in header_text and "subject code" in header_text:
                for tr in table.find_all("tr"):
                    cols = tr.find_all("td")
                    if len(cols) >= 5 and cols[0].get_text(strip=True).isdigit():
                        subjects.append({
                            "code": cols[1].get_text(strip=True),
                            "name": cols[2].get_text(strip=True),
                            "short": cols[3].get_text(strip=True) if len(cols) > 3 else "",
                            "staff": cols[5].get_text(strip=True) if len(cols) > 5 else (cols[4].get_text(strip=True) if len(cols) > 4 else "")
                        })
                        
        return {"ok": True, "ays": ays, "sections": sections, "schedule": schedule, "subjects": subjects}
    except Exception as e:
        return {"ok": False, "error": str(e), "ays": [], "sections": [], "schedule": [], "subjects": []}




def rasterize_and_compress_pdf(file_bytes):
    if not fitz or not Image: raise Exception("PyMuPDF/Pillow missing.")
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    zoom_matrix = fitz.Matrix(0.5, 0.5)
    for page in doc:
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    if not images: return file_bytes
    output_io = io.BytesIO()
    images[0].save(output_io, format="PDF", resolution=100.0, save_all=True, append_images=images[1:], quality=50, optimize=True)
    return output_io.getvalue()

def require_token():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "): abort(401)
    token_str = h.split(" ")[1]
    
    try:
        token_data = json.loads(base64.urlsafe_b64decode(token_str).decode())
        
        session = requests.Session()
        session.mount("https://", HTTP_ADAPTER)
        session.mount("http://", HTTP_ADAPTER)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        if token_data.get("x"):
            session.headers.update({"X-CSRF-TOKEN": token_data["x"]})
        session.cookies.update(token_data.get("c", {}))
        
        g.session = session
        g.username = token_data.get("u", "")
        return token_str
    except Exception:
        abort(401)

@app.route("/check_update", methods=["GET"])
def check_update():
    return jsonify({
        "version": "5.2.2", 
        "build_number": 6, 
        "download_url": "https://drive.google.com/file/d/1DN1wUwjgqv8t-Xbo0ZxSrdqBNWz6g8u2/view?usp=sharing"
    })


@app.route("/home_summary", methods=["GET"])
@app.route("/api/home_bundle", methods=["GET"])
def home_summary():
    """
    Super-fast Home Screen bundle:
    Runs Profile, Attendance, Timetable, and Biometrics in parallel threads!
    Takes ~1.5s instead of 6s+ sequentially, and serves from 5-min RAM cache on repeat.
    """
    token = require_token()
    username = TOKENS[token].get("username", "").upper()
    
    cache_key = f"home_summary_{username}"
    cached = get_cached(cache_key)
    if cached:
        return jsonify({"ok": True, "source": "cache", "data": cached})

    session = SESSIONS[token]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        f_profile = executor.submit(scrape_profile_details, session)
        f_attendance = executor.submit(scrape_attendance, session)
        f_timetable = executor.submit(scrape_timetable, session)
        f_biometric = executor.submit(scrape_biometric, session)
        
        profile = f_profile.result()
        attendance = f_attendance.result()
        timetable = f_timetable.result()
        biometric = f_biometric.result()

    result = {
        "profile": profile,
        "attendance": attendance,
        "timetable": timetable,
        "biometric": biometric
    }
    
    set_cached(cache_key, result)
    return jsonify({"ok": True, "source": "live", "data": result})

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Samvidha Portal API (Server 1)"})

@app.route("/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    username, password = data.get("username"), data.get("password")
    if not username or not password: return jsonify({"ok": False, "error": "missing_credentials"}), 400
    session, err = login_session(username, password)
    if not session: return jsonify({"ok": False, "error": err}), 401
    
    # Create a completely stateless token that contains the session data
    token_data = {
        "u": username,
        "c": session.cookies.get_dict(),
        "x": session.headers.get("X-CSRF-TOKEN", "")
    }
    
    try:
        token = base64.urlsafe_b64encode(json.dumps(token_data).encode()).decode()
        return jsonify({"ok": True, "token": token})
    except Exception as e:
        return jsonify({"ok": False, "error": f"token_generation_failed: {str(e)}"}), 500

@app.route("/logout", methods=["POST"])
def api_logout():
    return jsonify({"ok": True})

@app.route("/profile", methods=["GET"])
def api_profile():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"profile_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = {"profile": scrape_profile(SESSIONS[token], u)}
    if res.get("profile"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/attendance", methods=["GET"])
def api_attendance():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"att_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = {"attendance": scrape_attendance(SESSIONS[token]), "biometric": scrape_biometric(SESSIONS[token])}
    if res.get("attendance", {}).get("records"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/course_delivery", methods=["GET"])
def api_course_delivery():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"cd_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = {"course_content": scrape_course_content(SESSIONS[token])}
    if res.get("course_content"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/results", methods=["GET"])
def api_results():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"res_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    results_info = scrape_results(SESSIONS[token])
    results_info["memos"] = scrape_memos(SESSIONS[token], u)
    res = {"results": results_info}
    if results_info.get("semesters"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/marks", methods=["GET"])
def api_marks():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"marks_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = {"midmarks": scrape_midmarks(SESSIONS[token])}
    if res.get("midmarks"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/timetable", methods=["POST"])
def api_timetable():
    token = require_token()
    data = request.get_json() or {}
    ay = data.get("ay")
    section = data.get("section")
    cache_key = f"tt_{ay}_{section}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = scrape_timetable(SESSIONS[token], ay, section)
    if res and res.get("ok"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/api/profile_details", methods=["POST"])
def api_profile_details():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"prof_det_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = scrape_profile_details(SESSIONS[token])
    if res and res.get("ok"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/api/fee_payment", methods=["POST"])
def api_fee_payment():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"fee_pay_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = scrape_fee_payment(SESSIONS[token])
    if res and res.get("ok"): set_cached(cache_key, res)
    return jsonify(res)

@app.route("/api/fee_status", methods=["POST"])
def api_fee_status():
    token = require_token()
    u = getattr(g, "username", "")
    cache_key = f"fee_stat_{u}"
    cached = get_cached(cache_key)
    if cached: return jsonify(cached)
    res = scrape_fee_status(SESSIONS[token])
    if res and res.get("ok"): set_cached(cache_key, res)
    return jsonify(res)


@app.route("/faculty", methods=["GET"])
def api_faculty():
    token = require_token()
    try:
        faculty_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faculty_data.json")
        if not os.path.exists(faculty_path):
            faculty_path = "faculty_data.json"
        with open(faculty_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"ok": True, "faculty": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Faculty data not found on server: {e}"})

@app.route("/aat_list", methods=["POST"])
def api_aat_list():
    token = require_token()
    data = request.get_json() or {}
    return jsonify(scrape_aat_list(SESSIONS[token], data.get("type")))

@app.route("/aat_questions", methods=["POST"])
def api_aat_questions():
    token = require_token()
    data = request.get_json() or {}
    return jsonify(scrape_aat_questions(SESSIONS[token], data.get("type"), data.get("subject_data")))

@app.route("/aat_upload", methods=["POST"])
def api_aat_upload():
    token = require_token()
    aat_type = request.form.get("type")
    
    subject_data = {
        "code": request.form.get("code"),
        "sem": request.form.get("sem"),
        "ay": request.form.get("ay"),
        "aat_type_param": request.form.get("aat_type_param"),
        "dept_id": request.form.get("dept_id")
    }
    youtube_link = request.form.get("youtube_link")
    
    file_bytes = None
    filename = None
    if 'aat_file' in request.files:
        f = request.files['aat_file']
        file_bytes = f.read()
        filename = f.filename
        
        if len(file_bytes) > 1024 * 1024:
            try: file_bytes = rasterize_and_compress_pdf(file_bytes)
            except Exception: pass
            if len(file_bytes) > 1024 * 1024: return jsonify({"ok": False, "error": "PDF too large."}), 400

    return jsonify(upload_aat_logic(SESSIONS[token], aat_type, subject_data, file_bytes, filename, youtube_link))

@app.route("/aat_delete", methods=["POST"])
def api_aat_delete():
    token = require_token()
    data = request.get_json() or {}
    aat_type = data.get("type")
    subject_data = data.get("subject_data", {})
    file_id = data.get("file_id", "") 
    
    ajax_url = BASE + "/pages/student/ajax/aatupload.php"
    if aat_type == "Tech Talk": ajax_url = BASE + "/pages/student/ajax/aatupload_tt.php"

    payload = {
        "id": file_id,
        "c_year": subject_data.get("ay", ""),
        "subcode": subject_data.get("code", ""),
        "sem": subject_data.get("sem", ""),
        "ay": subject_data.get("ay", ""),
        "aat_type": subject_data.get("aat_type_param", ""),
        "action": "delete_answer"
    }

    # Override for Concept Video deletion as per JS source
    if aat_type == "Concept Video":
        payload["sub_code"] = subject_data.get("code", "")
        payload["action"] = "Delete"
    
    try:
        res = SESSIONS[token].post(ajax_url, data=payload, headers={"x-requested-with": "XMLHttpRequest"}, timeout=10)
        return jsonify({"ok": True, "message": "Deleted successfully!"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/profile_details", methods=["POST"])
def api_profile_details():
    token = require_token()
    return jsonify(scrape_profile_details(SESSIONS[token]))

@app.route("/api/fee_payment", methods=["POST"])
def api_fee_payment():
    token = require_token()
    return jsonify(scrape_fee_payment(SESSIONS[token]))

@app.route("/api/fee_status", methods=["POST"])
def api_fee_status():
    token = require_token()
    return jsonify(scrape_fee_status(SESSIONS[token]))

@app.route("/all", methods=["GET"])
def api_all():
    token = require_token()
    session = SESSIONS[token]
    username = TOKENS[token]["username"]  
    try:
        f_att = scraping_executor.submit(scrape_attendance, session)
        f_bio = scraping_executor.submit(scrape_biometric, session)
        f_mid = scraping_executor.submit(scrape_midmarks, session)
        f_pro = scraping_executor.submit(scrape_profile, session, username)
        f_res = scraping_executor.submit(scrape_results, session)
        f_mem = scraping_executor.submit(scrape_memos, session, username)
        f_tt  = scraping_executor.submit(scrape_timetable, session, None, None)
        
        results_info = f_res.result()
        results_info["memos"] = f_mem.result()
        
        return jsonify({
            "ok": True, 
            "attendance": f_att.result(), 
            "biometric": f_bio.result(), 
            "midmarks": f_mid.result(), 
            "profile": f_pro.result(), 
            "results": results_info,
            "timetable_init": f_tt.result()
        })
    except SessionExpiredError: abort(401)

@app.route("/lab_init", methods=["GET"])
def api_lab_init():
    token = require_token()
    session = SESSIONS[token]
    try:
        r = session.get(BASE + "/home?action=labrecord_std", timeout=15)
        check_auth(r)
        soup = BeautifulSoup(r.text, "lxml")
        user_details = {}
        for key in ['ay', 'rollno', 'current_sem', 'lab_batch_no', 'dept_id', 'sec']:
            inp = soup.find('input', id=key)
            user_details[key] = inp['value'].strip() if inp else ""
            
        subjects = []
        seen_subjects = set()
        select = soup.find('select', id='ddlsub_code')
        if select:
            for opt in select.find_all('option'):
                val = opt.get('value', '').strip()
                text = opt.get_text(strip=True)
                if val and "Select Lab" not in text:
                    if val not in seen_subjects:
                        seen_subjects.add(val)
                        if " - " in text: text = text.split(" - ", 1)[1]
                        subjects.append({"value": val, "label": text})
        return jsonify({"ok": True, "user_details": user_details, "subjects": subjects})
    except Exception as e: return jsonify({"ok": False, "error": str(e)})

@app.route("/lab_subject_data", methods=["POST"])
def api_lab_subject_data():
    token = require_token()
    session = SESSIONS[token]
    data = request.get_json() or {}
    sub_code = data.get("sub_code")
    ud = data.get("user_details", {})
    
    ajax_url = BASE + "/pages/student/lab_records/ajax/day2day.php"
    headers = {'x-requested-with': 'XMLHttpRequest'}
    schedule_list = []
    submitted_list = []
    
    try:
        exp_res = session.post(ajax_url, headers=headers, data={'ay': ud.get('ay'), 'sub_code': sub_code, 'action': 'get_exp_list'}, timeout=15)
        check_auth(exp_res)
        if exp_res.status_code == 200:
            soup = BeautifulSoup(exp_res.text, "lxml")
            for tr in soup.find_all('tr')[1:]:
                cols = tr.find_all('td')
                if len(cols) >= 6:
                    schedule_list.append({"week": cols[0].get_text(strip=True), "title": cols[3].get_text(strip=True), "date": cols[5].get_text(strip=True)})

        sub_res = session.post(ajax_url, headers=headers, data={'rollno': ud.get('rollno'), 'ay': ud.get('ay'), 'sub_code': sub_code, 'action': 'day2day_lab'}, timeout=15)
        check_auth(sub_res)
        if sub_res.status_code == 200:
            sub_json = sub_res.json()
            for rec in sub_json.get('data', []):
                week_no = rec.get('week_no')
                mark = str(rec.get('mark', '')).strip()
                action_str = str(rec.get('action', '')).lower()
                remarks = str(rec.get('remarks', '')).lower()
                can_delete = str(rec.get('delete', '0')) == '1' or 'delete' in action_str or 'btn-danger' in action_str
                can_reupload = 'reupload' in action_str or 're-upload' in action_str or 'update' in action_str or 'reupload' in remarks
                
                if can_delete or can_reupload:
                    status = "Submitted"  
                    if mark == "0": mark = "-"  
                else: status = "Evaluated" if mark and mark not in ["-", ""] else "Submitted"
                
                roll = ud.get('rollno', '').upper()
                sem = ud.get('current_sem', '').upper()
                url = f"https://iare-data.s3.ap-south-1.amazonaws.com/uploads/STUDENTS/{roll}/LAB/SEM{sem}/{sub_code}/{roll}_week{week_no}.pdf"
                submitted_list.append({"week_no": str(week_no), "week": f"Week-{week_no}", "title": rec.get('exp_title', f"Experiment {week_no}"), "marks": mark if mark else "-", "status": status, "url": url, "can_delete": can_delete, "can_reupload": can_reupload})
                
        for sub in submitted_list:
            for sch in schedule_list:
                if sch['week'].replace(" ", "") == sub['week'].replace(" ", ""): sub['title'] = sch['title']
        return jsonify({"ok": True, "schedule": schedule_list, "submitted": submitted_list})
    except Exception as e: return jsonify({"ok": False, "error": str(e)})

@app.route("/lab_upload", methods=["POST"])
def api_lab_upload():
    token = require_token()
    session = SESSIONS[token]
    ajax_url = BASE + "/pages/student/lab_records/ajax/day2day"
    upload_payload = {'action': (None, 'upload_lab_record_student')}
    for k, v in request.form.items(): upload_payload[k] = (None, v)
    if not request.files or 'prog_doc' not in request.files: return jsonify({"ok": False, "error": "No file uploaded"}), 400
    f = request.files['prog_doc']
    file_bytes = f.read()
    if len(file_bytes) > 1024 * 1024:
        try: file_bytes = rasterize_and_compress_pdf(file_bytes)
        except Exception: pass
        if len(file_bytes) > 1024 * 1024: return jsonify({"ok": False, "error": "PDF too large. Please compress manually."}), 400
    rollno = request.form.get('rollno', '').upper()
    week_no = request.form.get('week_no', '')
    upload_payload['prog_doc'] = (f"{rollno}_week{week_no}.pdf" if rollno and week_no else f.filename, io.BytesIO(file_bytes), 'application/pdf')
    try:
        res = session.post(ajax_url, files=upload_payload, timeout=30)
        check_auth(res)
        res_json = res.json()
        if res_json.get("status") == "success": return jsonify({"ok": True, "message": "Uploaded successfully to Samvidha!"})
        return jsonify({"ok": False, "error": res_json.get("msg", "Upload failed")})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/lab_delete", methods=["POST"])
def api_lab_delete():
    token = require_token()
    session = SESSIONS[token]
    data = request.get_json() or {}
    try:
        res = session.post(BASE + "/pages/student/lab_records/ajax/day2day", data={'rollno': data.get('rollno'), 'ay': data.get('ay'), 'sub_code': data.get('sub_code'), 'week_no': data.get('week_no'), 'sem': data.get('current_sem'), 'action': 'day2day_lab_delete'}, headers={'x-requested-with': 'XMLHttpRequest'}, timeout=15)
        check_auth(res)
        if res.json().get("status") == "success": return jsonify({"ok": True, "message": "Deleted successfully!"})
        return jsonify({"ok": False, "error": "Delete failed"})
    except Exception as e: return jsonify({"ok": False, "error": str(e)})

def call_ai_with_fallback(system_prompt, history_messages, user_msg="", user_data=None):
    import os, time, requests
    api_keys = [k.strip() for k in os.environ.get("GLOBAL_AI_KEY", "").split(",") if k.strip()]
    groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()

    if not api_keys and not groq_api_key:
        return {"success": False, "error": "No API keys configured on server."}

    def get_gemini_url(key):
        try:
            models_res = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=10).json()
            if 'models' in models_res:
                for m in models_res['models']:
                    if 'generateContent' in m.get('supportedGenerationMethods', []) and 'gemini-1.5-flash' in m['name']:
                        return f"https://generativelanguage.googleapis.com/v1beta/{m['name']}:generateContent?key={key}"
                for m in models_res['models']:
                    if 'generateContent' in m.get('supportedGenerationMethods', []) and 'gemini' in m['name']:
                        return f"https://generativelanguage.googleapis.com/v1beta/{m['name']}:generateContent?key={key}"
        except: pass
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
                role = msg["role"]
                text = msg["text"]
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
                # If rate limited, wait a second and try the NEXT key in the loop
                if 'high demand' in err_msg or '503' in err_msg or '429' in err_msg or 'overloaded' in err_msg or 'quota' in err_msg:
                    time.sleep(1)
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
                # Groq role is "assistant", not "model"
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

    # Offline Fallback Commands when all APIs fail
    if user_data:
        msg_lower = user_msg.lower()
        
        # Attendance Logic
        if "attendance" in msg_lower or "bunk" in msg_lower or "absent" in msg_lower:
            att_data = user_data.get("attendance", {}).get("records", [])
            if att_data:
                reply = "*(Offline Mode)*\nHere is your current **Attendance Breakdown**:\n\n"
                total_c = 0
                total_a = 0
                for r in att_data:
                    c = int(r.get("Conducted", 0))
                    a = int(r.get("Attended", 0))
                    total_c += c
                    total_a += a
                    reply += f"- **{r.get('Subject', 'Subject')}**: {r.get('Attendance %', '0')}%\n"
                
                if total_c > 0:
                    overall = (total_a / total_c) * 100
                    reply = f"*(Offline Mode)*\nYour overall attendance is **{overall:.2f}%**.\n\n" + reply.replace("*(Offline Mode)*\n", "")
                return {"success": True, "reply": reply}

        # Timetable Logic
        if "timetable" in msg_lower or "class" in msg_lower or "period" in msg_lower:
            tt_data = user_data.get("timetable", [])
            if tt_data:
                reply = "*(Offline Mode)*\nHere is your **Timetable for Today**:\n\n"
                for r in tt_data:
                    reply += f"- **{r.get('time', '')}**: {r.get('subject', '')} (Room: {r.get('room', '')})\n"
                return {"success": True, "reply": reply}
                
        # Results Logic
        if "result" in msg_lower or "cgpa" in msg_lower or "sgpa" in msg_lower or "mark" in msg_lower:
            results = user_data.get("results", {})
            cgpa = results.get("cgpa", "N/A")
            reply = f"*(Offline Mode)*\nYour current overall **CGPA is {cgpa}**.\n\nCheck the Results section on the home screen for detailed subject marks!"
            return {"success": True, "reply": reply}

    # Engaging generic fallback message
    fallback_msg = "Samvidha AI is currently receiving an overwhelming amount of traffic and is taking a quick break! ⏳\n\nIn the meantime, you can explore the app to check your **Attendance**, view your **Timetable**, or browse your **Lab Records** directly from the home screen."
    return {"success": True, "reply": fallback_msg}

@app.route("/", methods=["GET"])
def home(): return jsonify({"status": "API is running"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))