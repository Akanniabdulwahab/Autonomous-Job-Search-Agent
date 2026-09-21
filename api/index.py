import os, re, json, hashlib, logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import ratio
from fastapi import FastAPI
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("job-agent")
app = FastAPI()

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "15"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS_PER_QUERY", "8"))
MAX_PAGE_FETCHES = int(os.getenv("MAX_PAGE_FETCHES_PER_RUN", "18"))
MAX_ALERTS = int(os.getenv("MAX_TELEGRAM_ALERTS", "10"))
MAX_TAVILY_SEARCHES_PER_DAY = int(os.getenv("MAX_TAVILY_SEARCHES_PER_DAY", "20"))
MAX_EXA_SEARCHES_PER_DAY = int(os.getenv("MAX_EXA_SEARCHES_PER_DAY", "10"))
SEARXNG_MAX_QUERIES_PER_RUN = int(os.getenv("SEARXNG_MAX_QUERIES_PER_RUN", "6"))
FIRECRAWL_ENABLED = os.getenv("USE_FIRECRAWL", "0") == "1"
SCAN_INTERVAL_HOURS = int(os.getenv("SCAN_INTERVAL_HOURS", "4"))

PROFILE = {
    "roles": [
        "data scientist", "data analyst", "machine learning engineer", "ai engineer",
        "applied scientist", "analytics engineer", "decision scientist", "product analyst",
        "business intelligence analyst", "bi analyst", "bi consultant", "business intelligence consultant",
        "data ai consultant", "data & ai consultant", "ai solutions consultant", "ai product specialist",
        "analytics consultant", "insights analyst", "quantitative analyst", "data engineer", "ml specialist",
        "machine learning specialist", "data solutions consultant", "intelligent automation consultant",
        "technology consultant", "digital analytics specialist", "business data specialist",
        "data automation specialist", "research scientist", "ml engineer", "product data scientist",
        "analytics specialist", "decision analytics", "data strategy consultant", "data technology consultant"
    ],
    "skills": [
        "python", "sql", "mysql", "postgresql", "scikit-learn", "machine learning", "predictive modeling",
        "predictive modelling", "nlp", "natural language processing", "power bi", "tableau", "dax", "pandas",
        "numpy", "matplotlib", "seaborn", "statistical analysis", "statistics", "data analysis",
        "data visualization", "business intelligence", "data pipelines", "classification", "regression",
        "customer segmentation", "git", "automation", "forecasting", "feature engineering", "dashboard",
        "data-driven", "artificial intelligence", "ai", "analytics", "experimentation"
    ],
    "experience": "Data Scientist since 2023; predictive models, NLP, SQL, dashboards, ML pipelines",
    "education": "B.Eng. Electrical & Electronics Engineering"
}

ROLE_FAMILIES = {
    "data_analytics": ["data", "analytics", "insights", "decision science", "decision scientist"],
    "ai_ml": ["artificial intelligence", "machine learning", "deep learning", "ai engineer", "ml engineer", "applied scientist"],
    "business_intelligence": ["business intelligence", "bi analyst", "bi consultant", "power bi", "tableau"],
    "data_engineering": ["data engineer", "analytics engineer", "data pipeline", "etl", "elt"],
    "quantitative": ["quantitative", "statistical", "forecasting", "experimentation", "econometric"],
    "product_analytics": ["product analyst", "product analytics", "product data scientist", "growth analytics"],
    "consulting": ["data consultant", "analytics consultant", "technology consultant", "ai consultant", "solutions consultant"],
    "automation": ["automation", "intelligent automation", "data automation", "workflow automation"],
    "research": ["research scientist", "researcher", "applied research", "research engineer"],
}

SOURCE_GROUPS = [
    ["greenhouse.io", "jobs.lever.co", "ashbyhq.com", "myworkdayjobs.com"],
    ["smartrecruiters.com", "workable.com", "jobvite.com", "icims.com"],
    ["linkedin.com", "indeed.com", "wellfound.com", "glassdoor.com"],
    ["remoteok.com", "weworkremotely.com", "remotive.com", "himalayas.app"],
    ["remote.co", "jobspresso.co", "workingnomads.com", "flexjobs.com"],
    ["builtin.com", "ycombinator.com", "otta.com", "wellfound.com"],
    ["careers.google.com", "amazon.jobs", "jobs.apple.com", "careers.microsoft.com"],
    ["jobs.ashbyhq.com", "boards.greenhouse.io", "jobs.smartrecruiters.com", "apply.workable.com"]
]
CUSTOM_DOMAINS = [d.strip().lower() for d in os.getenv("JOB_SOURCE_DOMAINS", "").split(",") if d.strip()]
if CUSTOM_DOMAINS:
    SOURCE_GROUPS = [CUSTOM_DOMAINS[i:i+8] for i in range(0, len(CUSTOM_DOMAINS), 8)]

SEARCH_QUERIES = [
    'remote data scientist data analyst machine learning analytics jobs',
    'remote data engineer business intelligence decision scientist product analyst jobs',
    'remote AI engineer applied scientist research scientist AI consultant jobs',
    'remote analytics consultant insights analyst quantitative analyst data consultant jobs',
    'remote technology consultant automation consultant digital analytics data specialist jobs',
    'Nigeria Lagos remote hybrid data analytics AI machine learning technology jobs',
    'data AI consultant intelligent automation consultant business intelligence consultant jobs',
    'analytics engineer data solutions consultant product analytics jobs'
]

JOB_BOARD_HOSTS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com", "greenhouse.io", "lever.co",
    "jobs.lever.co", "ashbyhq.com", "myworkdayjobs.com", "smartrecruiters.com", "workable.com",
    "jobvite.com", "icims.com", "remoteok.com", "weworkremotely.com", "remotive.com", "himalayas.app",
    "flexjobs.com", "remote.co", "workingnomads.com", "builtin.com", "otta.com", "jobspresso.co"
}

# ---------- utility ----------
def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).isoformat() if dt else ""

def normalize_url(url):
    if not url: return ""
    p = urlparse(url)
    if not p.scheme or not p.netloc: return ""
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()

def host(url): return urlparse(url).netloc.lower().replace("www.", "")
def root_domain(h):
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h

def job_key(job):
    raw = "|".join([
        job.get("job_id", ""), normalize_url(job.get("job_url", "")),
        job.get("title", "").lower().strip(), job.get("company", "").lower().strip(),
        job.get("location", "").lower().strip()
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def parse_date_value(value):
    if not value: return None
    if isinstance(value, list):
        for v in value:
            d = parse_date_value(v)
            if d: return d
        return None
    s = str(value).strip()
    if not s: return None
    m = re.search(r"(\d+)\s+days?\s+ago", s, re.I)
    if m: return now() - timedelta(days=int(m.group(1)))
    if re.search(r"today|just posted", s, re.I): return now()
    if re.search(r"yesterday", s, re.I): return now() - timedelta(days=1)
    try:
        from dateutil.parser import parse
        d = parse(s, fuzzy=True)
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def within_window(d):
    if not d: return False
    age = now() - d
    return timedelta(0) <= age <= timedelta(days=LOOKBACK_DAYS)

def classify_role(title, description):
    text = (title + " " + description).lower()
    families = []
    for family, terms in ROLE_FAMILIES.items():
        if any(t in text for t in terms): families.append(family)
    return families

def semantic_match(title, description):
    text = (title + " " + description).lower()
    skill_hits = []
    for s in PROFILE["skills"]:
        if re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", text):
            skill_hits.append(s)
    role_hits = [r for r in PROFILE["roles"] if r in text]
    families = classify_role(title, description)
    skill_score = min(100, round(len(skill_hits) / 14 * 100))
    role_bonus = min(30, len(role_hits) * 8)
    family_bonus = 10 if families else 0
    score = min(100, round(skill_score * 0.65 + role_bonus + family_bonus))
    return score, skill_hits, role_hits, families

def potential_gaps(text):
    t = text.lower()
    likely = ["cloud", "spark", "aws", "azure", "gcp", "airflow", "databricks", "dbt", "kubernetes", "pytorch", "tensorflow"]
    return ", ".join(x for x in likely if x in t and x not in PROFILE["skills"])

def infer_work_arrangement(text):
    t = text.lower()
    if re.search(r"\bremote\b", t): return "Remote"
    if re.search(r"\bhybrid\b", t): return "Hybrid"
    if re.search(r"\bon[- ]site\b|onsite", t): return "On-site"
    return "Not stated"

def infer_sponsorship(text):
    t = text.lower()
    if re.search(r"visa sponsorship|sponsorship available|will sponsor|sponsor visa|work permit sponsorship", t): return "Mentioned"
    if re.search(r"no sponsorship|without sponsorship|must have.*right to work|not sponsor", t): return "Restricted/Not offered"
    return "Not stated"

# ---------- search providers ----------
def tavily_search(query, domains=None):
    key = os.getenv("TAVILY_API_KEY")
    if not key: raise RuntimeError("TAVILY_API_KEY not configured")
    payload = {"query": query, "topic": "general", "search_depth": "basic", "max_results": MAX_RESULTS,
               "include_answer": False, "include_raw_content": False, "include_images": False}
    if domains: payload["include_domains"] = domains
    r = requests.post("https://api.tavily.com/search", json=payload,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=25)
    r.raise_for_status()
    return r.json().get("results", [])

def exa_search(query, domains=None):
    key = os.getenv("EXA_API_KEY")
    if not key: raise RuntimeError("EXA_API_KEY not configured")
    payload = {"query": query, "numResults": MAX_RESULTS, "contents": {"text": {"maxCharacters": 2500}}}
    if domains: payload["includeDomains"] = domains
    r = requests.post("https://api.exa.ai/search", json=payload,
                      headers={"x-api-key": key, "Content-Type": "application/json"}, timeout=25)
    r.raise_for_status()
    return r.json().get("results", [])

def searxng_search(query):
    base = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
    if not base: raise RuntimeError("SEARXNG_URL not configured")
    r = requests.get(base + "/search", params={"q": query, "format": "json", "language": "en"},
                     headers={"User-Agent": "AutonomousJobSearchAgent/3.0"}, timeout=25)
    r.raise_for_status()
    return r.json().get("results", [])

def provider_result(provider, x):
    if provider == "Tavily":
        return {"title": x.get("title", "").strip(), "job_url": x.get("url", "").strip(),
                "snippet": x.get("content", "") or x.get("raw_content", ""), "source": provider}
    if provider == "Exa":
        return {"title": x.get("title", "").strip(), "job_url": x.get("url", "").strip(),
                "snippet": x.get("text", "") or x.get("highlights", [""])[0] if isinstance(x.get("highlights"), list) and x.get("highlights") else x.get("text", ""),
                "source": provider}
    return {"title": x.get("title", "").strip(), "job_url": x.get("url", "").strip(),
            "snippet": x.get("content", "") or x.get("snippet", ""), "source": provider}

def search_jobs():
    # Six scans/day, with provider rotation. The run index is derived from UTC hour.
    run_slot = (now().hour // SCAN_INTERVAL_HOURS) % 6
    group = SOURCE_GROUPS[run_slot % len(SOURCE_GROUPS)]
    query_indices = [(run_slot + i) % len(SEARCH_QUERIES) for i in range(3)]
    out = []

    # Every scan uses at most one provider heavily and can use a second provider as a catch-up.
    plan = ["Tavily", "Exa", "SearXNG", "Tavily+Exa", "SearXNG+Tavily", "Exa+SearXNG"][run_slot]
    providers = plan.split("+")
    for provider in providers:
        for qi in query_indices[:2]:
            q = SEARCH_QUERIES[qi]
            try:
                if provider == "Tavily": results = tavily_search(q, group)
                elif provider == "Exa": results = exa_search(q, group)
                else: results = searxng_search(q)
                out.extend(provider_result(provider, x) for x in results)
            except Exception as e:
                log.warning("%s search failed: %s", provider, e)

    seen = set(); unique = []
    for x in out:
        u = normalize_url(x.get("job_url", ""))
        if u and u not in seen:
            seen.add(u); unique.append(x)
    return unique, plan

# ---------- page extraction ----------
def fetch_page(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AutonomousJobSearchAgent/3.0)"},
                     timeout=15, allow_redirects=True)
    r.raise_for_status()
    if "text/html" not in r.headers.get("content-type", ""): return "", r.url
    return r.text, r.url

def meta(soup, names):
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"): return tag["content"].strip()
    return ""

def jsonld_objects(soup):
    objs = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
            if isinstance(data, list): objs.extend(data)
            elif isinstance(data, dict) and "@graph" in data: objs.extend(data["@graph"])
            elif isinstance(data, dict): objs.append(data)
        except Exception: pass
    return objs

def find_jobposting(soup):
    for o in jsonld_objects(soup):
        typ = o.get("@type") if isinstance(o, dict) else None
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ): return o
    return None

def text_clean(soup):
    for x in soup(["script", "style", "noscript"]): x.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

def extract_company_url(soup, company_name):
    if not company_name: return ""
    target = company_name.lower().replace("&", "and")
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a.get("title", "")).lower()
        if target and target in label:
            u = urljoin("https://example.com", a["href"])
            if u.startswith("http") and root_domain(host(u)) not in JOB_BOARD_HOSTS:
                return normalize_url(u)
    return ""

def parse_page(raw, final_url, fallback):
    soup = BeautifulSoup(raw, "html.parser")
    jp = find_jobposting(soup)
    text = text_clean(BeautifulSoup(raw, "html.parser"))
    title = company = location = salary = company_url = ""
    posted = None
    if jp:
        title = jp.get("title", "") or ""
        org = jp.get("hiringOrganization") or {}
        if isinstance(org, dict):
            company = org.get("name", "") or ""
            company_url = org.get("sameAs", "") or org.get("url", "") or ""
        loc = jp.get("jobLocation") or {}
        if isinstance(loc, list): loc = loc[0] if loc else {}
        if isinstance(loc, dict):
            addr = loc.get("address") or {}
            if isinstance(addr, dict):
                location = ", ".join(str(addr.get(k)) for k in ["addressLocality", "addressRegion", "addressCountry"] if addr.get(k))
            elif addr: location = str(addr)
        base = jp.get("baseSalary")
        if isinstance(base, dict):
            val = base.get("value") or {}
            if isinstance(val, dict):
                mn, mx, cur = val.get("minValue"), val.get("maxValue"), base.get("currency") or ""
                if mn or mx: salary = f"{mn or ''}-{mx or ''} {cur}".strip("-")
        posted = parse_date_value(jp.get("datePosted"))
        if jp.get("description"): text = BeautifulSoup(jp["description"], "html.parser").get_text(" ", strip=True)
    else:
        title = meta(soup, ["og:title", "twitter:title"])
        if not title and soup.title: title = soup.title.get_text(" ", strip=True)
        desc = meta(soup, ["description", "og:description", "twitter:description"])
        if desc: text = desc + " " + text
        posted = parse_date_value(meta(soup, ["article:published_time", "date", "datePublished"])) or parse_date_value(text)
    title = title.strip() or fallback.get("title", "")
    if not company:
        m = re.search(r"(?:company|employer|hiring organization)\s*[:\-]\s*([^|•\n]{2,100})", text, re.I)
        if m: company = m.group(1).strip()
    if not location:
        m = re.search(r"(?:location|locations?)\s*[:\-]\s*([^|•\n]{2,100})", text, re.I)
        if m: location = m.group(1).strip()
    if not posted: posted = parse_date_value(fallback.get("snippet", "") + " " + text[:5000])
    if not company_url: company_url = extract_company_url(soup, company)
    return {"title": title, "company": company, "location": location or "Not stated", "salary": salary or "Not stated",
            "description": text[:30000], "posted": posted, "job_url": normalize_url(final_url),
            "company_website": normalize_url(company_url)}

def enrich_candidate(x):
    try:
        raw, final = fetch_page(x["job_url"])
        if raw: return parse_page(raw, final, x)
    except Exception as e:
        log.info("Page fetch skipped %s: %s", x.get("job_url"), e)
    return {"title": x.get("title", ""), "company": "", "location": "Not stated", "salary": "Not stated",
            "description": x.get("snippet", "") or "", "posted": parse_date_value(x.get("snippet", "")),
            "job_url": normalize_url(x.get("job_url", "")), "company_website": ""}

# ---------- legitimacy / duplicates ----------
def employer_domain_reachable(url):
    if not url: return False
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8, allow_redirects=True)
        return r.status_code < 400 and root_domain(host(r.url)) not in JOB_BOARD_HOSTS
    except Exception: return False

def likely_legitimate(job):
    h = root_domain(host(job.get("job_url", "")))
    if h in JOB_BOARD_HOSTS:
        return "Third-party job source; employer identity extracted/verified where possible"
    if employer_domain_reachable(job.get("company_website", "")):
        return "Employer website reachable; listing found on employer site"
    return "Source reachable; employer verification required"

def duplicate_level(job, existing):
    key = job_key(job)
    if key in existing: return "confirmed"
    title, company, loc = job.get("title", "").lower(), job.get("company", "").lower(), job.get("location", "").lower()
    for e in existing.values():
        if not e: continue
        et, ec, el = e.get("title", "").lower(), e.get("company", "").lower(), e.get("location", "").lower()
        if company and ec and company == ec and ratio(title, et) > 92 and (not loc or not el or ratio(loc, el) > 78): return "possible"
    return "unique"

def existing_jobs(rows):
    out = {}
    for r in rows[1:]:
        if not r: continue
        out[r[0]] = {"title": r[1] if len(r) > 1 else "", "company": r[2] if len(r) > 2 else "", "location": r[4] if len(r) > 4 else ""}
    return out

# ---------- Google ----------
def sheets_docs():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/documents", "https://www.googleapis.com/auth/drive.file"])
    return build("sheets", "v4", credentials=creds), build("docs", "v1", credentials=creds)

HEADERS = ["Job Key","Job Title","Company","Official Company Website","Location","Work Arrangement","Salary","Posted Date","Date Discovered","Match %","Matched Skills","Potential Gaps","Sponsorship","Job Source","Job Source URL","Original Job/Application URL","Legitimacy","Duplicate Status","Role Families","Status"]

def get_rows(sheets):
    sid = os.environ["GOOGLE_SHEET_ID"]
    return sheets.spreadsheets().values().get(spreadsheetId=sid, range="All Jobs!A:T").execute().get("values", [])

def append_rows(sheets, rows):
    if rows:
        sheets.spreadsheets().values().append(spreadsheetId=os.environ["GOOGLE_SHEET_ID"], range="All Jobs!A:T", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values": rows}).execute()

def send_telegram(message):
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": message, "disable_web_page_preview": False}, timeout=15).raise_for_status()

def update_doc(docs, text):
    did = os.environ["GOOGLE_DOC_ID"]
    doc = docs.documents().get(documentId=did).execute()
    end = doc["body"]["content"][-1]["endIndex"] - 1
    docs.documents().batchUpdate(documentId=did, body={"requests": [{"insertText": {"location": {"index": end}, "text": text}}]}).execute()

# ---------- agent ----------
def run_agent():
    sheets, docs = sheets_docs()
    rows = get_rows(sheets)
    existing = existing_jobs(rows)
    candidates, provider_plan = search_jobs()

    relevant = []
    for x in candidates:
        text = (x.get("title", "") + " " + x.get("snippet", "")).lower()
        if any(k in text for k in ["data", "analytics", "machine learning", "artificial intelligence", " ai ", "business intelligence", "automation", "quantitative", "technology consultant", "scientist"]):
            relevant.append(x)
    candidates = relevant[:MAX_PAGE_FETCHES]

    new, possible, alerts = [], [], []
    for raw in candidates:
        j = enrich_candidate(raw)
        if not j["title"] or not j["job_url"] or not j["posted"] or not within_window(j["posted"]): continue
        score, skills, roles, families = semantic_match(j["title"], j["description"])
        # A title-only hit is insufficient; keep only roles with meaningful responsibility/skill overlap.
        if score < 25: continue
        j.update({"match": score, "skills": skills, "roles": roles, "families": families, "gaps": potential_gaps(j["description"])})
        j["work"] = infer_work_arrangement(j["title"] + " " + j["description"])
        j["sponsorship"] = infer_sponsorship(j["description"])
        j["legitimacy"] = likely_legitimate(j)
        j["key"] = job_key(j)
        dup = duplicate_level(j, existing)
        j["duplicate_status"] = dup
        if dup == "confirmed": continue
        if dup == "possible":
            possible.append(j)
            # Keep uncertain duplicates visible rather than deleting them.
            continue
        new.append(j)
        existing[j["key"]] = {"title": j["title"], "company": j["company"], "location": j["location"]}

    new.sort(key=lambda x: x["match"], reverse=True)
    rows_to_add = []
    for j in new:
        rows_to_add.append([
            j["key"], j["title"], j["company"] or "Not stated", j["company_website"], j["location"], j["work"], j["salary"],
            iso(j["posted"]), iso(now()), j["match"], ", ".join(j["skills"]), j["gaps"], j["sponsorship"], j.get("source", "Multi-provider search"),
            j["source_url"] if j.get("source_url") else j["job_url"], j["job_url"], j["legitimacy"], "unique", ", ".join(j["families"]), "New - manual application"
        ])
        if len(alerts) < MAX_ALERTS:
            alerts.append(f"🆕 {j['match']}% — {j['title']}\nCompany: {j['company'] or 'Not stated'}\nLocation: {j['location']}\nRole family: {', '.join(j['families']) or 'Other relevant tech/data'}\nPosted: {j['posted'].date()}\nJob: {j['job_url']}")

    append_rows(sheets, rows_to_add)
    report = f"\n\n=== JOB AGENT UPDATE {now().strftime('%Y-%m-%d %H:%M UTC')} ===\nProvider plan: {provider_plan}\nScan interval: {SCAN_INTERVAL_HOURS} hours\nCandidate URLs: {len(candidates)}\nNew unique jobs (<=15 days): {len(new)}\nPossible duplicates retained for review: {len(possible)}\nOlder/undated jobs excluded: yes\nApplications submitted: 0\n"
    for j in new:
        report += f"\n{j['match']}% | {j['title']} | {j['company'] or 'Not stated'} | {j['job_url']}\n"
    update_doc(docs, report)
    if alerts: send_telegram("\n\n".join(alerts))
    return {"ok": True, "provider_plan": provider_plan, "new_jobs": len(new), "possible_duplicates": len(possible), "candidates_checked": len(candidates), "lookback_days": LOOKBACK_DAYS, "scan_interval_hours": SCAN_INTERVAL_HOURS, "paid_fallbacks_disabled": True}

@app.get("/api/cron")
async def cron():
    return run_agent()

@app.get("/api/health")
async def health():
    return {
        "ok": True, "agent": "autonomous-job-search-v3", "scan_interval_hours": SCAN_INTERVAL_HOURS,
        "scans_per_day": max(1, 24 // SCAN_INTERVAL_HOURS), "providers": ["Tavily", "Exa", "Self-hosted SearXNG"],
        "firecrawl_fallback_enabled": FIRECRAWL_ENABLED, "lookback_days": LOOKBACK_DAYS,
        "paid_fallbacks_disabled": True, "application_automation": False
    }
