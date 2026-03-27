"""
QE Test Plan Reviewer — Local Proxy Server
==========================================
Run:  python server.py
Open: http://localhost:5000

Credentials are read from (highest priority first):
  1. Request headers sent by the browser UI  (X-Confluence-Token, X-Anthropic-Key)
  2. .env file  (CONFLUENCE_TOKEN, ANTHROPIC_API_KEY)
"""

import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app)


# ─── HTML → plain text ────────────────────────────────────────────────────────

class _Stripper(HTMLParser):
    SKIP_TAGS = {"script", "style", "head"}
    BLOCK_TAGS = {"p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "div", "td"}

    def __init__(self):
        super().__init__()
        self._buf: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, _attrs):
        if tag in self.SKIP_TAGS:
            self._skip = True
        if tag in self.BLOCK_TAGS:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def result(self) -> str:
        text = "".join(self._buf)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(html: str) -> str:
    s = _Stripper()
    s.feed(html)
    return s.result()


# ─── Confluence helpers ────────────────────────────────────────────────────────

def _extract_page_id(url: str) -> str | None:
    """Pull a numeric page ID out of any common Confluence URL pattern."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "pageId" in qs:
        return qs["pageId"][0]
    m = re.search(r"/pages/(\d+)", parsed.path)
    if m:
        return m.group(1)
    return None


def fetch_confluence_page(url: str, token: str) -> tuple[dict | None, str | None]:
    """Return (page_dict, error_string). page_dict keys: title, page_id, version, content_md."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    page_id = _extract_page_id(url)
    if page_id:
        api_url = (
            f"{base}/rest/api/content/{page_id}"
            "?expand=body.view,title,version,space"
        )
    else:
        # /display/SPACE/Title+Here  or  /wiki/spaces/SPACE/pages/.../Title
        m = re.match(r"^/display/([^/]+)/(.+)$", parsed.path)
        if not m:
            return None, f"Cannot parse Confluence URL: {url}"
        space_key = m.group(1)
        title = unquote(m.group(2).replace("+", " "))
        api_url = (
            f"{base}/rest/api/content"
            f"?spaceKey={space_key}"
            f"&title={requests.utils.quote(title)}"
            "&expand=body.view,title,version,space"
            "&limit=1"
        )

    try:
        resp = requests.get(api_url, headers=headers, timeout=30, verify=True)
    except requests.exceptions.SSLError:
        resp = requests.get(api_url, headers=headers, timeout=30, verify=False)
    except requests.exceptions.ConnectionError as exc:
        return None, f"Cannot connect to Confluence: {exc}"

    if resp.status_code == 401:
        return None, "Confluence authentication failed — check your Personal Access Token"
    if resp.status_code == 403:
        return None, "Access denied to this Confluence page"
    if resp.status_code == 404:
        return None, "Confluence page not found"
    if not resp.ok:
        return None, f"Confluence error {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    page = data["results"][0] if "results" in data else data
    if "results" in data and not data["results"]:
        return None, f"No Confluence page found for: {url}"

    content_html = page.get("body", {}).get("view", {}).get("value", "")
    return {
        "title": page.get("title", "Untitled"),
        "page_id": page.get("id", ""),
        "version": page.get("version", {}).get("number", 1),
        "content_md": html_to_text(content_html),   # truncated later per-provider
    }, None


# ─── AI call (multi-provider) ─────────────────────────────────────────────────

def call_ai(prompt: str, provider: str, api_key: str) -> tuple[str | None, str | None]:
    """Return (response_text, error_string). Supports github, openai, anthropic."""

    system = (
        "You are a senior Quality Engineer. "
        "Work only from the provided documents. "
        "Return only valid JSON as instructed — no prose before or after."
    )

    # ── GitHub Models (free, uses GitHub PAT, OpenAI-compatible) ──────────────
    if provider == "github":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "max_tokens": 3000,   # input cap is 8k; leave 5k for our prompt
        }
        try:
            r = requests.post(
                "https://models.inference.ai.azure.com/chat/completions",
                headers=headers, json=body, timeout=120,
            )
        except Exception as exc:
            return None, f"GitHub Models request failed: {exc}"
        if r.status_code == 401:
            return None, "GitHub token invalid or missing 'models:read' permission"
        if r.status_code == 403:
            return None, "GitHub Models access denied — ensure you have GitHub Copilot or free tier access"
        if not r.ok:
            return None, f"GitHub Models error {r.status_code}: {r.text[:300]}"
        return r.json()["choices"][0]["message"]["content"], None

    # ── OpenAI ────────────────────────────────────────────────────────────────
    if provider == "openai":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "max_tokens": 8000,
        }
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=body, timeout=120,
            )
        except Exception as exc:
            return None, f"OpenAI request failed: {exc}"
        if r.status_code == 401:
            return None, "OpenAI API key is invalid"
        if not r.ok:
            return None, f"OpenAI error {r.status_code}: {r.text[:300]}"
        return r.json()["choices"][0]["message"]["content"], None

    # ── Anthropic ─────────────────────────────────────────────────────────────
    if provider == "anthropic":
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text, None
        except Exception as exc:
            return None, f"Anthropic error: {exc}"

    return None, f"Unknown provider: {provider}"



# ─── Chunked analysis helpers (GitHub Models 8k-token workaround) ────────────
# Token budget per call (GitHub Models free tier = 8k input tokens max):
#   Spec chunk      : 3,000 chars ≈  750 tokens
#   Test plan       : 3,000 chars ≈  750 tokens
#   Design skeleton :   800 chars ≈  200 tokens
#   Prompt + schema :   800 chars ≈  200 tokens
#   System msg      :   200 chars ≈   50 tokens
#   Output budget   :                1,500 tokens
#   ─────────────────────────────────────────────
#   Total           :              ~3,450 tokens  → well under 8k
SPEC_CHUNK_CHARS   = 3_000
TP_CONDENSED_CHARS = 3_000
DESIGN_SKELETON_CHARS = 800


def hard_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks ≤ max_chars, breaking at paragraph boundaries."""
    chunks: list[str] = []
    while len(text) > max_chars:
        split_at = text.rfind("\n\n", 0, max_chars)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        chunks.append(text[:split_at].strip())
        text = text[split_at:].lstrip("\n")
    if text.strip():
        chunks.append(text.strip())
    return chunks or [""]


def condense_doc(text: str, max_chars: int) -> str:
    """Return first max_chars chars, preferring paragraph breaks."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n\n", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return text[:cut].strip() + "\n…[truncated]"


def merge_feedback(all_items: list[dict]) -> list[dict]:
    """Deduplicate by (category, issue-prefix) and sort High → Low."""
    order = {"High": 0, "Medium": 1, "Low": 2}
    all_items.sort(key=lambda x: order.get(x.get("severity", "Low"), 2))
    seen: set[tuple] = set()
    out: list[dict] = []
    for item in all_items:
        fp = (item.get("category", ""), item.get("issue", "")[:60].lower().strip())
        if fp not in seen:
            seen.add(fp)
            out.append(item)
    return out


FEEDBACK_SCHEMA = (
    '{"feedback":['
    '{"severity":"High"|"Medium"|"Low",'
    '"category":"Missing Functional Coverage"|"Missing Edge Case"|"Missing Negative Scenario"'
    '|"Missing Integration"|"Missing Failure Handling"|"Dependency Gap"|"Environment/Data Gap"'
    '|"Missing Performance"|"Missing Security"|"Redundancy"|"Traceability Gap",'
    '"issue":"string","reason":"string",'
    '"reference":[{"doc_title":"string","heading":"string","quote":"string"}],'
    '"requirement_ids":[],"affected_test_cases":[],"suggestion":"string"}]}'
)


def analyze_github_chunked(
    spec: dict, test_plan: dict, design: dict | None, api_key: str
) -> tuple[list | None, str | None]:
    """
    Hard-split the spec into SPEC_CHUNK_CHARS chunks.
    Each API call gets: spec chunk + condensed test plan + condensed design.
    Merge all batches' feedback and return deduplicated top-30.
    """
    tp_condensed     = condense_doc(test_plan["content_md"], TP_CONDENSED_CHARS)
    design_condensed = condense_doc(design["content_md"], DESIGN_SKELETON_CHARS) if design else None
    batches          = hard_chunks(spec["content_md"], SPEC_CHUNK_CHARS)

    system = (
        "You are a senior Quality Engineer. "
        "Work only from the provided documents. "
        "Return only valid JSON as instructed — no prose before or after."
    )

    all_feedback: list[dict] = []

    for i, batch in enumerate(batches):
        design_part = (
            f"\n\n## DESIGN DOCUMENT (condensed)\n{design_condensed}"
            if design_condensed else ""
        )
        prompt = (
            f"## SPEC — batch {i + 1} of {len(batches)}\n\n"
            f"{batch}\n\n"
            f"---\n\n"
            f"## TEST PLAN: {test_plan['title']} (condensed headings)\n\n"
            f"{tp_condensed}"
            f"{design_part}\n\n"
            f"---\n\n"
            f"Does the Test Plan adequately cover the spec section(s) above?\n"
            f"Return ONLY valid JSON (no prose, no markdown fences):\n"
            f"{FEEDBACK_SCHEMA}\n\n"
            f"If fully covered or non-testable, return {{\"feedback\":[]}}."
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "max_tokens": 2000,
        }

        try:
            r = requests.post(
                "https://models.inference.ai.azure.com/chat/completions",
                headers=headers, json=body, timeout=120,
            )
        except Exception as exc:
            return None, f"GitHub Models request failed on batch {i + 1}: {exc}"

        if r.status_code == 401:
            return None, "GitHub token invalid or missing 'models:read' permission"
        if r.status_code == 403:
            return None, "GitHub Models access denied"
        if not r.ok:
            return None, f"GitHub Models error {r.status_code}: {r.text[:300]}"

        raw = r.json()["choices"][0]["message"]["content"]
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        try:
            parsed = json.loads(clean)
            all_feedback.extend(parsed.get("feedback", []))
        except json.JSONDecodeError:
            pass  # skip malformed batch response, keep going

    return merge_feedback(all_feedback)[:30], None


def build_user_prompt(spec: dict, test_plan: dict, design: dict | None) -> str:
    """Full-document prompt for providers with large context (OpenAI, Anthropic)."""
    def section(label, doc):
        return (
            f"## {label}\n"
            f"Title: {doc['title']}  |  Page ID: {doc['page_id']}  |  Version: {doc['version']}\n\n"
            f"{doc['content_md'][:30_000]}\n"
        )

    parts = [section("SPEC", spec), "---", section("TEST PLAN", test_plan)]
    if design:
        parts += ["---", section("DESIGN DOCUMENT", design)]

    parts.append(
        "---\n\n"
        "Evaluate whether the Test Plan fully covers:\n"
        "1. Functional requirements\n"
        "2. Edge cases and boundaries\n"
        "3. Negative/error scenarios\n"
        "4. Integration points and external dependencies\n"
        "5. Failure handling and recovery\n"
        "6. Data dependencies and environments\n"
        "7. Performance considerations (throughput/latency/resource limits)\n"
        "8. Security concerns (authZ/authN/input validation/leakage)\n\n"
        f"Return ONLY valid JSON — no prose, no markdown fences:\n{FEEDBACK_SCHEMA}\n\n"
        "Cap at 30 items. Prioritise highest-risk gaps first."
    )

    return "\n\n".join(parts)


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/config", methods=["GET"])
def get_config():
    """Return saved credentials so the browser can pre-fill Settings."""
    return jsonify({
        "confluence_token": os.getenv("CONFLUENCE_TOKEN", ""),
        "ai_key":           os.getenv("AI_API_KEY", ""),
        "ai_provider":      os.getenv("AI_PROVIDER", "github"),
    })


@app.route("/api/config", methods=["POST"])
def save_config():
    """Persist credentials to .env so they survive browser clears."""
    body = request.json or {}
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    updates = {}
    if body.get("confluence_token"):
        updates["CONFLUENCE_TOKEN"] = body["confluence_token"]
    if body.get("ai_key"):
        updates["AI_API_KEY"] = body["ai_key"]
    if body.get("ai_provider"):
        updates["AI_PROVIDER"] = body["ai_provider"]

    # Read existing .env lines, replace or append
    lines = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    written_keys = set()
    new_lines = []
    for line in lines:
        key = line.split("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written_keys.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in written_keys:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Reload so this process picks up the new values immediately
    load_dotenv(env_path, override=True)
    return jsonify({"saved": list(updates.keys())})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.json or {}

    spec_url   = body.get("spec_url", "").strip()
    tp_url     = body.get("test_plan_url", "").strip()
    design_url = body.get("design_url", "").strip()
    provider   = body.get("provider", "github").strip()   # github | openai | anthropic

    # Credentials: browser UI headers override .env
    confluence_token = (
        request.headers.get("X-Confluence-Token")
        or os.getenv("CONFLUENCE_TOKEN", "")
    )
    ai_key = (
        request.headers.get("X-AI-Key")
        or os.getenv("AI_API_KEY", "")
        or os.getenv("ANTHROPIC_API_KEY", "")   # backwards compat
    )

    if not spec_url:
        return jsonify({"error": "spec_url is required"}), 400
    if not tp_url:
        return jsonify({"error": "test_plan_url is required"}), 400
    if not confluence_token:
        return jsonify(
            {"error": "Confluence token not configured. Open Settings and add your Confluence Personal Access Token."}
        ), 400
    if not ai_key:
        key_help = {
            "github":    "GitHub Personal Access Token (github.com → Settings → Developer settings → Personal access tokens, scope: models:read)",
            "openai":    "OpenAI API key (platform.openai.com)",
            "anthropic": "Anthropic API key (console.anthropic.com)",
        }.get(provider, "AI API key")
        return jsonify({"error": f"AI key not configured. Open Settings and add your {key_help}."}), 400

    # Fetch Confluence pages
    spec, err = fetch_confluence_page(spec_url, confluence_token)
    if err:
        return jsonify({"error": f"Spec: {err}"}), 400

    tp, err = fetch_confluence_page(tp_url, confluence_token)
    if err:
        return jsonify({"error": f"Test Plan: {err}"}), 400

    design = None
    if design_url:
        design, _ = fetch_confluence_page(design_url, confluence_token)  # optional

    # Build prompt and call AI
    if provider == "github":
        feedback, err = analyze_github_chunked(spec, tp, design, ai_key)
        if err:
            return jsonify({"error": err}), 500
        return jsonify({"feedback": feedback, "sections_analyzed": True})
    else:
        prompt = build_user_prompt(spec, tp, design)
        raw, err = call_ai(prompt, provider, ai_key)
        if err:
            return jsonify({"error": err}), 500
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        try:
            result = json.loads(clean)
            return jsonify(result)
        except json.JSONDecodeError:
            return jsonify({"error": "AI returned invalid JSON", "raw": raw[:1000]}), 500


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("  QE Test Plan Reviewer")
    print("  " + "-" * 36)
    print("  Open:  http://localhost:5000")
    print("  Stop:  Ctrl+C")
    print()
    app.run(host="0.0.0.0", port=5000, debug=False)
