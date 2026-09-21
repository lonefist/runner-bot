# ============================================================
# V1.5.2 LORE / NARRATIVE EVIDENCE ENGINE
# ============================================================

import re
import html
from urllib.parse import urljoin, urlparse


# ------------------------------------------------------------
# SOURCE STATUS
# ------------------------------------------------------------

SOURCE_NOT_CONFIGURED = "NOT_CONFIGURED"
SOURCE_FOUND = "FOUND"
SOURCE_NOT_FOUND = "NOT_FOUND"
SOURCE_LOOKUP_FAILED = "LOOKUP_FAILED"
SOURCE_NO_CONTENT = "FOUND_NO_CONTENT"


# ------------------------------------------------------------
# X / TWITTER
# ------------------------------------------------------------

def x_headers():
    if not X_BEARER_TOKEN:
        return None

    return {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "User-Agent": "RunnerBot-V1.5.2",
    }


def x_lookup_user(handle):
    """
    Returns:
        {
            "status": ...,
            "username": ...,
            "id": ...,
            "name": ...,
            "description": ...
        }
    """

    handle = (handle or "").strip().lstrip("@")

    if not handle:
        return {
            "status": SOURCE_NOT_FOUND,
            "username": "",
        }

    headers = x_headers()

    if not headers:
        return {
            "status": SOURCE_NOT_CONFIGURED,
            "username": handle,
        }

    url = f"{X_BASE}/2/users/by/username/{urllib.parse.quote(handle)}"

    try:
        data = http_get_json(
            url,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )

        user = data.get("data")

        if not user:
            return {
                "status": SOURCE_NOT_FOUND,
                "username": handle,
            }

        return {
            "status": SOURCE_FOUND,
            "username": user.get("username", handle),
            "id": user.get("id", ""),
            "name": user.get("name", ""),
            "description": user.get("description", ""),
        }

    except Exception:
        return {
            "status": SOURCE_LOOKUP_FAILED,
            "username": handle,
        }


def x_recent_posts(username):
    """
    Searches recent X posts from the project's account.

    Returns:
        {
            "status": ...,
            "posts": [...]
        }
    """

    username = (username or "").strip().lstrip("@")

    if not username:
        return {
            "status": SOURCE_NOT_FOUND,
            "posts": [],
        }

    headers = x_headers()

    if not headers:
        return {
            "status": SOURCE_NOT_CONFIGURED,
            "posts": [],
        }

    query = f"from:{username} -is:retweet"

    params = urllib.parse.urlencode({
        "query": query,
        "max_results": min(LORE_MAX_X_POSTS, 100),
        "tweet.fields": "created_at,public_metrics,text",
    })

    url = f"{X_BASE}/2/tweets/search/recent?{params}"

    try:
        data = http_get_json(
            url,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )

        posts = data.get("data") or []

        if not posts:
            return {
                "status": SOURCE_NO_CONTENT,
                "posts": [],
            }

        return {
            "status": SOURCE_FOUND,
            "posts": posts,
        }

    except Exception:
        return {
            "status": SOURCE_LOOKUP_FAILED,
            "posts": [],
        }


def format_x_posts(posts):
    lines = []

    for post in posts[:LORE_MAX_X_POSTS]:
        text = clean_text(post.get("text", ""))

        if not text:
            continue

        created = post.get("created_at", "")

        metrics = post.get("public_metrics") or {}

        likes = safe_int(metrics.get("like_count"), 0)
        replies = safe_int(metrics.get("reply_count"), 0)
        reposts = safe_int(metrics.get("retweet_count"), 0)

        lines.append(
            f"[{created}] "
            f"Likes:{likes} "
            f"Replies:{replies} "
            f"Reposts:{reposts}\n"
            f"{text}"
        )

    return truncate(
        "\n\n".join(lines),
        LORE_MAX_X_CHARS,
    )


# ------------------------------------------------------------
# HTML CLEANING
# ------------------------------------------------------------

def strip_html(raw):
    if not raw:
        return ""

    text = re.sub(
        r"(?is)<script.*?>.*?</script>",
        " ",
        raw,
    )

    text = re.sub(
        r"(?is)<style.*?>.*?</style>",
        " ",
        text,
    )

    text = re.sub(
        r"(?is)<noscript.*?>.*?</noscript>",
        " ",
        text,
    )

    text = re.sub(
        r"(?is)<svg.*?>.*?</svg>",
        " ",
        text,
    )

    text = re.sub(
        r"(?is)<[^>]+>",
        " ",
        text,
    )

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ------------------------------------------------------------
# URL DISCOVERY
# ------------------------------------------------------------

def normalize_url(url, base_url=None):
    if not url:
        return ""

    url = html.unescape(url).strip()

    if base_url:
        url = urljoin(base_url, url)

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return ""

    return url


def extract_links(raw_html, base_url):
    """
    Extract useful external links from a website.

    We specifically look for:
    - GitHub
    - Docs
    - Documentation
    - Whitepaper
    - Litepaper
    - Technical paper
    - PDF
    """

    if not raw_html:
        return []

    links = []

    pattern = re.compile(
        r'''(?is)
        <a[^>]+href\s*=\s*["']([^"']+)["'][^>]*>
        (.*?)
        </a>
        '''
    )

    for match in pattern.finditer(raw_html):

        href = normalize_url(
            match.group(1),
            base_url,
        )

        label = strip_html(match.group(2))

        if not href:
            continue

        links.append({
            "url": href,
            "label": label,
        })

    # Also detect plain URLs inside HTML.
    url_pattern = re.compile(
        r'https?://[^\s"\'<>]+',
        re.I,
    )

    for match in url_pattern.findall(raw_html):

        url = normalize_url(match, base_url)

        if url:
            links.append({
                "url": url,
                "label": "",
            })

    # Deduplicate
    output = []
    seen = set()

    for item in links:

        url = item["url"]

        if url in seen:
            continue

        seen.add(url)
        output.append(item)

    return output


def classify_evidence_link(url, label=""):
    """
    Classify a discovered website link.
    """

    value = f"{url} {label}".lower()

    if "github.com/" in value:
        return "GITHUB"

    if ".pdf" in value:
        return "WHITEPAPER"

    keywords = (
        "whitepaper",
        "white-paper",
        "litepaper",
        "lite-paper",
        "technical paper",
        "technical-paper",
        "technicalpaper",
        "documentation",
        "docs",
        "document",
        "research",
        "paper",
    )

    for keyword in keywords:
        if keyword in value:
            return "DOCUMENTATION"

    return ""


# ------------------------------------------------------------
# WEBSITE
# ------------------------------------------------------------

def fetch_website(url):
    """
    Fetch website and discover secondary evidence links.

    Returns:
        {
            "status": ...,
            "url": ...,
            "text": ...,
            "links": [...]
        }
    """

    if not url:
        return {
            "status": SOURCE_NOT_FOUND,
            "url": "",
            "text": "",
            "links": [],
        }

    try:

        raw = http_get_text(
            url,
            timeout=HTTP_TIMEOUT,
        )

        if not raw:
            return {
                "status": SOURCE_NO_CONTENT,
                "url": url,
                "text": "",
                "links": [],
            }

        text = strip_html(raw)

        links = extract_links(
            raw,
            url,
        )

        return {
            "status": SOURCE_FOUND,
            "url": url,
            "text": truncate(
                text,
                LORE_MAX_WEBSITE_CHARS,
            ),
            "links": links,
            "raw_html": raw,
        }

    except Exception:
        return {
            "status": SOURCE_LOOKUP_FAILED,
            "url": url,
            "text": "",
            "links": [],
        }


# ------------------------------------------------------------
# FIND SECONDARY EVIDENCE
# ------------------------------------------------------------

def find_secondary_evidence(website_result):
    """
    Returns discovered evidence links grouped by type.
    """

    result = {
        "GITHUB": [],
        "DOCUMENTATION": [],
        "WHITEPAPER": [],
    }

    for item in website_result.get("links", []):

        url = item.get("url", "")
        label = item.get("label", "")

        kind = classify_evidence_link(
            url,
            label,
        )

        if not kind:
            continue

        if url not in result[kind]:
            result[kind].append(url)

    return result


# ------------------------------------------------------------
# FETCH DOCUMENT / WHITEPAPER
# ------------------------------------------------------------

def fetch_document(url):
    """
    Fetch documentation or whitepaper page.

    For normal HTML documentation we extract text.

    For PDFs we keep the URL and attempt normal HTTP retrieval.
    If PDF text is not directly parseable by the lightweight
    HTTP layer, it remains a discovered source rather than
    pretending that we read it.
    """

    if not url:
        return {
            "status": SOURCE_NOT_FOUND,
            "url": "",
            "text": "",
        }

    try:

        raw = http_get_text(
            url,
            timeout=HTTP_TIMEOUT,
        )

        if not raw:
            return {
                "status": SOURCE_NO_CONTENT,
                "url": url,
                "text": "",
            }

        text = strip_html(raw)

        return {
            "status": SOURCE_FOUND,
            "url": url,
            "text": truncate(
                text,
                LORE_MAX_GITHUB_CHARS,
            ),
        }

    except Exception:
        return {
            "status": SOURCE_LOOKUP_FAILED,
            "url": url,
            "text": "",
        }


# ------------------------------------------------------------
# GITHUB
# ------------------------------------------------------------

def fetch_github(github_url):
    if not github_url:
        return {
            "status": SOURCE_NOT_FOUND,
            "url": "",
            "text": "",
        }

    try:

        raw = http_get_text(
            github_url,
            timeout=HTTP_TIMEOUT,
        )

        if not raw:
            return {
                "status": SOURCE_NO_CONTENT,
                "url": github_url,
                "text": "",
            }

        text = strip_html(raw)

        return {
            "status": SOURCE_FOUND,
            "url": github_url,
            "text": truncate(
                text,
                LORE_MAX_GITHUB_CHARS,
            ),
        }

    except Exception:
        return {
            "status": SOURCE_LOOKUP_FAILED,
            "url": github_url,
            "text": "",
        }


# ------------------------------------------------------------
# GITHUB URL SELECTION
# ------------------------------------------------------------

def choose_github_url(website_result):
    discovered = find_secondary_evidence(
        website_result
    )

    urls = discovered.get("GITHUB", [])

    if urls:
        return urls[0]

    return ""


# ------------------------------------------------------------
# DOCUMENT URL SELECTION
# ------------------------------------------------------------

def choose_document_url(website_result):
    discovered = find_secondary_evidence(
        website_result
    )

    whitepapers = discovered.get(
        "WHITEPAPER",
        [],
    )

    if whitepapers:
        return whitepapers[0]

    docs = discovered.get(
        "DOCUMENTATION",
        [],
    )

    if docs:
        return docs[0]

    return ""


# ------------------------------------------------------------
# CREDIBLE SOURCE COUNT
# ------------------------------------------------------------

def calculate_credible_sources(sources):
    """
    IMPORTANT:

    Each source TYPE counts once.

    X = 1
    WEBSITE = 1
    GITHUB = 1
    DOCUMENTATION = 1
    WHITEPAPER = 1

    DEX description is SUPPORTING ONLY and does not count.
    """

    credible = []

    # X
    if (
        sources.get("x_status") == SOURCE_FOUND
        and sources.get("x_post_count", 0) > 0
    ):
        credible.append("X")

    # Website
    if (
        sources.get("website_status") == SOURCE_FOUND
        and sources.get("website_text")
    ):
        credible.append("WEBSITE")

    # GitHub
    if (
        sources.get("github_status") == SOURCE_FOUND
        and sources.get("github_text")
    ):
        credible.append("GITHUB")

    # Documentation
    if (
        sources.get("documentation_status") == SOURCE_FOUND
        and sources.get("documentation_text")
    ):
        credible.append("DOCUMENTATION")

    # Whitepaper
    if (
        sources.get("whitepaper_status") == SOURCE_FOUND
        and sources.get("whitepaper_text")
    ):
        credible.append("WHITEPAPER")

    # Remove duplicates
    credible = list(dict.fromkeys(credible))

    return credible


# ------------------------------------------------------------
# LORE SOURCE COLLECTION
# ------------------------------------------------------------

def collect_lore_sources(s):
    """
    V1.5.2 evidence collector.

    The important difference from V1.5.1 is that the website
    is now used as a discovery hub for GitHub/docs/whitepapers.
    """

    handle = s.get("x_handle", "")
    website = s.get("website", "")
    dex_description = s.get(
        "dex_description",
        "",
    )

    result = {
        "x_handle": handle,

        "x_status": SOURCE_NOT_FOUND,
        "x_posts": [],
        "x_post_count": 0,
        "x_profile": {},
        "x_posts_text": "",

        "website": website,
        "website_status": SOURCE_NOT_FOUND,
        "website_text": "",
        "website_links": [],

        "github_url": "",
        "github_status": SOURCE_NOT_FOUND,
        "github_text": "",

        "documentation_url": "",
        "documentation_status": SOURCE_NOT_FOUND,
        "documentation_text": "",

        "whitepaper_url": "",
        "whitepaper_status": SOURCE_NOT_FOUND,
        "whitepaper_text": "",

        "dex_description": dex_description,
        "dex_description_status": (
            SOURCE_FOUND
            if dex_description
            else SOURCE_NOT_FOUND
        ),

        "credible_types": [],
        "source_count": 0,
        "source_summary": "",
        "error": "",
    }

    # --------------------------------------------------------
    # X
    # --------------------------------------------------------

    if handle:

        profile = x_lookup_user(handle)

        result["x_profile"] = profile
        result["x_status"] = profile.get(
            "status",
            SOURCE_LOOKUP_FAILED,
        )

        if result["x_status"] == SOURCE_FOUND:

            posts_result = x_recent_posts(handle)

            posts = posts_result.get(
                "posts",
                [],
            )

            result["x_posts"] = posts
            result["x_post_count"] = len(posts)

            if posts:
                result["x_posts_text"] = format_x_posts(
                    posts
                )

            if posts_result.get("status") != SOURCE_FOUND:
                result["x_status"] = posts_result.get(
                    "status",
                    SOURCE_NO_CONTENT,
                )

    else:
        result["x_status"] = SOURCE_NOT_FOUND

    # --------------------------------------------------------
    # WEBSITE
    # --------------------------------------------------------

    website_result = fetch_website(
        website
    )

    result["website_status"] = website_result.get(
        "status",
        SOURCE_LOOKUP_FAILED,
    )

    result["website_text"] = website_result.get(
        "text",
        "",
    )

    result["website_links"] = website_result.get(
        "links",
        [],
    )

    # --------------------------------------------------------
    # SECONDARY EVIDENCE
    # --------------------------------------------------------

    if (
        result["website_status"]
        == SOURCE_FOUND
    ):

        discovered = find_secondary_evidence(
            website_result
        )

        # GitHub
        github_urls = discovered.get(
            "GITHUB",
            [],
        )

        if github_urls:

            result["github_url"] = github_urls[0]

            github = fetch_github(
                result["github_url"]
            )

            result["github_status"] = github.get(
                "status",
                SOURCE_LOOKUP_FAILED,
            )

            result["github_text"] = github.get(
                "text",
                "",
            )

        # Whitepaper
        whitepaper_urls = discovered.get(
            "WHITEPAPER",
            [],
        )

        if whitepaper_urls:

            result["whitepaper_url"] = (
                whitepaper_urls[0]
            )

            whitepaper = fetch_document(
                result["whitepaper_url"]
            )

            result["whitepaper_status"] = (
                whitepaper.get(
                    "status",
                    SOURCE_LOOKUP_FAILED,
                )
            )

            result["whitepaper_text"] = (
                whitepaper.get(
                    "text",
                    "",
                )
            )

        # Documentation
        documentation_urls = discovered.get(
            "DOCUMENTATION",
            [],
        )

        if documentation_urls:

            result["documentation_url"] = (
                documentation_urls[0]
            )

            documentation = fetch_document(
                result["documentation_url"]
            )

            result["documentation_status"] = (
                documentation.get(
                    "status",
                    SOURCE_LOOKUP_FAILED,
                )
            )

            result["documentation_text"] = (
                documentation.get(
                    "text",
                    "",
                )
            )

    # --------------------------------------------------------
    # CREDIBLE SOURCE CALCULATION
    # --------------------------------------------------------

    credible = calculate_credible_sources(
        result
    )

    result["credible_types"] = credible
    result["source_count"] = len(credible)

    all_sources = []

    all_sources.extend(credible)

    # DEX is deliberately supporting only.
    if dex_description:
        all_sources.append(
            "DEX_DESCRIPTION_SUPPORTING"
        )

    result["source_summary"] = ", ".join(
        all_sources
    )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    diagnostics = []

    if result["x_status"] == SOURCE_NOT_CONFIGURED:
        diagnostics.append(
            "X_API_NOT_CONFIGURED"
        )
    elif result["x_status"] == SOURCE_NOT_FOUND:
        diagnostics.append(
            "X_ACCOUNT_NOT_FOUND"
        )
    elif result["x_status"] == SOURCE_LOOKUP_FAILED:
        diagnostics.append(
            "X_LOOKUP_FAILED"
        )
    elif result["x_status"] == SOURCE_NO_CONTENT:
        diagnostics.append(
            "X_FOUND_NO_POSTS"
        )

    if result["website_status"] != SOURCE_FOUND:
        diagnostics.append(
            f"WEBSITE:{result['website_status']}"
        )

    if result["github_status"] != SOURCE_FOUND:
        diagnostics.append(
            f"GITHUB:{result['github_status']}"
        )

    if result["documentation_status"] != SOURCE_FOUND:
        diagnostics.append(
            f"DOCS:{result['documentation_status']}"
        )

    if result["whitepaper_status"] != SOURCE_FOUND:
        diagnostics.append(
            f"WHITEPAPER:{result['whitepaper_status']}"
        )

    if result["source_count"] < LORE_MIN_EVIDENCE:
        result["error"] = (
            "INSUFFICIENT_CREDIBLE_EVIDENCE"
        )

        if diagnostics:
            result["error"] += (
                " | "
                + ", ".join(diagnostics)
            )

    return result


# ------------------------------------------------------------
# PRINT LORE SOURCES
# ------------------------------------------------------------

def print_lore_sources(sources):

    print("[LORE SOURCES]")

    handle = sources.get(
        "x_handle"
    )

    print(
        f"X HANDLE: "
        f"{('@' + handle) if handle else 'NONE'}"
    )

    print(
        f"X STATUS: "
        f"{sources.get('x_status', 'UNKNOWN')}"
    )

    print(
        f"X POSTS: "
        f"{sources.get('x_post_count', 0)}"
    )

    print(
        f"WEBSITE: "
        f"{sources.get('website_status', 'UNKNOWN')}"
    )

    if sources.get("github_url"):
        print(
            f"GITHUB: FOUND"
        )
        print(
            f"GITHUB URL: "
            f"{sources['github_url']}"
        )
    else:
        print(
            f"GITHUB: "
            f"{sources.get('github_status', 'MISSING')}"
        )

    if sources.get("documentation_url"):
        print(
            "DOCUMENTATION: FOUND"
        )
        print(
            f"DOCS URL: "
            f"{sources['documentation_url']}"
        )
    else:
        print(
            f"DOCUMENTATION: "
            f"{sources.get('documentation_status', 'MISSING')}"
        )

    if sources.get("whitepaper_url"):
        print(
            "WHITEPAPER: FOUND"
        )
        print(
            f"WHITEPAPER URL: "
            f"{sources['whitepaper_url']}"
        )
    else:
        print(
            f"WHITEPAPER: "
            f"{sources.get('whitepaper_status', 'MISSING')}"
        )

    print(
        "DEX DESCRIPTION: "
        + (
            "YES (SUPPORTING ONLY)"
            if sources.get("dex_description")
            else "NO (SUPPORTING ONLY)"
        )
    )

    print(
        f"CREDIBLE SOURCES: "
        f"{sources.get('source_count', 0)}/"
        f"{LORE_MIN_EVIDENCE}"
    )

    credible = sources.get(
        "credible_types",
        [],
    )

    print(
        "CREDIBLE TYPES: "
        + (
            ", ".join(credible)
            if credible
            else "NONE"
        )
    )

    print(
        "ALL SOURCES: "
        + (
            sources.get(
                "source_summary"
            )
            or "NONE"
        )
    )

    if sources.get("error"):
        print(
            "SOURCE DIAGNOSTIC: "
            + sources["error"]
        )


# ============================================================
# LORE AI
# ============================================================

def lore_ai_configured():
    return bool(
        LORE_AI_BASE_URL
        and LORE_AI_MODEL
        and LORE_AI_API_KEY
    )


def lore_ai_request(token, sources):

    if not lore_ai_configured():
        return {
            "status": "NOT_CONFIGURED",
            "result": None,
            "error": "LORE_AI_NOT_CONFIGURED",
        }

    credible_types = sources.get(
        "credible_types",
        [],
    )

    evidence_sections = []

    if "X" in credible_types:

        evidence_sections.append(
            "=== X / TWITTER ===\n"
            + truncate(
                sources.get(
                    "x_posts_text",
                    "",
                ),
                LORE_MAX_X_CHARS,
            )
        )

    if "WEBSITE" in credible_types:

        evidence_sections.append(
            "=== OFFICIAL WEBSITE ===\n"
            + truncate(
                sources.get(
                    "website_text",
                    "",
                ),
                LORE_MAX_WEBSITE_CHARS,
            )
        )

    if "GITHUB" in credible_types:

        evidence_sections.append(
            "=== GITHUB ===\n"
            + truncate(
                sources.get(
                    "github_text",
                    "",
                ),
                LORE_MAX_GITHUB_CHARS,
            )
        )

    if "DOCUMENTATION" in credible_types:

        evidence_sections.append(
            "=== DOCUMENTATION ===\n"
            + truncate(
                sources.get(
                    "documentation_text",
                    "",
                ),
                LORE_MAX_GITHUB_CHARS,
            )
        )

    if "WHITEPAPER" in credible_types:

        evidence_sections.append(
            "=== WHITEPAPER ===\n"
            + truncate(
                sources.get(
                    "whitepaper_text",
                    "",
                ),
                LORE_MAX_GITHUB_CHARS,
            )
        )

    # DEX description can be seen by the AI,
    # but cannot be treated as independent proof.
    if sources.get("dex_description"):

        evidence_sections.append(
            "=== DEX DESCRIPTION "
            "(SUPPORTING ONLY) ===\n"
            + truncate(
                sources.get(
                    "dex_description",
                    "",
                ),
                5000,
            )
        )

    source_text = "\n\n".join(
        evidence_sections
    )

    system_prompt = """
You are a strict crypto token narrative and lore verifier.

Your job is NOT to promote a token.

Never invent lore.
Never infer a project's purpose merely from its name.
Never treat price action, market cap, volume, buys, sells,
or liquidity as proof of a narrative.
Never treat a DEX description as independent evidence.
Marketing claims must be treated cautiously.

A credible narrative should be supported by concrete evidence
from independent source types.

Examples of useful evidence:
- official website describing a specific product
- GitHub repository containing relevant code
- technical documentation
- whitepaper/litepaper
- consistent official X posts describing the project
- identifiable developer/project identity across sources

A GitHub repository proves that code/repository material exists,
but does NOT automatically prove every claim made about the project.

X activity can establish that a narrative is being actively
communicated, but does NOT independently prove technical claims.

If evidence is weak, contradictory, vague, copied, or insufficient,
return UNKNOWN.

Return ONLY valid JSON:

{
  "lore_score": 0,
  "confidence": "HIGH|MEDIUM|LOW",
  "momentum": "HIGH|MEDIUM|LOW",
  "status": "PASS|FAIL|UNKNOWN",
  "narrative": "short explanation",
  "evidence": [
    "specific evidence"
  ],
  "red_flags": [
    "specific concern"
  ]
}

PASS normally requires:
- lore_score >= 18
- at least 2 specific evidence points
- confidence HIGH or MEDIUM
- evidence supported by at least 2 independent source types

Do not award PASS simply because the token is trending.

For momentum:
HIGH means there is clear recent evidence of active narrative
development, communication, building, releases, or community activity.

LOW means little recent evidence.

If the evidence does not justify a conclusion,
use UNKNOWN.
"""

    user_prompt = f"""
TOKEN:
Name: {token.get('name', '')}
Symbol: {token.get('symbol', '')}
Contract: {token.get('address', '')}

CREDIBLE SOURCE TYPES:
{", ".join(credible_types)}

SOURCE MATERIAL:

{source_text}
"""

    payload = {
        "model": LORE_AI_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": system_prompt.strip(),
            },
            {
                "role": "user",
                "content": user_prompt.strip(),
            },
        ],
    }

    headers = {
        "Authorization":
            f"Bearer {LORE_AI_API_KEY}",
        "Content-Type":
            "application/json",
    }

    try:

        raw = urllib.request.Request(
            LORE_AI_BASE_URL
            + "/chat/completions",
            data=json.dumps(payload).encode(
                "utf-8"
            ),
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(
            raw,
            timeout=LORE_AI_TIMEOUT,
        ) as response:

            body = response.read().decode(
                "utf-8",
                errors="replace",
            )

        data = json.loads(body)

        choices = data.get(
            "choices",
            [],
        )

        if not choices:
            return {
                "status": "ERROR",
                "result": None,
                "error": "NO_AI_CHOICES",
            }

        content = (
            choices[0]
            .get("message", {})
            .get("content", "")
        )

        content = content.strip()

        # Remove accidental markdown fences.
        content = re.sub(
            r"^```json\s*",
            "",
            content,
            flags=re.I,
        )

        content = re.sub(
            r"\s*```$",
            "",
            content,
        )

        result = json.loads(content)

        return {
            "status": "OK",
            "result": result,
            "error": "",
        }

    except Exception as exc:

        return {
            "status": "ERROR",
            "result": None,
            "error": str(exc),
        }


# ============================================================
# NORMALIZE LORE RESULT
# ============================================================

def normalize_lore_result(result, sources):

    if not isinstance(result, dict):

        return {
            "lore_score": 0,
            "confidence": "LOW",
            "momentum": "LOW",
            "status": "UNKNOWN",
            "narrative": "",
            "evidence": [],
            "red_flags": [
                "Invalid lore result"
            ],
        }

    score = clamp(
        safe_int(
            result.get(
                "lore_score",
                0
            ),
            0,
        ),
        0,
        25,
    )

    confidence = str(
        result.get(
            "confidence",
            "LOW",
        )
    ).upper()

    momentum = str(
        result.get(
            "momentum",
            "LOW",
        )
    ).upper()

    status = str(
        result.get(
            "status",
            "UNKNOWN",
        )
    ).upper()

    evidence = result.get(
        "evidence",
        [],
    )

    red_flags = result.get(
        "red_flags",
        [],
    )

    if not isinstance(
        evidence,
        list
    ):
        evidence = []

    if not isinstance(
        red_flags,
        list
    ):
        red_flags = []

    credible_count = sources.get(
        "source_count",
        0,
    )

    # --------------------------------------------------------
    # HARD LOCAL ENFORCEMENT
    # --------------------------------------------------------

    if status == "PASS":

        if score < LORE_MIN_SCORE:
            status = "UNKNOWN"

        elif len(evidence) < 2:
            status = "UNKNOWN"

        elif confidence not in (
            "HIGH",
            "MEDIUM",
        ):
            status = "UNKNOWN"

        elif credible_count < LORE_MIN_EVIDENCE:
            status = "UNKNOWN"

    if confidence not in (
        "HIGH",
        "MEDIUM",
        "LOW",
    ):
        confidence = "LOW"

    if momentum not in (
        "HIGH",
        "MEDIUM",
        "LOW",
    ):
        momentum = "LOW"

    if status not in (
        "PASS",
        "FAIL",
        "UNKNOWN",
    ):
        status = "UNKNOWN"

    return {
        "lore_score": score,
        "confidence": confidence,
        "momentum": momentum,
        "status": status,
        "narrative": clean_text(
            str(
                result.get(
                    "narrative",
                    "",
                )
            )
        ),
        "evidence": [
            clean_text(str(x))
            for x in evidence[:10]
            if clean_text(str(x))
        ],
        "red_flags": [
            clean_text(str(x))
            for x in red_flags[:10]
            if clean_text(str(x))
        ],
    }


# ============================================================
# RESEARCH LORE
# ============================================================

def research_lore(token):

    sources = collect_lore_sources(token)

    print_lore_sources(sources)

    # --------------------------------------------------------
    # REQUIRE 2 CREDIBLE SOURCE TYPES BEFORE AI
    # --------------------------------------------------------

    if sources.get("source_count", 0) < LORE_MIN_EVIDENCE:

        print("[LORE AI]")
        print("STATUS: NOT RUN")

        reason = (
            sources.get("error")
            or "INSUFFICIENT_CREDIBLE_EVIDENCE"
        )

        print(f"REASON: {reason}")

        return {
            "lore_score": 0,
            "confidence": "LOW",
            "momentum": "LOW",
            "status": "UNKNOWN",
            "narrative": "",
            "evidence": [],
            "red_flags": [
                "Insufficient independent evidence"
            ],
            "sources": sources,
            "ai_status": "NOT RUN",
        }

    # --------------------------------------------------------
    # RUN LORE AI
    # --------------------------------------------------------

    ai = lore_ai_request(
        token,
        sources,
    )

    print("[LORE AI]")

    print(
        f"STATUS: {ai.get('status', 'UNKNOWN')}"
    )

    if ai.get("error"):
        print(
            f"ERROR: {ai['error']}"
        )

    # --------------------------------------------------------
    # AI FAILED / NOT CONFIGURED
    # --------------------------------------------------------

    if ai.get("status") != "OK":

        return {
            "lore_score": 0,
            "confidence": "LOW",
            "momentum": "LOW",
            "status": "UNKNOWN",
            "narrative": "",
            "evidence": [],
            "red_flags": [
                ai.get(
                    "error",
                    "Lore AI failed",
                )
            ],
            "sources": sources,
            "ai_status": ai.get(
                "status",
                "UNKNOWN",
            ),
        }

    # --------------------------------------------------------
    # NORMALIZE AI RESULT
    # --------------------------------------------------------

    result = normalize_lore_result(
        ai.get("result"),
        sources,
    )

    result["sources"] = sources
    result["ai_status"] = "OK"

    return result
