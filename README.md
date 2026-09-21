# Autonomous Job Search Agent v3 — strict free-first design

This version expands the previous Tavily-only starter into a multi-provider architecture:

- Tavily — primary web discovery
- Exa — secondary independent web discovery
- Self-hosted SearXNG — free metasearch layer
- Direct HTTP/JSON-LD extraction — primary page parsing
- Firecrawl — optional fallback, disabled by default
- Google Sheets — long-term job database
- Google Docs — readable reports
- Telegram — notifications
- GitHub Actions — six scans/day (every 4 hours)

## Important $0 policy

There is **no paid fallback and no automatic upgrade**. Keep `USE_FIRECRAWL=0` unless you intentionally decide to use its free allowance. Do not attach billing to the search accounts.

## Job-discovery philosophy

The agent does NOT depend on exact titles such as "Data Scientist". It searches and classifies data, analytics, AI, ML, BI, quantitative, product analytics, consulting, automation, research, and technology roles. It then reads responsibilities and required skills and matches them against the user's CV.

Examples include Decision Scientist, Analytics Consultant, Product Analyst, BI Consultant, Insights Analyst, Quantitative Analyst, Data & AI Consultant, AI Solutions Consultant, Applied Scientist, Research Scientist, Machine Learning Specialist, AI Engineer, AI Product Specialist, Analytics Engineer, Data Solutions Consultant, Technology Consultant, Digital Analytics Specialist, Business Data Specialist, Data & Automation Specialist, and Intelligent Automation Consultant.

A new title can also be relevant if its actual responsibilities strongly overlap with the user's data/AI/technology profile.

## Hard rules

- Worldwide jobs.
- Remote worldwide; Nigeria remote/hybrid and relevant local roles.
- Maximum job age: 15 days.
- Jobs with no reliable posting date are excluded from the active pool.
- All legitimate unique jobs remain in the database, including lower-match jobs.
- Confirmed duplicates are skipped; possible duplicates are flagged rather than silently deleted.
- Match score is for prioritization, not exclusion, subject to the minimum relevance check.
- The agent never applies, submits forms, uploads CVs, sends recruiter messages, or accepts/rejects offers.
- Application links are provided for manual action only.

## Schedule

GitHub Actions runs at 00:00, 04:00, 08:00, 12:00, 16:00, and 20:00 UTC, which is 01:00, 05:00, 09:00, 13:00, 17:00, and 21:00 in Nigeria (WAT).

## Environment variables

Copy `.env.example` and supply secrets through GitHub Actions Secrets or your deployment platform. Never commit API keys or Telegram tokens.

Required search secrets:

- `TAVILY_API_KEY`
- `EXA_API_KEY`
- `SEARXNG_URL`

Existing Google/Telegram values:

- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SHEET_ID`
- `GOOGLE_DOC_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## SearXNG

The official SearXNG documentation recommends Docker Compose for container deployment. This repository intentionally does not assume a particular hosting provider is permanently free; the SearXNG URL is supplied as a secret so a genuinely free host can be selected and changed later.

## Testing

Run:

```bash
python -m py_compile api/index.py
```

Then configure secrets and run the GitHub Actions workflow manually once before relying on the schedule.
