# Scrapling Library -- Comprehensive Technical Summary

**Version**: 0.4.1 (as of pyproject.toml)  
**License**: BSD-3-Clause  
**Python**: >= 3.10  
**Author**: Karim Shoair (D4Vinci)  
**Repository**: https://github.com/D4Vinci/Scrapling  
**Docs**: https://scrapling.readthedocs.io/en/latest/

---

## 1. Installation

### Base (parser only)
```bash
pip install scrapling
```
Core dependencies: `lxml>=6.0.2`, `cssselect>=1.4.0`, `orjson>=3.11.7`, `tld>=0.13.1`, `w3lib>=2.4.0`, `typing_extensions`.

### With Fetchers (browser automation + HTTP)
```bash
pip install "scrapling[fetchers]"
scrapling install           # downloads browsers + system deps
scrapling install --force   # force reinstall
```
Fetcher dependencies: `curl_cffi>=0.14.0`, `playwright==1.56.0`, `patchright==1.56.0`, `browserforge>=1.2.4`, `msgspec>=0.20.0`, `anyio>=4.12.1`, `click>=8.3.0`.

### Other extras
```bash
pip install "scrapling[ai]"      # MCP server (adds mcp, markdownify)
pip install "scrapling[shell]"   # Interactive shell + extract CLI
pip install "scrapling[all]"     # Everything
```

### Programmatic browser install
```python
from scrapling.cli import install
install([], standalone_mode=False)
```

### Docker
```bash
docker pull pyd4vinci/scrapling
# or
docker pull ghcr.io/d4vinci/scrapling:latest
```

---

## 2. Architecture Overview

```
scrapling/
  __init__.py          # Top-level exports (Fetcher, StealthyFetcher, etc.)
  parser.py            # Selector, Selectors classes (HTML parsing engine)
  cli.py               # CLI entry point
  fetchers/
    __init__.py         # Lazy imports for all fetcher classes
    requests.py         # Fetcher, AsyncFetcher (curl_cffi-based HTTP)
    chrome.py           # DynamicFetcher (Playwright-based)
    stealth_chrome.py   # StealthyFetcher (Patchright-based stealth)
  engines/
    static.py           # FetcherSession, FetcherClient, AsyncFetcherClient (HTTP session logic)
    constants.py        # STEALTH_ARGS, HARMFUL_ARGS, DEFAULT_ARGS, EXTRA_RESOURCES
    _browsers/
      _base.py          # SyncSession, AsyncSession base classes
      _controllers.py   # DynamicSession, AsyncDynamicSession
      _stealth.py       # StealthySession, AsyncStealthySession (Cloudflare solver)
      _types.py         # TypedDicts for all session/fetch params
      _validators.py    # Parameter validation
    toolbelt/
      convertor.py      # ResponseFactory (converts raw responses to Response objects)
      fingerprints.py   # Header generation, referer spoofing
      proxy_rotation.py # ProxyRotator class
      navigation.py     # Request interception handlers
  core/
    custom_types.py     # TextHandler, TextHandlers, AttributesHandler
    mixins.py           # SelectorsGeneration mixin
    storage.py          # SQLiteStorageSystem for adaptive scraping
    translator.py       # CSS-to-XPath translator
  spiders/              # Spider framework for full crawling
```

---

## 3. Fetcher Types -- Complete Reference

### 3.1 Fetcher / AsyncFetcher (HTTP requests via curl_cffi)

**Import**: `from scrapling.fetchers import Fetcher, AsyncFetcher`

**What it does**: Stateless HTTP client that impersonates browser TLS fingerprints using `curl_cffi`. Fastest option, no JavaScript rendering.

**Methods**: `get()`, `post()`, `put()`, `delete()` -- all class methods on singleton instances.

**Usage**:
```python
# Sync one-off request
page = Fetcher.get('https://example.com/')
page = Fetcher.post('https://example.com/api', json={"key": "value"})

# Async one-off request
page = await AsyncFetcher.get('https://example.com/')
```

**Key Parameters** (all methods):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | str | required | Target URL |
| `impersonate` | str/list/None | `"chrome"` | Browser TLS fingerprint to impersonate (e.g., `"chrome"`, `"firefox135"`, or list for random) |
| `stealthy_headers` | bool | `True` | Auto-generate realistic browser headers + Google referer |
| `http3` | bool | `False` | Use HTTP/3 (may conflict with impersonate) |
| `headers` | dict | `{}` | Custom headers |
| `cookies` | CookieTypes | None | Request cookies |
| `proxy` | str | None | Proxy URL (`"http://user:pass@host:port"`) |
| `proxies` | dict | `{}` | Dict of proxies |
| `timeout` | int/float | `30` | Timeout in seconds |
| `follow_redirects` | bool | `True` | Follow HTTP redirects |
| `max_redirects` | int | `30` | Max redirect count |
| `retries` | int | `3` | Retry attempts on failure |
| `retry_delay` | int | `1` | Seconds between retries |
| `verify` | bool | `True` | Verify HTTPS certs |
| `auth` | tuple | None | (username, password) basic auth |
| `params` | dict | None | URL query parameters |
| `data` | dict/str/bytes | None | POST body (form data) |
| `json` | dict/list | None | POST body (JSON) |

**Returns**: `Response` object (which IS a `Selector` -- see Section 5).

---

### 3.2 FetcherSession (Persistent HTTP sessions)

**Import**: `from scrapling.fetchers import FetcherSession`

**What it does**: Wraps curl_cffi sessions with context manager support. Maintains cookies and connection state across requests. Works in both sync and async contexts.

**Usage**:
```python
# Sync
with FetcherSession(impersonate='chrome') as session:
    page1 = session.get('https://example.com/')
    page2 = session.get('https://example.com/page2')

# Async
async with FetcherSession(http3=True) as session:
    page = session.get('https://example.com/')

# With proxy rotation
from scrapling.fetchers import ProxyRotator
rotator = ProxyRotator(["http://proxy1:8080", "http://proxy2:8080"])
with FetcherSession(proxy_rotator=rotator) as session:
    page = session.get('https://example.com/')
```

**Constructor Parameters**: Same as Fetcher method params above, but set as defaults for the session. Additional:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proxy_rotator` | ProxyRotator | None | Automatic proxy rotation (cannot combine with static `proxy`/`proxies`) |
| `selector_config` | dict | `{}` | Arguments passed to Selector class creation |

**Session Methods**: `get()`, `post()`, `put()`, `delete()` -- same params as Fetcher but can override session defaults per-request.

---

### 3.3 DynamicFetcher / DynamicSession / AsyncDynamicSession (Playwright browser)

**Import**: `from scrapling.fetchers import DynamicFetcher, DynamicSession, AsyncDynamicSession`

**What it does**: Full browser automation via Playwright's Chromium. Renders JavaScript, handles dynamic content. Uses standard Playwright (NOT stealth-patched).

**Backward compatibility alias**: `PlayWrightFetcher = DynamicFetcher`

**One-off usage**:
```python
# Sync
page = DynamicFetcher.fetch('https://example.com/', headless=True, network_idle=True)

# Async
page = await DynamicFetcher.async_fetch('https://example.com/')
```

**Session usage**:
```python
# Sync
with DynamicSession(headless=True, disable_resources=True) as session:
    page = session.fetch('https://example.com/')

# Async
async with AsyncDynamicSession(headless=True) as session:
    page = await session.fetch('https://example.com/')
```

**Key Parameters (session init / fetch)**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `headless` | bool | `True` | Run browser hidden |
| `disable_resources` | bool | `False` | Block font/image/media/stylesheet/etc for speed |
| `blocked_domains` | set[str] | None | Block requests to these domains (subdomains included) |
| `useragent` | str | auto | Custom user agent (auto-generates real one if omitted) |
| `cookies` | list | None | Cookies to set |
| `network_idle` | bool | `False` | Wait for no network activity for 500ms |
| `load_dom` | bool | `True` | Wait for full JS execution |
| `timeout` | int | `30000` | Timeout in **milliseconds** |
| `wait` | int | `0` | Additional wait time (ms) after page load |
| `page_action` | Callable | None | Function receiving `page` object for custom automation |
| `wait_selector` | str | None | CSS selector to wait for |
| `wait_selector_state` | str | `"attached"` | State for wait_selector: `attached`, `detached`, `visible`, `hidden` |
| `init_script` | str | None | Path to JS file executed on page creation |
| `locale` | str | system | Browser locale (e.g., `"en-GB"`) |
| `real_chrome` | bool | `False` | Use locally installed Chrome instead of Playwright Chromium |
| `cdp_url` | str | None | Connect to existing Chrome via CDP |
| `google_search` | bool | `True` | Set referer as if from Google search |
| `extra_headers` | dict | None | Additional HTTP headers |
| `proxy` | str/dict | None | Proxy (string or `{"server": ..., "username": ..., "password": ...}`) |
| `extra_flags` | list[str] | None | Additional Chromium flags |
| `selector_config` | dict | `{}` | Arguments for Selector class |
| `additional_args` | dict | None | Extra Playwright context args (highest priority) |
| `max_pages` | int | `1` | Page pool size for sessions |
| `retries` | int | `3` | Retry attempts |
| `retry_delay` | int/float | `1` | Seconds between retries |
| `proxy_rotator` | ProxyRotator | None | Automatic proxy rotation |

---

### 3.4 StealthyFetcher / StealthySession / AsyncStealthySession (Anti-detection)

**Import**: `from scrapling.fetchers import StealthyFetcher, StealthySession, AsyncStealthySession`

**What it does**: Built on Patchright (patched Playwright) + extensive stealth measures. Passes virtually all bot detection tests including Cloudflare Turnstile. This is the primary anti-bot fetcher.

**One-off usage**:
```python
# Sync
page = StealthyFetcher.fetch('https://nopecha.com/demo/cloudflare', headless=True, solve_cloudflare=True)

# Async
page = await StealthyFetcher.async_fetch('https://example.com/')
```

**Session usage**:
```python
# Sync
with StealthySession(headless=True, solve_cloudflare=True) as session:
    page = session.fetch('https://protected-site.com/')

# Async
async with AsyncStealthySession(max_pages=2) as session:
    tasks = [session.fetch(url) for url in urls]
    results = await asyncio.gather(*tasks)
    print(session.get_pool_stats())
```

**All DynamicFetcher parameters PLUS these stealth-specific ones**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `solve_cloudflare` | bool | `False` | **Automatically solve Cloudflare Turnstile/Interstitial challenges** |
| `hide_canvas` | bool | `False` | Add random noise to canvas operations to prevent fingerprinting |
| `block_webrtc` | bool | `False` | Force WebRTC to respect proxy (prevent local IP leak) |
| `allow_webgl` | bool | `True` | Keep WebGL enabled (disabling not recommended -- WAFs check for it) |
| `user_data_dir` | str | temp dir | Persistent user profile directory for session data |
| `timezone_id` | str | system | Override browser timezone |

---

## 4. Anti-Bot / Stealth Capabilities

### 4.1 Cloudflare Bypass
The `StealthyFetcher` includes a built-in Cloudflare solver (`_cloudflare_solver` method) that:
1. Detects challenge type: `non-interactive`, `interactive`, or `embedded`
2. For non-interactive: waits for the "Just a moment..." page to disappear
3. For interactive/embedded: locates the Turnstile iframe, calculates captcha coordinates, simulates mouse click with randomized delays
4. Recursively retries if challenge persists
5. Uses `solve_cloudflare=True` parameter to enable

### 4.2 Browser Fingerprint Stealth
StealthyFetcher uses Patchright (not standard Playwright) and applies:

**Chromium Stealth Args** (from `constants.py`):
- `--disable-blink-features=AutomationControlled` (removes `navigator.webdriver` flag)
- `--start-maximized` (bypasses headless viewport detection)
- `--enable-features=NetworkService,NetworkServiceInProcess,TrustTokens`
- `--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4` (simulates real input devices)
- 40+ additional flags for speed and stealth

**Harmful Args Filtered Out**:
- `--enable-automation`, `--disable-popup-blocking`, `--disable-component-update`, `--disable-default-apps`, `--disable-extensions`

**Resource Blocking** (when `disable_resources=True`):
- Blocks: `font`, `image`, `media`, `beacon`, `object`, `imageset`, `texttrack`, `websocket`, `csp_report`, `stylesheet`

### 4.3 HTTP-Level Stealth (Fetcher/FetcherSession)
- TLS fingerprint impersonation via `curl_cffi` (`impersonate` parameter)
- Can impersonate specific browser versions (e.g., `"chrome"`, `"firefox135"`)
- Can randomize browser selection from a list: `impersonate=["chrome", "firefox135", "safari"]`
- Auto-generates realistic browser headers when `stealthy_headers=True`
- Auto-sets Google search referer for each request domain
- HTTP/3 support via `http3=True`

### 4.4 Fingerprint Protection Features
- **Canvas noise**: `hide_canvas=True` adds random noise to canvas operations
- **WebRTC blocking**: `block_webrtc=True` prevents local IP leak
- **WebGL control**: `allow_webgl=True` (default) -- disabling detectable by WAFs
- **Google search referer**: `google_search=True` (default) -- spoofs referrer
- **Custom user agent**: `useragent` parameter or auto-generation of real browser UAs
- **Domain blocking**: `blocked_domains` to prevent tracking/analytics requests
- **User data persistence**: `user_data_dir` for maintaining browser profiles

---

## 5. Response / Selector -- Content Extraction

All fetchers return a `Response` object, which extends `Selector`. The `Selector` class is the core parsing engine.

### 5.1 Direct Parser Usage (without fetchers)
```python
from scrapling.parser import Selector

page = Selector("<html><body><h1>Hello</h1></body></html>")
# Works exactly like Response from fetchers
```

### 5.2 CSS Selectors
```python
page = Fetcher.get('https://example.com/')

# Basic CSS
elements = page.css('.product')           # Returns Selectors (list-like)
element = page.css('.product')[0]         # Single Selector

# Pseudo-elements (Scrapy/Parsel compatible)
texts = page.css('.quote .text::text')       # Extract text content
all_texts = page.css('.quote::text').getall() # Get all as list
first_text = page.css('.quote::text').get()   # Get first or None
hrefs = page.css('a::attr(href)').getall()    # Extract attributes

# Chained selectors
page.css('.quote').css('.text::text').getall()

# Adaptive scraping (survives website structure changes)
page = Selector(html, adaptive=True)
products = page.css('.product', auto_save=True)    # Save element signatures
# Later, when structure changes:
products = page.css('.product', adaptive=True)      # Relocates elements automatically
```

### 5.3 XPath Selectors
```python
elements = page.xpath('//div[@class="quote"]')
texts = page.xpath('//span[@class="text"]/text()').getall()
```

### 5.4 BeautifulSoup-Style Finders
```python
# find_all - returns Selectors
page.find_all('div', {'class': 'quote'})
page.find_all('div', class_='quote')
page.find_all(['div', 'span'], class_='quote')
page.find_all(class_='quote')  # any tag

# find - returns first match or None
page.find('div', class_='quote')

# With regex patterns
import re
page.find_all(re.compile(r'price-\d+'))

# With callable filters
page.find_all(lambda el: 'price' in el.text)
```

### 5.5 Text Search
```python
page.find_by_text('quote', tag='div')
```

### 5.6 Element Properties & Navigation
```python
el = page.css('.product')[0]

# Properties
el.tag                    # Tag name (str)
el.text                   # Direct text content (TextHandler)
el.get_all_text()         # All descendant text, concatenated
el.attrib                 # AttributesHandler (dict-like, read-only)
el.attrib['href']         # Get specific attribute
el.html_content           # Inner HTML
el.body                   # Raw body (str/bytes)
el.prettify()             # Pretty-printed HTML
el.has_class('active')    # Check for CSS class
el.url                    # Associated URL

# Navigation
el.parent                 # Parent element
el.children               # Direct children (Selectors)
el.siblings               # Sibling elements
el.next                   # Next sibling
el.previous               # Previous sibling
el.path                   # Path from root to element
el.below_elements         # All descendant elements
el.iterancestors()        # Generator over ancestors
el.find_ancestor(func)    # Find ancestor matching function

# Similarity
el.find_similar()         # Find elements similar to this one (Selectors method)

# Serialization
el.get()                  # Serialize to string (outer HTML or text)
el.getall()               # Single-element list of serialized string
el.json()                 # Parse as JSON (for JSON responses)
```

### 5.7 TextHandler (Enhanced String)
All text values are returned as `TextHandler` objects (extends `str`):
```python
text = el.text

# Regex operations
text.re(r'\d+')                          # Returns TextHandlers (list of matches)
text.re_first(r'\d+', default='0')       # First match or default
text.re(r'pattern', check_match=True)    # Returns bool
text.re(r'pattern', case_sensitive=False) # Case-insensitive
text.re(r'pattern', clean_match=True)    # Ignores whitespace

# Cleaning
text.clean()                             # Remove whitespace, consecutive spaces
text.clean(remove_entities=True)         # Also decode HTML entities

# JSON
text.json()                              # Parse as JSON dict

# All standard str methods return TextHandler
text.upper()
text.strip()
text.replace('old', 'new')
```

### 5.8 Selectors (List of Selector)
The `Selectors` class is a list-like container returned by `.css()`, `.xpath()`, `.find_all()`:
```python
elements = page.css('.product')

elements.get()        # First element's serialized string, or None
elements.getall()     # List of all serialized strings (TextHandlers)
elements.css(...)     # Chain CSS on all elements, flatten results
elements.xpath(...)   # Chain XPath on all elements

# Filtering
elements.filter(lambda el: 'price' in el.text)

# Iteration
for el in elements:
    print(el.text)
```

---

## 6. Proxy Rotation

```python
from scrapling.fetchers import ProxyRotator, FetcherSession, StealthySession

# Create rotator with proxy list
rotator = ProxyRotator([
    "http://user:pass@proxy1:8080",
    "http://user:pass@proxy2:8080",
    "http://user:pass@proxy3:8080",
])

# Use with any session type
with FetcherSession(proxy_rotator=rotator) as session:
    page = session.get('https://example.com/')  # auto-rotates proxies

with StealthySession(proxy_rotator=rotator, headless=True) as session:
    page = session.fetch('https://example.com/')

# Per-request proxy override (bypass rotator)
page = session.fetch('https://example.com/', proxy="http://specific:proxy@host:port")

# IMPORTANT: Cannot combine proxy_rotator with static proxy/proxies
# This raises ValueError:
# FetcherSession(proxy_rotator=rotator, proxy="http://...")  # ERROR
```

---

## 7. Spider Framework (Full Crawling)

```python
from scrapling.spiders import Spider, Request, Response

class MySpider(Spider):
    name = "my_spider"
    start_urls = ["https://example.com/"]
    concurrent_requests = 10  # Concurrency limit

    def configure_sessions(self, manager):
        # Multi-session support
        manager.add("fast", FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(headless=True), lazy=True)

    async def parse(self, response: Response):
        for item in response.css('.product'):
            yield {"title": item.css('h2::text').get()}

        next_page = response.css('.next a')
        if next_page:
            yield response.follow(next_page[0].attrib['href'])
            # Or route to specific session:
            yield Request(url, sid="stealth", callback=self.parse_protected)

# Run
result = MySpider().start()
result.items.to_json("output.json")
result.items.to_jsonl("output.jsonl")

# Pause/Resume
MySpider(crawldir="./crawl_data").start()  # Ctrl+C to pause, restart to resume

# Streaming
async for item in spider.stream():
    process(item)
```

---

## 8. CLI Commands

```bash
# Interactive shell
scrapling shell

# Extract content to file
scrapling extract get 'https://example.com' output.md
scrapling extract get 'https://example.com' output.txt --css-selector '#content' --impersonate 'chrome'
scrapling extract fetch 'https://example.com' output.md --css-selector '#content' --no-headless
scrapling extract stealthy-fetch 'https://nopecha.com/demo/cloudflare' out.html --css-selector '#padded_content a' --solve-cloudflare
```

Output formats: `.txt` (text only), `.md` (markdown), `.html` (raw HTML).

---

## 9. Key Implementation Details

### Response Factory
- HTTP responses: `ResponseFactory.from_http_request(response, selector_config, meta)`
- Browser responses: `ResponseFactory.from_playwright_response(page, first_response, final_response, selector_config, meta)`
- Responses carry `meta` dict with proxy info

### Adaptive Scraping Storage
- Uses SQLite by default (`elements_storage.db`)
- Stores element signatures (tag, text, attributes, path, parent info, siblings)
- Similarity scoring uses `SequenceMatcher` across multiple features
- Enable with `Selector(html, adaptive=True)` or `StealthyFetcher.adaptive = True`

### Session Page Pooling (Browser Sessions)
- `max_pages` controls concurrent tab count
- `get_pool_stats()` returns `{total_pages, busy_pages, max_pages}`
- Pages are managed via `PagePool` with busy/free/error tracking

### Error Handling
- All fetchers support `retries` and `retry_delay`
- Proxy errors are detected via `is_proxy_error()` for intelligent retry
- Browser sessions raise `RuntimeError` on context manager misuse
- Cloudflare solver retries recursively up to timeout

---

## 10. Quick-Start Recipes for Implementation

### Simple HTTP scrape with stealth headers
```python
from scrapling.fetchers import Fetcher
page = Fetcher.get('https://example.com/', stealthy_headers=True, impersonate='chrome')
titles = page.css('h1::text').getall()
```

### Cloudflare-protected site
```python
from scrapling.fetchers import StealthyFetcher
page = StealthyFetcher.fetch(
    'https://protected-site.com/',
    headless=True,
    solve_cloudflare=True,
    network_idle=True,
    hide_canvas=True,
    block_webrtc=True,
)
data = page.css('.content::text').getall()
```

### JavaScript-rendered page with custom action
```python
from scrapling.fetchers import DynamicFetcher

def click_load_more(page):
    page.click('button.load-more')
    page.wait_for_timeout(2000)

page = DynamicFetcher.fetch(
    'https://spa-site.com/',
    headless=True,
    page_action=click_load_more,
    network_idle=True,
    wait_selector='.results-loaded',
)
items = page.css('.item').getall()
```

### Persistent session with proxy rotation
```python
from scrapling.fetchers import FetcherSession, ProxyRotator

rotator = ProxyRotator(["http://p1:8080", "http://p2:8080"])
with FetcherSession(impersonate='chrome', proxy_rotator=rotator) as session:
    for url in urls:
        page = session.get(url)
        yield page.css('.data::text').get()
```

### Async concurrent scraping
```python
import asyncio
from scrapling.fetchers import AsyncStealthySession

async def scrape():
    async with AsyncStealthySession(headless=True, max_pages=5) as session:
        tasks = [session.fetch(url) for url in urls]
        results = await asyncio.gather(*tasks)
        for page in results:
            print(page.css('h1::text').get())

asyncio.run(scrape())
```
