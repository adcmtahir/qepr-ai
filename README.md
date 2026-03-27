# QE Test Plan Reviewer — Setup & Usage Guide

Paste three Confluence URLs, click **Analyze**, and get a prioritized list of test coverage gaps in minutes — no copy-pasting, no manual prompting.

---

## Prerequisites

- Python 3.10+ installed
- Access to your company's Confluence instance (you must be able to log in)
- A GitHub account (free tier is enough)

---

## 1. Install Dependencies

Open a terminal in the `QEPR-AI` folder and run:

```
pip install -r requirements.txt
```

---

## 2. Get Your Confluence Personal Access Token

This lets the tool fetch pages on your behalf without needing your password.

1. Log in to Confluence in your browser
2. Click your **profile picture** (top-right) → **Settings** (or **Profile**)
3. In the left sidebar click **Personal Access Tokens**
4. Click **Create token**
   - Name it something like `QE Reviewer`
   - Set expiry as desired (90 days is fine)
   - Leave permissions as default (read-only is sufficient)
5. Click **Create** and **copy the token immediately** — it won't be shown again
6. Save it somewhere safe (you'll paste it into the tool's Settings)

> **Note:** If you don't see "Personal Access Tokens" in your profile, ask your Confluence admin — some instances require it to be enabled.

---

## 3. Get Your GitHub Personal Access Token (Free AI)

GitHub Models gives you free access to GPT-4o-mini. You need a GitHub PAT to use it.

1. Go to [github.com](https://github.com) and log in
2. Click your **profile picture** (top-right) → **Settings**
3. Scroll down the left sidebar and click **Developer settings**
4. Click **Personal access tokens** → **Tokens (classic)**
5. Click **Generate new token** → **Generate new token (classic)**
   - Note: `QE Reviewer`
   - Expiration: 90 days (or no expiration)
   - **Scopes: you don't need to check anything** — GitHub Models only requires a valid token, no specific scopes
6. Click **Generate token** and **copy it immediately**

> **Tip:** If you already have a GitHub PAT lying around, it will work as-is — no need to create a new one.

---

## 4. Start the Server

In a terminal in the `QEPR-AI` folder:

```
python server.py
```

You should see:

```
  QE Test Plan Reviewer
  ------------------------------------
  Open:  http://localhost:5000
  Stop:  Ctrl+C
```

Open **http://localhost:5000** in your browser.

> The server must stay running while you use the tool. Keep the terminal open.

---

## 5. Configure the Tool (First Time Only)

1. Click the **Settings** button (top-right of the page)
2. Fill in:
   - **AI Provider**: leave as `GitHub Models (Free)`
   - **GitHub Token**: paste the token from Step 3
   - **Confluence Token**: paste the token from Step 2
3. Click **Save Settings**

Your credentials are saved locally to a `.env` file — you won't need to re-enter them next time.

---

## 6. Run an Analysis

1. Open the Confluence page for your **Spec** in a browser tab and copy the URL from the address bar
2. Do the same for your **Test Plan**
3. Optionally do the same for a **Design Document** (check the "Include Design" box first)
4. Paste the URLs into the respective fields
5. Click **Analyze Test Plan**

> **How to get the right Confluence URL:**  
> Make sure you are already logged in to Confluence. The URL should look like:  
> `https://yourcompany.atlassian.net/wiki/spaces/SPACE/pages/123456789/Page+Title`  
> Do **not** copy a URL that contains `okta.com` or `SAMLRequest` — that is a login redirect, not the page itself.

---

## 7. Reading the Results

Results are grouped into expandable cards, sorted by severity:

| Severity | Meaning |
|---|---|
| 🔴 High | Likely to cause a production bug or missed critical path if untested |
| 🟡 Medium | Gap that reduces confidence or could cause regression |
| 🟢 Low | Minor improvement or nice-to-have |

Each card shows:
- **Issue** — what is missing from the test plan
- **Reason** — why it matters
- **Reference** — the exact section and quote from the spec/design doc
- **Suggestion** — a concrete test you can add right now

---

## 8. Performance Expectations

| Provider | Analysis time | Notes |
|---|---|---|
| GitHub Models (free) | 1–3 minutes | Sends ~10–15 small API calls to stay within the free 8k token limit |
| OpenAI (gpt-4o) | ~30 seconds | Sends full docs in one call; requires paid API key |
| Anthropic (claude) | ~30 seconds | Sends full docs in one call; requires paid API key |

---

## Switching to OpenAI or Anthropic (Optional)

For faster analysis or very long documents, you can use a paid provider:

1. Click **Settings**
2. Change **AI Provider** to `OpenAI` or `Anthropic`
3. Paste your API key:
   - OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
   - Anthropic: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
4. Click **Save Settings**

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Confluence authentication failed` | Your Confluence token expired or is wrong — regenerate it (Step 2) |
| `Confluence page not found` | Make sure you're logged into Confluence first, then re-copy the URL |
| `GitHub token invalid` | Regenerate your GitHub PAT (Step 3) |
| `Cannot connect to Confluence` | Make sure you're on the company network or VPN |
| `AI returned invalid JSON` | Retry — occasional model glitch; usually resolves on second attempt |
| Server won't start | Another process is using port 5000. Run: `netstat -ano \| findstr :5000` then `taskkill /F /PID <pid>` |

---

## File Reference

```
QEPR-AI/
├── server.py          # Flask backend — runs the local server
├── index.html         # Web UI — open in browser via localhost:5000
├── requirements.txt   # Python dependencies
├── .env               # Auto-created when you save Settings (contains your tokens)
└── README.md          # This file
```

> **Security note:** The `.env` file contains your API keys. Do not share it or commit it to source control.
