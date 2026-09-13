import json
import math
import re
import os
from collections import Counter

class BM25Index:
    def __init__(self):
        self.documents = []
        self.doc_lengths = []
        self.term_freqs = []
        self.doc_freqs = Counter()
        self.avgdl = 0
        self.N = 0
        self.k1 = 1.5
        self.b = 0.75

    def tokenize(self, text):
        if not text:
            return []
        # Lowercase and extract alphanumeric words
        return re.findall(r'\w+', str(text).lower())

    def add_document(self, text, doc_data):
        tokens = self.tokenize(text)
        if not tokens:
            return
            
        self.documents.append(doc_data)
        self.doc_lengths.append(len(tokens))
        
        tf = Counter(tokens)
        self.term_freqs.append(tf)
        
        for term in tf.keys():
            self.doc_freqs[term] += 1
            
        self.N = len(self.documents)
        self.avgdl = sum(self.doc_lengths) / self.N

    def idf(self, term):
        n = self.doc_freqs.get(term, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def get_score(self, query_tokens, doc_index):
        score = 0.0
        doc_len = self.doc_lengths[doc_index]
        tf = self.term_freqs[doc_index]
        
        for term in query_tokens:
            if term not in self.doc_freqs:
                continue
            freq = tf.get(term, 0)
            numerator = freq * (self.k1 + 1)
            denominator = freq + self.k1 * (1 - self.b + self.b * (doc_len / self.avgdl))
            score += self.idf(term) * (numerator / denominator)
            
        return score

    def retrieve(self, query, top_k=2, min_score=0.1):
        if self.N == 0:
            return []
            
        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []
            
        scores = []
        for i in range(self.N):
            score = self.get_score(query_tokens, i)
            if score >= min_score:
                scores.append((score, self.documents[i]))
                
        scores.sort(key=lambda x: x[0], reverse=True)
        return [doc for score, doc in scores[:top_k]]


class UnifiedRAG:
    def __init__(self, base_dir=None):
        if base_dir is None:
            base_dir = os.path.dirname(__file__)
            
        self.memory_index = BM25Index()
        self.faculty_index = BM25Index()
        self.features_index = BM25Index()
        self.learned_index = BM25Index()
        
        self.load_memory(os.path.join(base_dir, "memory.json"))
        self.load_faculty(os.path.join(base_dir, "faculty_data.json"))
        self.load_features()
        
        self.learned_memory_path = os.path.join(base_dir, "learned_memory.json")
        self.load_learned_memory(self.learned_memory_path)
        
    def load_memory(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                knowledge = data.get('knowledge', [])
                for item in knowledge:
                    parts = [
                        item.get('title', ''),
                        item.get('category', ''),
                        " ".join(item.get('keywords', [])),
                        " ".join(item.get('aliases', []))
                    ]
                    
                    sections = item.get('sections', [])
                    if isinstance(sections, dict):
                        sections = [{"heading": k, "content": str(v)} for k, v in sections.items()]
                        
                    for sec in sections:
                        if isinstance(sec, dict):
                            parts.append(sec.get('heading', ''))
                            parts.append(sec.get('content', ''))
                            
                    faq = item.get('faq', [])
                    if isinstance(faq, dict):
                        faq = [{"question": k, "answer": str(v)} for k, v in faq.items()]
                        
                    for f in faq:
                        if isinstance(f, dict):
                            parts.append(f.get('question', ''))
                            parts.append(f.get('answer', ''))
                        
                    self.memory_index.add_document(" ".join(parts), item)
            print(f"[UnifiedRAG] Indexed {self.memory_index.N} memory items.")
        except Exception as e:
            print(f"[UnifiedRAG] Error loading memory: {e}")
            
    def load_faculty(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                items = data if isinstance(data, list) else [{"name": k, **v} for k, v in data.items()]
                
                for item in items:
                    name = item.get('name', '')
                    dept = item.get('department_short', '')
                    desig = item.get('designation', '')
                    search_idx = " ".join(item.get('search_index', []))
                    
                    full_text = f"{name} {dept} {desig} {search_idx}"
                    self.faculty_index.add_document(full_text, item)
            print(f"[UnifiedRAG] Indexed {self.faculty_index.N} faculty items.")
        except Exception as e:
            print(f"[UnifiedRAG] Error loading faculty: {e}")

    def reload_faculty_index(self):
        print("[UnifiedRAG] Reloading faculty index from disk...")
        try:
            self.faculty_index = BM25Index()
            self.load_faculty(self.faculty_data_path)
        except Exception as e:
            print(f"[UnifiedRAG] Error reloading faculty index: {e}")

    def load_features(self):
        features = [
            {"name": "Dashboard", "desc": "Shows today's timetable classes and overall attendance percentage.", "keywords": "dashboard home main page"},
            {"name": "Attendance", "desc": "Detailed subject-wise breakdown of present/absent classes.", "keywords": "attendance present absent classes percentage bunk bunking"},
            {"name": "Biometric", "desc": "Daily punch-in/punch-out logs and timings.", "keywords": "biometric punch in out time gate logs"},
            {"name": "Results", "desc": "Exam performance, Midmarks, SGPA and CGPA.", "keywords": "results marks grades sgpa cgpa exam midmarks"},
            {"name": "Labs", "desc": "Upload and manage lab records and assignments.", "keywords": "labs laboratory record assignment submission"},
            {"name": "AAT", "desc": "Alternative Assessment Tools submission (an AI assignment solver is built-in!).", "keywords": "aat assignment alternative assessment tool solve ai"},
            {"name": "Memos", "desc": "Official college notices and circulars.", "keywords": "memos notices circulars news official note board"},
            {"name": "Anonymous Chat", "desc": "Chat safely with peers without revealing identity.", "keywords": "anonymous chat message secret peers"}
        ]
        for feat in features:
            text = f"{feat['name']} {feat['desc']} {feat['keywords']}"
            self.features_index.add_document(text, feat)
        print(f"[UnifiedRAG] Indexed {self.features_index.N} app features.")

    def load_learned_memory(self, path):
        try:
            if not os.path.exists(path):
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            with open(path, 'r', encoding='utf-8') as f:
                facts = json.load(f)
                for fact in facts:
                    self.learned_index.add_document(fact.get("text", ""), fact)
            print(f"[UnifiedRAG] Indexed {self.learned_index.N} dynamically learned facts.")
        except Exception as e:
            print(f"[UnifiedRAG] Error loading learned memory: {e}")

    def learn_new_fact(self, fact_text):
        if not fact_text.strip():
            return
            
        fact_obj = {"text": fact_text.strip()}
        
        # Add to live index immediately
        self.learned_index.add_document(fact_text, fact_obj)
        print(f"[UnifiedRAG] Dynamically learned new fact! Index size: {self.learned_index.N}")
        
        # Persist to disk
        try:
            facts = []
            if os.path.exists(self.learned_memory_path):
                with open(self.learned_memory_path, 'r', encoding='utf-8') as f:
                    try:
                        facts = json.load(f)
                    except:
                        pass
            
            facts.append(fact_obj)
            with open(self.learned_memory_path, 'w', encoding='utf-8') as f:
                json.dump(facts, f, indent=2)
        except Exception as e:
            print(f"[UnifiedRAG] Error saving learned memory to disk: {e}")
            
        # Push to GitHub API in background
        import threading
        threading.Thread(target=self._push_to_github_api, args=(fact_obj,)).start()

    def _push_to_github_api(self, fact_obj):
        pat = os.environ.get("GITHUB_PAT")
        if not pat:
            print("[UnifiedRAG] GITHUB_PAT not set. Skipping GitHub API sync.")
            return
            
        import requests
        import base64
        
        url = "https://api.github.com/repos/nithin-chatla/samvidha-backend/contents/learned_memory.json"
        headers = {
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            # 1. Get current file
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                sha = data['sha']
                content_b64 = data['content']
                content_str = base64.b64decode(content_b64).decode('utf-8')
                try:
                    facts = json.loads(content_str)
                except:
                    facts = []
            elif res.status_code == 404:
                sha = None
                facts = []
            else:
                print(f"[UnifiedRAG] GitHub GET failed: {res.status_code} {res.text}")
                return
                
            # 2. Append new fact
            facts.append(fact_obj)
            new_content_str = json.dumps(facts, indent=2)
            new_content_b64 = base64.b64encode(new_content_str.encode('utf-8')).decode('utf-8')
            
            # 3. Update file
            payload = {
                "message": f"Auto-learned fact: {fact_obj.get('text', '')[:30]}...",
                "content": new_content_b64
            }
            if sha:
                payload["sha"] = sha
                
            put_res = requests.put(url, headers=headers, json=payload)
            if put_res.status_code in [200, 201]:
                print("[UnifiedRAG] Successfully synced learned fact to GitHub!")
            else:
                print(f"[UnifiedRAG] GitHub PUT failed: {put_res.status_code} {put_res.text}")
                
        except Exception as e:
            print(f"[UnifiedRAG] GitHub API sync exception: {e}")

    def dynamic_user_data_rag(self, query, user_data):
        if not user_data:
            return {}
            
        user_index = BM25Index()
        
        if 'profile' in user_data:
            user_index.add_document("profile name roll number branch section details id email", {"profile": user_data['profile']})
        if 'attendance' in user_data:
            user_index.add_document("attendance classes present absent bunk percentage subject subjects", {"attendance": user_data['attendance']})
        if 'timetable' in user_data:
            user_index.add_document("timetable schedule classes periods time today room block", {"timetable": user_data['timetable']})
        if 'results' in user_data:
            user_index.add_document("results marks cgpa sgpa grades exams midmarks", {"results": user_data['results']})
        if 'biometric' in user_data:
            user_index.add_document("biometric punch gate timings entry exit", {"biometric": user_data['biometric']})
        if 'fees' in user_data:
            user_index.add_document("fees payment due amount paid tuition", {"fees": user_data['fees']})
            
        relevant_chunks = user_index.retrieve(query, top_k=2, min_score=0.1)
        
        filtered_user_data = {}
        for chunk in relevant_chunks:
            filtered_user_data.update(chunk)
            
        return filtered_user_data

    def build_prompt_context(self, query, user_data=None):
        memory_chunks = self.memory_index.retrieve(query, top_k=2)
        faculty_chunks = self.faculty_index.retrieve(query, top_k=1)
        feature_chunks = self.features_index.retrieve(query, top_k=2)
        learned_chunks = self.learned_index.retrieve(query, top_k=2)
        filtered_user_data = self.dynamic_user_data_rag(query, user_data)
        
        context_parts = []
        
        if learned_chunks:
            context_parts.append("--- DYNAMICALLY LEARNED FACTS ---\n" + json.dumps(learned_chunks, indent=2) + "\n---------------------------------")
        
        if feature_chunks:
            feat_lines = [f"- {f['name']}: {f['desc']}" for f in feature_chunks]
            context_parts.append("--- SAMVIDHA APP FEATURES ---\nIf the user asks about app features, guide them using these tools:\n" + "\n".join(feat_lines) + "\n-----------------------------")
            
        if memory_chunks:
            context_parts.append("--- COLLEGE KNOWLEDGE / MEMORY ---\n" + json.dumps(memory_chunks, indent=2) + "\n----------------------------------")
            
        if faculty_chunks:
            context_parts.append("--- RELEVANT FACULTY DATA ---\n" + json.dumps(faculty_chunks, indent=2) + "\nCRITICAL INSTRUCTION: When answering questions about a faculty member, you MUST ALWAYS include their full Designation, Department, Email, and provide their profile_url as a link. If the user misspelled the name, politely clarify that you found information for the closest matching faculty member.\n-----------------------------")
            
        if filtered_user_data:
            context_parts.append("--- STUDENT DATA CONTEXT ---\n" + json.dumps(filtered_user_data, indent=2) + "\n----------------------------")
            
        return "\n\n".join(context_parts)
